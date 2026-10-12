"""Model entry points, the FPL-Auto reference estimator and batch forecasting."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from .data import POSITIONS
from .features import FEATURES, FEATURE_VERSION, FixtureContext, future_form


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
            # Resolve decimal half-way ties consistently despite sub-picounit
            # CSV parser noise. This changes the cache key only, not model inputs.
            normalized[column] = (normalized[column].astype("float64") + 1e-10).round(8)
    digest = hashlib.sha256(pd.util.hash_pandas_object(normalized, index=False).values.tobytes()).hexdigest()
    return hashlib.sha256((FEATURE_VERSION + digest).encode()).hexdigest()


def train(frame, directory, progress=lambda message: None, force=False, search="standard"):
    from .learning import train as train_ensemble
    return train_ensemble(frame, directory, progress, force, search)


def availability(player):
    value = player.get("chance_of_playing_next_round")
    if value is not None:
        return max(0.0, min(1.0, float(value) / 100))
    return 0.0 if player.get("status") in ("i", "s", "u", "n") or player.get("removed") else 1.0


def forecast(history, fixtures, bootstrap, manifest, bundle, horizon=5, discount=0.8, feedback=None):
    from collections import defaultdict
    from threadpoolctl import threadpool_limits
    from .feedback import point_correction
    feedback = feedback or {}
    season, gw = manifest["season"], manifest["next_gameweek"]
    weeks = list(range(gw, min(gw + horizon, max(int(e["id"]) for e in bootstrap["events"]) + 1)))
    form = future_form(history, bootstrap, season, gw)
    context = FixtureContext(fixtures)
    club_map = {int(t["id"]): t for t in bootstrap["teams"]}
    rows, tasks, totals = [], defaultdict(list), defaultdict(float)
    for player in bootstrap["elements"]:
        element = int(player["id"])
        position = POSITIONS[int(player["element_type"])]
        history_count = int(form.loc[element, "history_count"])
        row = {"id": element, "code": int(player.get("code", 0)), "name": player["web_name"],
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
            for game in games:
                features = form.loc[element, LAG_COLUMNS()].to_dict()
                features.update(context.values(player["team"], week, cutoff=gw, specific_fixture=game))
                features.update({"gameweek": week, "scoring_era": max(0, int(season[:4]) - 2024),
                                 "baseline_form": .65 * features["total_points_mean3"] + .35 * features["total_points_mean8"]})
                tasks[position].append((element, week, features))
            row[f"fixtures{week}"] = ", ".join(club_map[op]["short_name"] + (" (H)" if home else " (A)") for op, home in games) or "Blank"
            row[f"games{week}"] = len(games)
        rows.append(row)
    # Predict each position as a batch, rather than thousands of tiny tree calls.
    with threadpool_limits(limits=2):
        for position, group in tasks.items():
            X = pd.DataFrame([task[2] for task in group])
            values = (baseline(X, bundle["priors"][position]) if bundle["methods"][position] == "form_baseline"
                      else bundle["models"][position].predict(X).clip(0, 20))
            for (element, week, _), value in zip(group, values):
                totals[(element, week)] += float(value)
    for row in rows:
        for week in weeks:
            base = totals[(row["id"], week)] * row["availability"]
            correction = (point_correction(feedback, row["position"], row["minutes_recent"], row[f"games{week}"], row["availability"])
                          if week == gw else 0.0)
            row[f"base_gw{week}"] = round(base, 3)
            row[f"gw{week}"] = round(float(np.clip(base + correction, 0, 20 * row[f"games{week}"])), 3)
        row["next_points"] = row[f"gw{gw}"]
        row["horizon_points"] = round(sum(row[f"gw{w}"] for w in weeks), 3)
        row["weighted_points"] = round(sum(discount ** i * row[f"gw{w}"] for i, w in enumerate(weeks)), 3)
        row["risk"] = "Limited history" if row["history_count"] < 3 else ("Availability flag" if row["availability"] < 1 else "")
    return pd.DataFrame(rows).sort_values("next_points", ascending=False).reset_index(drop=True), weeks


def LAG_COLUMNS():
    from .features import LAG_FEATURES
    return LAG_FEATURES
