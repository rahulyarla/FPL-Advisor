"""One refresh command: data -> features -> validation/train -> forecasts -> report."""

import json
import threading
from pathlib import Path

import pandas as pd

from .data import sync, API
from .features import build_training_frame
from .http import Client, DataError, utc_now, write_json
from .model import train, forecast
from .selection import recommend
from .analytics import build_analytics

_LOCK = threading.Lock()


def load_config(root):
    config = json.loads((Path(root) / "config.json").read_text())
    if not 1 <= int(config["horizon"]) <= 8:
        raise DataError("Forecast horizon must be 1–8 gameweeks.")
    if not 0 < float(config["discount"]) <= 1:
        raise DataError("Discount must be in (0,1].")
    return config


def cached_inputs(root):
    data = Path(root) / "data"
    try:
        history = pd.read_csv(data / "history.csv.gz")
        fixtures = {s: pd.DataFrame(rows) for s, rows in json.loads((data / "fixtures.json").read_text()).items()}
        bootstrap = json.loads((data / "bootstrap.json").read_text())
        manifest = json.loads((data / "manifest.json").read_text())
        return history, fixtures, bootstrap, manifest
    except (FileNotFoundError, ValueError) as exc:
        raise DataError("No complete saved snapshot. Run an online refresh first.") from exc


def run(root, offline=False, cached=False, force=False, progress=lambda message: None):
    root = Path(root)
    if not _LOCK.acquire(blocking=False):
        raise DataError("A refresh is already running.")
    try:
        config = load_config(root)
        if cached:
            history, fixture_sets, bootstrap, manifest = cached_inputs(root)
            progress("Using the saved data snapshot")
        else:
            client = Client(root / "data/http", timeout=config.get("request_timeout", 40), offline=offline)
            history, fixture_sets, bootstrap, manifest = sync(client, config, progress)
        progress("Building features from information available before each gameweek")
        training = build_training_frame(history, fixture_sets)
        bundle, retrained = train(training, root / "models", progress, force=force)
        progress("Predicting upcoming fixtures and player points")
        predictions, weeks = forecast(history, fixture_sets[manifest["season"]], bootstrap,
                                      manifest, bundle, config["horizon"], config["discount"])
        manifest = {**manifest, "forecast_created_at": utc_now(), "forecast_weeks": weeks,
                    "model_retrained": retrained, "model_trained_at": bundle["report"]["created_at"],
                    "model_fingerprint": bundle["fingerprint"], "cached_run": bool(cached),
                    "offline_run": bool(offline)}
        progress("Saving forecasts and validation results")
        data = root / "data"
        reports = root / "reports"
        data.mkdir(parents=True, exist_ok=True)
        reports.mkdir(parents=True, exist_ok=True)
        # All work succeeded before saved data/reports are replaced.
        history.to_csv(data / "history.csv.gz.tmp", index=False, compression="gzip")
        (data / "history.csv.gz.tmp").replace(data / "history.csv.gz")
        write_json(data / "bootstrap.json", bootstrap)
        write_json(data / "fixtures.json", {s: json.loads(df.to_json(orient="records")) for s, df in fixture_sets.items()})
        predictions.to_csv(reports / "player_forecasts.csv.tmp", index=False)
        (reports / "player_forecasts.csv.tmp").replace(reports / "player_forecasts.csv")
        write_json(reports / "model_checks.json", bundle["report"])
        write_json(reports / "player_analytics.json", build_analytics(history, predictions, manifest))
        write_json(data / "manifest.json", manifest)
        write_json(reports / "snapshot.json", {"manifest": manifest, "validation": bundle["report"],
                                               "players": json.loads(predictions.to_json(orient="records"))})
        progress(f"Ready: {manifest['season']} GW{manifest['next_gameweek']}, {len(predictions)} players")
        return manifest
    finally:
        _LOCK.release()


def state(root):
    root = Path(root)
    report = root / "reports/snapshot.json"
    if not report.exists():
        return {"ready": False, "message": "Use Refresh data and model to get started."}
    return {"ready": True, **json.loads(report.read_text())}


def public_team(root, entry_id):
    from .rivals import parse_entry_id
    entry_id = parse_entry_id(entry_id)
    config = load_config(root)
    bootstrap = json.loads((Path(root) / "data/bootstrap.json").read_text())
    client = Client(Path(root) / "data/http", timeout=config.get("request_timeout", 40))
    profile = client.json(f"{API}entry/{int(entry_id)}/", cache=False)
    history = client.json(f"{API}entry/{int(entry_id)}/history/", cache=False)
    played = history.get("current", [])
    if not played:
        raise DataError("There is no public deadline squad for this entry yet. Enter the players manually.")
    event = max(int(x["event"]) for x in played)
    picks = client.json(f"{API}entry/{int(entry_id)}/event/{event}/picks/", cache=False)
    squad = [int(p["element"]) for p in picks.get("picks", [])]
    if len(squad) != 15:
        raise DataError("Public picks did not contain 15 players.")
    latest = picks.get("entry_history", played[-1])
    completed = {int(x) for x in state(root)["manifest"].get("completed_gameweeks", [])}
    finalized_history = [{"gameweek": int(h["event"]), "points": int(h.get("points", 0)),
                          "total_points": int(h.get("total_points", 0)),
                          "rank": h.get("overall_rank")}
                         for h in sorted(played, key=lambda h: int(h["event"])) if int(h["event"]) in completed]
    return {"entry_id": int(entry_id), "team_name": profile.get("name"), "source_gameweek": event,
            "squad": squad, "bank": float(latest.get("bank", 0)) / 10,
            "selling_prices": {}, "free_transfers": None,
            "season": state(root)["manifest"]["season"],
            "last_deadline_starters": [int(p["element"]) for p in picks.get("picks", []) if int(p.get("position", 16)) <= 11],
            "last_deadline_captain": next((int(p["element"]) for p in picks.get("picks", []) if p.get("is_captain")), None),
            "chips_used": history.get("chips", []), "active_chip_at_deadline": picks.get("active_chip"),
            "history": finalized_history,
            "total_points_through_completed": finalized_history[-1]["total_points"] if finalized_history else None,
            "imported_at": utc_now(),
            "note": f"Imported the GW{event} deadline snapshot. Check later transfers, bank, free transfers and every selling price."}


def selection(root, payload):
    current = state(root)
    if not current["ready"]:
        raise DataError("Refresh data and train the model first.")
    config = load_config(root)
    mode = payload.get("mode", "lineup")
    owned = payload.get("owned", [])
    if owned and mode != "lineup" and not payload.get("economics_confirmed"):
        raise DataError("Confirm your current bank, free transfers and actual selling prices first.")
    allowed = {"mode", "owned", "bank", "selling_prices", "budget", "free_transfers", "max_transfers", "locked", "excluded"}
    options = {k: payload[k] for k in allowed if k in payload}
    options.setdefault("mode", mode)
    options["discount"] = float(config["discount"])
    options["seconds"] = float(config.get("solver_seconds", 30))
    frame = pd.DataFrame(current["players"])
    weeks = current["manifest"]["forecast_weeks"]
    if "compare_rivals" in payload and not isinstance(payload["compare_rivals"], bool):
        raise DataError("The rival comparison switch must be on or off.")
    if payload.get("compare_rivals"):
        from .rivals import compare_recommendations, rivals_state
        entries = rivals_state(root)["rivals"]
        # An imported team may also have been added before the user entered
        # their own ID. Keep it out of their rival comparison automatically.
        if payload.get("entry_id"):
            from .rivals import parse_entry_id
            own_id = parse_entry_id(payload["entry_id"])
            entries = [r for r in entries if int(r["entry_id"]) != own_id]
        return compare_recommendations(frame, weeks, entries, options,
                                       style=payload.get("rival_style", "protect"),
                                       influence=payload.get("rival_influence", .08),
                                       max_points_loss=payload.get("max_points_loss", .01),
                                       samples=config.get("scenario_samples", 5000))
    return recommend(frame, weeks, **options)
