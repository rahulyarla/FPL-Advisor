"""A real forecast ledger and conservative learning from finalized forecast errors.

Keep the latest online forecast before each origin-GW deadline. Repeated refreshes
do not create extra independent observations. Historical backtests never enter
this ledger. Results are matched by season, seasonal ID and stable player code.
"""

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .data import season_order
from .http import utc_now
from .learning import MODEL_VERSION, minute_bucket
from .model import metrics


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Archive timestamps need an explicit time zone.")
    return parsed.astimezone(timezone.utc)


def archive_snapshot(root, snapshot, bootstrap, source="live_refresh", allow_saved_cached=False):
    manifest = snapshot.get("manifest", {})
    if ((manifest.get("cached_run") or manifest.get("offline_run")) and not allow_saved_cached) or not manifest.get("live_refresh", False):
        return False
    created = manifest.get("forecast_created_at")
    if not created:
        return False  # A file without a real forecast timestamp is not prospective evidence.
    origin = int(manifest["next_gameweek"])
    deadlines = {int(e["id"]): e["deadline_time"] for e in bootstrap["events"]}
    if origin not in deadlines or timestamp(created) >= timestamp(deadlines[origin]):
        return False
    if timestamp(manifest["created_at"]) > timestamp(created):
        return False
    family = snapshot.get("validation", {}).get("model_version", "legacy-v2")
    safe_family = "".join(c if c.isalnum() or c in "-." else "_" for c in family)
    players = {int(p["id"]): p for p in bootstrap["elements"]}
    weeks = [int(w) for w in manifest.get("forecast_weeks", []) if int(w) in deadlines]
    records = []
    for row in snapshot.get("players", []):
        element = int(row["id"])
        player = players.get(element)
        if player is None:
            continue
        records.append({"id": element, "code": int(player.get("code", 0)), "name": row["name"],
                        "position": row["position"], "minutes_recent": float(row.get("minutes_recent", 0)),
                        "availability": float(row.get("availability", 1)),
                        "weeks": [{"gw": w, "points": float(row[f"gw{w}"]),
                                   "base_points": float(row.get(f"base_gw{w}", row[f"gw{w}"])),
                                   "games": int(row.get(f"games{w}", 1)), "deadline": deadlines[w]}
                                  for w in weeks]})
    if not records:
        return False
    ledger = {"schema": 1, "season": manifest["season"], "origin_gw": origin,
              "created_at": created, "data_as_of": manifest["created_at"],
              "trained_through": int(manifest.get("model_trained_through", season_order(manifest["season"], max(manifest.get("completed_gameweeks", [0]), default=0)))),
              "model_family": family, "model_fingerprint": manifest.get("model_fingerprint"),
              "source": source, "input_mode": "cached" if manifest.get("cached_run") else "offline" if manifest.get("offline_run") else "online", "players": records}
    path = Path(root) / "data/forecast_archive" / manifest["season"] / f"gw{origin:02d}-{safe_family}.json.gz"
    if path.exists():
        try:
            old = json.loads(gzip.decompress(path.read_bytes()))
            if timestamp(old["created_at"]) >= timestamp(created):
                return False
        except (ValueError, KeyError, TypeError, OSError):
            pass  # Replace an unreadable current-GW file with a valid pre-deadline snapshot.
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ledger, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(gzip.compress(payload, compresslevel=6, mtime=0))
    temporary.replace(path)
    return True


def reconcile(root, history, manifest):
    completed = set(map(int, manifest.get("completed_gameweeks", [])))
    actual = history[(history.season == manifest["season"]) & history.gw.isin(completed)].set_index(["element", "gw"])
    matches, warnings = {}, []
    for path in sorted((Path(root) / "data/forecast_archive" / manifest["season"]).glob("*.json.gz")):
        try:
            archive = json.loads(gzip.decompress(path.read_bytes()))
            if archive.get("schema") != 1 or archive.get("season") != manifest["season"]:
                continue
            created = timestamp(archive["created_at"])
            if timestamp(archive["data_as_of"]) > created:
                raise ValueError("data timestamp follows forecast timestamp")
            for player in archive["players"]:
                for prediction in player["weeks"]:
                    gw, element = int(prediction["gw"]), int(player["id"])
                    if gw not in completed or created >= timestamp(prediction["deadline"]):
                        continue
                    if int(archive["trained_through"]) >= season_order(archive["season"], gw):
                        continue
                    if (element, gw) not in actual.index:
                        continue  # Missing outcomes are not silently replaced with zero.
                    outcome = actual.loc[(element, gw)]
                    if int(player["code"]) <= 0 or int(outcome.code) != int(player["code"]):
                        continue
                    lead = gw - int(archive["origin_gw"]) + 1
                    if lead < 1:
                        continue
                    value, base = float(prediction["points"]), float(prediction["base_points"])
                    if not np.isfinite([value, base, float(outcome.total_points)]).all():
                        continue
                    key = (archive["model_family"], gw, element, lead)
                    row = {"season": archive["season"], "gw": gw, "element": element, "code": int(player["code"]),
                           "name": player["name"], "position": player["position"], "lead": lead,
                           "model_family": archive["model_family"], "created_at": archive["created_at"],
                           "source": archive.get("source", "unknown"), "input_mode": archive.get("input_mode", "unknown"),
                           "prediction": value, "base_prediction": base, "actual": float(outcome.total_points),
                           "games": int(prediction["games"]), "minutes_recent": float(player["minutes_recent"]),
                           "availability": float(player.get("availability", 1))}
                    if key not in matches or created > timestamp(matches[key]["created_at"]):
                        matches[key] = row
        except (ValueError, KeyError, TypeError, OSError) as exc:
            warnings.append(f"Skipped invalid forecast archive {path.name}: {exc}")
    return pd.DataFrame(matches.values()), warnings


def live_bias(rows):
    corrections = {}
    if rows.empty:
        return corrections
    valid = rows[(rows.games > 0) & (rows.availability > 0)].copy()
    valid["bucket"] = minute_bucket(valid.minutes_recent)
    latest = valid.gw.max() if len(valid) else 0
    weights = .85 ** (latest - valid.gw)
    error = (valid.actual - valid.base_prediction) / valid.games
    for (position, bucket), group in valid.groupby(["position", "bucket"]):
        idx = group.index
        # Hundreds of correlated player outcomes must not masquerade as hundreds of independent GWs.
        corrections[f"{position}:{bucket}"] = float(np.clip((error.loc[idx] * weights.loc[idx]).sum() /
                                                            (weights.loc[idx].sum() + 300), -.5, .5))
    return corrections


def correction_values(rows, corrections):
    return np.array([corrections.get(f"{position}:{bucket}", 0) for position, bucket in
                     zip(rows.position, minute_bucket(rows.minutes_recent))]) * rows.games.to_numpy() * rows.availability.to_numpy()


def objective(rows, values):
    all_score = metrics(rows.actual, values)
    regular = rows.minutes_recent.to_numpy() >= 45
    active = metrics(rows.actual[regular], np.asarray(values)[regular]) if regular.any() else all_score
    return .75 * active["rmse"] + .25 * all_score["rmse"], all_score["mae"]


def evaluate_feedback(rows, warnings=None, minimum_gameweeks=3):
    report = {"created_at": utc_now(), "matched_forecasts": len(rows), "warnings": warnings or [],
              "model_family": MODEL_VERSION, "correction_gain": 0.0, "corrections": {}, "positions": {},
              "gameweeks": [], "player_predictions": {}, "evaluated_gameweeks": [],
              "status": "Waiting for finalized outcomes of forecasts saved before deadlines.",
              "policy": "Latest pre-deadline snapshot per origin GW. Match finalized actuals by season, ID and code. Learn only from one-GW forecasts of this model family; validate corrections on later GWs."}
    if rows.empty:
        return report
    # Plot the nearest recorded horizon per player/GW, while keeping families visible in each observation.
    nearest = rows.sort_values(["lead", "created_at"], ascending=[True, False]).drop_duplicates(["element", "gw"])
    for element, group in nearest.groupby("element"):
        report["player_predictions"][str(int(element))] = [{"gameweek": int(r.gw), "predicted": float(r.prediction),
                                                            "actual": float(r.actual), "lead": int(r.lead),
                                                            "model_family": r.model_family, "created_at": r.created_at}
                                                           for r in group.sort_values("gw").itertuples()]
    for (family, gw, lead), group in rows.groupby(["model_family", "gw", "lead"]):
        result = metrics(group.actual, group.prediction)
        report["gameweeks"].append({"model_family": family, "gameweek": int(gw), "lead": int(lead),
                                   **result, "mean_error": float((group.actual - group.prediction).mean())})
    learning = rows[(rows.model_family == MODEL_VERSION) & (rows.lead == 1)].copy()
    weeks = sorted(map(int, learning.gw.unique()))
    report["evaluated_gameweeks"] = weeks
    for position, group in learning.groupby("position"):
        report["positions"][position] = metrics(group.actual, group.prediction)
    if len(weeks) < max(3, int(minimum_gameweeks)):
        report["status"] = f"Matched {len(rows):,} forecasts. Live correction needs at least {max(3, int(minimum_gameweeks))} finalized GWs from version 3; currently {len(weeks)}. Training still learns from every finalized result."
        return report
    learning = learning[learning.gw.isin(weeks[-8:])]
    validation, adjustment = [], []
    for gw in weeks[-8:][2:]:
        past = learning[learning.gw < gw]
        later = learning[learning.gw == gw]
        validation.append(later)
        adjustment.extend(correction_values(later, live_bias(past)))
    if not validation:
        return report
    valid = pd.concat(validation, ignore_index=True)
    adjustment = np.array(adjustment)
    base_score, base_mae = objective(valid, valid.base_prediction.to_numpy())
    choices = []
    for gain in (0, .25, .5, 1):
        values = np.maximum(0, valid.base_prediction.to_numpy() + gain * adjustment)
        value, mae = objective(valid, values)
        choices.append({"gain": gain, "objective": value, "mae": mae})
    winner = min(choices, key=lambda x: (x["objective"], x["gain"]))
    # Require measurable gain without increasing MAE; otherwise keep the base forecast.
    gain = winner["gain"] if winner["objective"] <= .995 * base_score and winner["mae"] <= base_mae else 0
    report.update({"correction_gain": gain, "corrections": live_bias(learning) if gain else {},
                   "correction_validation": {"gameweeks": sorted(map(int, valid.gw.unique())), "rows": len(valid), "candidates": choices,
                                             "base_objective": base_score, "base_mae": base_mae},
                   "status": f"Learning from {len(weeks)} finalized version 3 GWs. " + ("A small bias correction passed chronological validation." if gain else "The base forecast won the live correction check.")})
    return report


def point_correction(feedback, position, minutes, games, availability):
    bucket = minute_bucket([minutes])[0]
    return float(feedback.get("correction_gain", 0) * feedback.get("corrections", {}).get(f"{position}:{bucket}", 0) * games * availability)
