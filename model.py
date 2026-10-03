"""One gradient-boosted regression model per position, with an honest baseline.

Same core estimator and tree count as FPL-Auto. Model choice uses two forward
validation blocks; the third block is kept separate and reports the selected
method's test error. No random splitting of player rows.
"""

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from .data import POSITIONS, season_order
from .features import FEATURES, FEATURE_VERSION, FixtureContext, future_form
from .http import DataError, utc_now


def estimator(position):
    return GradientBoostingRegressor(n_estimators=110, learning_rate=0.1, max_depth=3,
                                     max_features={"GK": 5, "DEF": 10, "MID": 20, "FWD": 10}[position],
                                     min_samples_leaf=15, random_state=42, loss="squared_error")


def baseline(frame, prior):
    return np.where(frame.history_count.to_numpy() > 0,
                    frame.baseline_form.to_numpy(), prior).clip(0, 20)


def metrics(y, predictions):
    if len(y) == 0:
        return None
    return {"rows": len(y), "rmse": float(np.sqrt(mean_squared_error(y, predictions))),
            "mae": float(mean_absolute_error(y, predictions))}


def recency_weights(frame):
    year = frame.season.str[:4].astype(int)
    return 0.85 ** (year.max() - year)


def data_fingerprint(frame):
    normalized = frame[["position", "order", "target"] + FEATURES].copy()
    # CSV round trips can change dtype or the last binary floating-point bit.
    # Treat equivalent numeric representations as the same dataset, while
    # retaining much finer precision than any upstream statistic provides.
    for column in normalized.columns:
        if column != "position":
            normalized[column] = normalized[column].astype("float64").round(8)
    digest = hashlib.sha256(pd.util.hash_pandas_object(normalized, index=False).values.tobytes()).hexdigest()
    return hashlib.sha256((FEATURE_VERSION + digest).encode()).hexdigest()


def train(frame, directory, progress=lambda message: None, force=False):
    directory = Path(directory)
    path = directory / "model.joblib"
    fingerprint = data_fingerprint(frame)
    if path.exists() and not force:
        try:
            saved = joblib.load(path)
        except Exception:
            saved = {}
        if (saved.get("fingerprint") == fingerprint and saved.get("feature_version") == FEATURE_VERSION
                and saved.get("sklearn_version") == sklearn.__version__):
            progress("Training data unchanged; keeping the validated model")
            return saved, False
    complete = frame.groupby("season").gw.max()
    validation_season = max((s for s, end in complete.items() if end >= 38), default=None)
    if validation_season is None:
        raise DataError("A complete season through GW38 is required for forward validation.")
    # Latest complete season: two development blocks, followed by an untouched test block.
    blocks = [(21, 26), (27, 32), (33, 38)]
    report = {"created_at": utc_now(), "validation_season": validation_season,
              "validation_policy": "Train strictly before each block; select on GW21-32; test on GW33-38.",
              "regular_player_definition": "Pre-game rolling three-fixture average minutes >=45.",
              "target": "Points per scheduled fixture (GW totals are divided by fixture count).",
              "feature_version": FEATURE_VERSION, "rows": len(frame), "positions": {},
              "limitations": ["Historical fixture assignments are final schedules, not archived deadline snapshots.",
                              "Rule changes affect comparability of older seasons; scoring era is a feature.",
                              "Forecasts are estimates, and later weeks reuse the latest observed player form."]}
    models, methods, priors = {}, {}, {}
    for position in POSITIONS.values():
        part = frame[frame.position == position].copy()
        if len(part) < 100:
            raise DataError(f"Too little data to fit {position}.")
        scores, dev_ml, dev_base, dev_y = [], [], [], []
        test_result = None
        for i, (start, stop) in enumerate(blocks):
            progress(f"Validating {position}: {validation_season} GW{start}–{stop}")
            past = part[part.order < season_order(validation_season, start)]
            holdout = part[(part.season == validation_season) & part.gw.between(start, stop)]
            if len(past) < 100 or holdout.empty:
                raise DataError(f"Insufficient forward validation data for {position}.")
            m = estimator(position).fit(past[FEATURES], past.target, sample_weight=recency_weights(past))
            prediction = m.predict(holdout[FEATURES]).clip(0, 20)
            prior = float(past.target.mean())
            reference = baseline(holdout, prior)
            regular = holdout.minutes_mean3.to_numpy() >= 45
            scores.append({"start_gw": start, "end_gw": stop,
                           "role": "development" if i < 2 else "test",
                           "all_players": {"gradient_boosting": metrics(holdout.target, prediction),
                                           "form_baseline": metrics(holdout.target, reference)},
                           "regular_players": {"gradient_boosting": metrics(holdout.target[regular], prediction[regular]),
                                               "form_baseline": metrics(holdout.target[regular], reference[regular])}})
            if i < 2:
                mask = regular if regular.sum() >= 50 else np.ones(len(holdout), dtype=bool)
                dev_ml.extend(prediction[mask])
                dev_base.extend(reference[mask])
                dev_y.extend(holdout.target.to_numpy()[mask])
            else:
                test_result = (holdout, prediction, reference, regular)
        ml_error = metrics(dev_y, dev_ml)
        base_error = metrics(dev_y, dev_base)
        method = "gradient_boosting" if ml_error["rmse"] < base_error["rmse"] else "form_baseline"
        holdout, prediction, reference, regular = test_result
        selected = prediction if method == "gradient_boosting" else reference
        test_metrics = metrics(holdout.target, selected)
        test_active = metrics(holdout.target[regular], selected[regular])
        report["positions"][position] = {"selected_method": method,
                                            "development_rmse_ml": ml_error["rmse"],
                                            "development_rmse_baseline": base_error["rmse"],
                                            "test_all_players": test_metrics,
                                            "test_regular_players": test_active,
                                            "blocks": scores,
                                            "training_rows": len(part)}
        progress(f"Fitting final {position} model on all completed gameweeks")
        models[position] = estimator(position).fit(part[FEATURES], part.target, sample_weight=recency_weights(part))
        methods[position] = method
        priors[position] = float(part.target.mean())
        importances = models[position].feature_importances_
        report["positions"][position]["top_features"] = sorted(
            [{"feature": n, "importance": float(v)} for n, v in zip(FEATURES, importances)],
            key=lambda x: x["importance"], reverse=True)[:10]
    bundle = {"feature_version": FEATURE_VERSION, "features": FEATURES, "fingerprint": fingerprint,
              "models": models, "methods": methods, "priors": priors, "report": report,
              "sklearn_version": sklearn.__version__}
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / "model.joblib.tmp"
    joblib.dump(bundle, temp)
    temp.replace(path)
    return bundle, True


def availability(player):
    value = player.get("chance_of_playing_next_round")
    if value is not None:
        return max(0.0, min(1.0, float(value) / 100))
    return 0.0 if player.get("status") in ("i", "s", "u", "n") or player.get("removed") else 1.0


def forecast(history, fixtures, bootstrap, manifest, bundle, horizon=5, discount=0.8):
    season, gw = manifest["season"], manifest["next_gameweek"]
    weeks = list(range(gw, min(gw + horizon, max(int(e["id"]) for e in bootstrap["events"]) + 1)))
    form = future_form(history, bootstrap, season, gw)
    context = FixtureContext(fixtures)
    club_map = {int(t["id"]): t for t in bootstrap["teams"]}
    rows = []
    for player in bootstrap["elements"]:
        element = int(player["id"])
        position = POSITIONS[int(player["element_type"])]
        history_count = int(form.loc[element, "history_count"])
        row = {"id": element, "name": player["web_name"],
               "full_name": f"{player['first_name']} {player['second_name']}",
               "position": position, "team_id": int(player["team"]),
               "club": club_map[int(player["team"])]["short_name"],
               "price": int(player["now_cost"]) / 10, "cost": int(player["now_cost"]),
               "ownership": float(player.get("selected_by_percent", 0) or 0),
               "availability": availability(player), "status": player.get("status", "a"),
               "news": player.get("news", ""), "history_count": history_count,
               "minutes_recent": float(form.loc[element, "minutes_mean3"]),
               "method": bundle["methods"][position],
               "price_change_week": int(player.get("cost_change_event", 0) or 0) / 10,
               "net_transfers_week": int(player.get("transfers_in_event", 0) or 0) - int(player.get("transfers_out_event", 0) or 0),
               "selectable": bool(player.get("can_select", not player.get("removed", False))),
               "error_scale": bundle["report"]["positions"][position]["test_regular_players"]["rmse"]}
        for week in weeks:
            games = context.by_team_gw.get((int(player["team"]), week), [])
            frames = []
            for game in games:
                features = form.loc[element, LAG_COLUMNS()].to_dict()
                features.update(context.values(player["team"], week, cutoff=gw, specific_fixture=game))
                features.update({"gameweek": week, "scoring_era": max(0, int(season[:4]) - 2024)})
                frames.append(features)
            if frames:
                X = pd.DataFrame(frames)[FEATURES]
                if bundle["methods"][position] == "gradient_boosting":
                    points = bundle["models"][position].predict(X).clip(0, 20).sum()
                else:
                    value = 0.65 * form.loc[element, "total_points_mean3"] + 0.35 * form.loc[element, "total_points_mean8"]
                    points = (value if history_count else bundle["priors"][position]) * len(games)
                points *= row["availability"]
            else:
                points = 0.0
            row[f"gw{week}"] = round(float(points), 3)
            row[f"fixtures{week}"] = ", ".join(club_map[op]["short_name"] + (" (H)" if home else " (A)") for op, home in games) or "Blank"
            row[f"games{week}"] = len(games)
        row["next_points"] = row[f"gw{gw}"]
        row["horizon_points"] = round(sum(row[f"gw{w}"] for w in weeks), 3)
        row["weighted_points"] = round(sum(discount ** i * row[f"gw{w}"] for i, w in enumerate(weeks)), 3)
        row["risk"] = "Limited history" if history_count < 3 else ("Availability flag" if row["availability"] < 1 else "")
        rows.append(row)
    return pd.DataFrame(rows).sort_values("next_points", ascending=False).reset_index(drop=True), weeks


def LAG_COLUMNS():
    from .features import LAG_FEATURES
    return LAG_FEATURES
