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
from .feedback import archive_snapshot, reconcile, evaluate_feedback

_LOCK = threading.Lock()
FIXTURE_FIELDS = ["id", "event", "team_h", "team_a", "finished", "finished_provisional",
                  "team_h_score", "team_a_score", "kickoff_time", "team_h_difficulty", "team_a_difficulty",
                  "team_h_xg", "team_a_xg"]


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
        history = pd.read_csv(data / "history.csv.gz", float_precision="round_trip")
        fixtures = {s: pd.DataFrame(rows) for s, rows in json.loads((data / "fixtures.json").read_text()).items()}
        bootstrap = json.loads((data / "bootstrap.json").read_text())
        manifest = json.loads((data / "manifest.json").read_text())
        return history, fixtures, bootstrap, manifest
    except (FileNotFoundError, ValueError) as exc:
        raise DataError("No complete saved snapshot. Run an online refresh first.") from exc


def run(root, offline=False, cached=False, force=False, progress=lambda message: None, skip_qualitative=False):
    root = Path(root)
    if not _LOCK.acquire(blocking=False):
        raise DataError("A refresh is already running.")
    try:
        config = load_config(root)
        # Preserve the last genuine online snapshot before replacing reports.
        old_report, old_bootstrap = root / "reports/snapshot.json", root / "data/bootstrap.json"
        if old_report.exists() and old_bootstrap.exists():
            try:
                archive_snapshot(root, json.loads(old_report.read_text()), json.loads(old_bootstrap.read_text()), "saved_online_snapshot")
            except (ValueError, KeyError, TypeError, OSError) as exc:
                progress(f"Previous snapshot could not be archived; continuing the fresh refresh: {exc}")
        if cached:
            history, fixture_sets, bootstrap, manifest = cached_inputs(root)
            progress("Using the saved data snapshot")
        else:
            client = Client(root / "data/http", timeout=config.get("request_timeout", 40), offline=offline)
            history, fixture_sets, bootstrap, manifest = sync(client, config, progress)
            if config.get("external_team_xg", True):
                from .external import enrich_fixture_sets
                fixture_sets = enrich_fixture_sets(client, fixture_sets, bootstrap, manifest, config, progress)
        progress("Building features from information available before each gameweek")
        current_history = history[history.season == manifest["season"]]
        if (not current_history.gw.isin(manifest["completed_gameweeks"]).all()
                or (current_history.gw >= int(manifest["next_gameweek"])).any()):
            raise DataError("Saved history and manifest disagree about finalized gameweeks. Refresh online before training.")
        training = build_training_frame(history, fixture_sets)
        bundle, retrained = train(training, root / "models", progress, force=force, search=config.get("model_search", "standard"))
        progress("Matching archived forecasts with finalized results and checking error corrections")
        matched, feedback_warnings = reconcile(root, history, manifest)
        feedback = evaluate_feedback(matched, feedback_warnings, config.get("feedback_min_gameweeks", 3))
        progress("Predicting upcoming fixtures and player points")
        predictions, weeks = forecast(history, fixture_sets[manifest["season"]], bootstrap,
                                      manifest, bundle, config["horizon"], config["discount"], feedback)
        manifest = {**manifest, "forecast_created_at": utc_now(), "forecast_weeks": weeks,
                    "model_retrained": retrained, "model_trained_at": bundle["report"]["created_at"],
                    "model_fingerprint": bundle["fingerprint"], "cached_run": bool(cached),
                    "model_version": bundle.get("model_version"), "model_trained_through": bundle.get("trained_through", int(training.order.max())),
                    "offline_run": bool(offline)}
        qualitative, candidate_players, learned_players = {}, [], []
        if config.get("qualitative_enabled", True) and not skip_qualitative:
            from .qualitative import build
            qualitative, candidate_players, learned_players = build(
                root, json.loads(predictions.to_json(orient="records")), manifest, history, config,
                online=not (cached or offline), progress=progress)
        progress("Saving forecasts and validation results")
        data = root / "data"
        reports = root / "reports"
        data.mkdir(parents=True, exist_ok=True)
        reports.mkdir(parents=True, exist_ok=True)
        # All work succeeded before saved data/reports are replaced.
        history.to_csv(data / "history.csv.gz.tmp", index=False, compression="gzip")
        (data / "history.csv.gz.tmp").replace(data / "history.csv.gz")
        write_json(data / "bootstrap.json", bootstrap)
        write_json(data / "fixtures.json", {s: json.loads(df[[c for c in FIXTURE_FIELDS if c in df]].to_json(orient="records", double_precision=15))
                                           for s, df in fixture_sets.items()})
        predictions.to_csv(reports / "player_forecasts.csv.tmp", index=False)
        (reports / "player_forecasts.csv.tmp").replace(reports / "player_forecasts.csv")
        write_json(reports / "model_checks.json", bundle["report"])
        analytics = build_analytics(history, predictions, manifest)
        for element, player in analytics.get("players", {}).items():
            player["recorded_predictions"] = feedback["player_predictions"].get(element, [])
        analytics["note"] += " Recorded predictions are real dated pre-deadline forecasts; legacy versions are labeled in the forecast-results download and do not tune version 3."
        write_json(reports / "player_analytics.json", analytics)
        write_json(reports / "prediction_feedback.json", feedback)
        if not matched.empty:
            matched.to_csv(reports / "forecast_results.csv.tmp", index=False)
            (reports / "forecast_results.csv.tmp").replace(reports / "forecast_results.csv")
        else:
            (reports / "forecast_results.csv").unlink(missing_ok=True)
        if (root / "models/backtest_predictions.csv.gz").exists():
            import shutil
            shutil.copyfile(root / "models/backtest_predictions.csv.gz", reports / "backtest_predictions.csv.gz")
        write_json(data / "manifest.json", manifest)
        snapshot = {"manifest": manifest, "validation": bundle["report"], "feedback": feedback,
                    "players": json.loads(predictions.to_json(orient="records")), "qualitative": qualitative,
                    "qualitative_candidate_players": candidate_players, "qualitative_learned_players": learned_players}
        archive_snapshot(root, snapshot, bootstrap)
        if qualitative:
            from .qualitative import archive_pair
            archive_pair(root, snapshot)
        write_json(reports / "snapshot.json", {k: v for k, v in snapshot.items() if k not in ("qualitative", "qualitative_candidate_players", "qualitative_learned_players")})
        progress(f"Ready: {manifest['season']} GW{manifest['next_gameweek']}, {len(predictions)} players")
        return manifest
    finally:
        _LOCK.release()


def state(root):
    root = Path(root)
    report = root / "reports/snapshot.json"
    if not report.exists():
        return {"ready": False, "message": "Use Refresh data and model to get started."}
    snapshot = json.loads(report.read_text())
    private_report = root / "reports/qualitative.json"
    if private_report.exists():
        q = json.loads(private_report.read_text())
        m = snapshot["manifest"]
        if q.get("season") == m["season"] and q.get("origin_gw") == m["next_gameweek"] and q.get("stats_created_at") == m["created_at"]:
            snapshot["qualitative"] = q
    if snapshot.get("qualitative") and not snapshot.get("qualitative_candidate_players"):
        from .qualitative import apply_evidence
        q = snapshot["qualitative"]
        candidates, learned = apply_evidence(snapshot["players"], snapshot["manifest"]["forecast_weeks"], q, q.get("discount", .8), q.get("learned_gain", 0))
        snapshot.update({"qualitative_candidate_players": candidates, "qualitative_learned_players": learned})
    return {"ready": True, **snapshot}


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
    if any(k in payload and not isinstance(payload[k], bool) for k in ("compare_qualitative", "validated_only")):
        raise DataError("Qualitative comparison switches must be on or off.")
    if payload.get("compare_qualitative") is True:
        options = {k: v for k, v in payload.items() if k != "compare_qualitative"}
        alternate_view = "learned" if payload.get("validated_only") is True else "qualitative"
        stats = selection(root, {**options, "forecast_view": "stats"})
        alternate = selection(root, {**options, "forecast_view": alternate_view})
        return {"stats_only": stats, "with_qualitative": alternate, "alternate_view": alternate_view,
                "evidence_as_of": current.get("qualitative", {}).get("as_of"),
                "note": current.get("qualitative", {}).get("experimental_note", ""),
                "learning": current.get("qualitative", {}).get("learning", {}),
                "player_changes": [{"id": int(k), **{field: p[field] for field in ("stats_points", "experimental_points", "experimental_delta", "learned_delta", "notes")}}
                                   for k, p in current.get("qualitative", {}).get("players", {}).items() if p["experimental_delta"] != 0]}
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
    forecast_view = payload.get("forecast_view", "stats")
    if forecast_view not in ("stats", "qualitative", "learned"):
        raise DataError("Forecast view must be stats, qualitative or learned.")
    variant = "qualitative_candidate_players" if forecast_view == "qualitative" else "qualitative_learned_players"
    if forecast_view != "stats" and not current.get(variant):
        raise DataError("Refresh qualitative evidence before using this forecast view.")
    if forecast_view != "stats":
        from .qual_sources import when
        if when(utc_now()) >= when(current["manifest"]["deadline"]):
            raise DataError("This gameweek deadline has passed. Refresh data before using qualitative forecasts.")
        if (when(utc_now()) - when(current["qualitative"]["as_of"])).total_seconds() > 86400:
            raise DataError("Qualitative evidence is more than 24 hours old. Refresh evidence first.")
    frame = pd.DataFrame(current["players"] if forecast_view == "stats" else current[variant])
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


def refresh_qualitative(root, online=True, progress=lambda message: None):
    """Refresh only text evidence, preserving quantitative model/data timestamps."""
    if not _LOCK.acquire(blocking=False):
        raise DataError("A refresh is already running. Try again after it finishes.")
    try:
        from .qualitative import build, archive_pair
        root = Path(root)
        snapshot = state(root)
        if not snapshot["ready"]:
            raise DataError("Refresh player data first.")
        history, _, _, _ = cached_inputs(root)
        report, candidates, learned = build(root, snapshot["players"], snapshot["manifest"], history, load_config(root), online, progress)
        snapshot.update({"qualitative": report, "qualitative_candidate_players": candidates, "qualitative_learned_players": learned})
        snapshot.pop("ready", None)
        archive_pair(root, snapshot)
        write_json(root / "reports/snapshot.json", {k: v for k, v in snapshot.items() if k not in ("qualitative", "qualitative_candidate_players", "qualitative_learned_players")})
        return report
    finally:
        _LOCK.release()
