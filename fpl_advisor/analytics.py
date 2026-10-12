"""Season-scoped player chart data from finalized observations, without a CDN."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .http import DataError


def build_analytics(history, predictions, manifest):
    completed = {int(x) for x in manifest.get("completed_gameweeks", [])}
    season = str(manifest["season"])
    past = history[(history.season.astype(str) == season) & history.gw.isin(completed)].copy()
    numeric = ["total_points", "minutes", "expected_goals", "expected_assists",
               "goals_scored", "assists", "clean_sheets", "saves", "bonus", "fixture_count"]
    for field in numeric:
        if field not in past:
            past[field] = np.nan
    # The production history is already aggregated by GW. Summing also supports
    # genuine fixture-level inputs, without losing a double gameweek's second match.
    if len(past):
        grouped = past.groupby(["element", "gw"])[numeric].sum(min_count=1)
    else:
        grouped = pd.DataFrame(columns=numeric)
    players = {}
    for p in predictions.to_dict("records"):
        element = int(p["id"])
        series = []
        if len(grouped) and element in grouped.index.get_level_values(0):
            for gw, row in grouped.loc[element].sort_index().iterrows():
                item = {"gameweek": int(gw)}
                for field in numeric:
                    value = row[field]
                    item[field] = round(float(value), 3) if pd.notna(value) else None
                item["xgi"] = (round(item["expected_goals"] + item["expected_assists"], 3)
                               if item["expected_goals"] is not None and item["expected_assists"] is not None else None)
                series.append(item)
        future = [{"gameweek": int(gw), "points": float(p[f"gw{gw}"]),
                   "fixtures": p.get(f"fixtures{gw}", ""), "games": int(p.get(f"games{gw}", 0))}
                  for gw in manifest["forecast_weeks"]]
        minutes = sum(x["minutes"] or 0 for x in series)
        xg = sum(x["expected_goals"] or 0 for x in series)
        xa = sum(x["expected_assists"] or 0 for x in series)
        players[str(element)] = {
            "id": element, "name": p["name"], "position": p["position"], "club": p["club"],
            "history": series, "forecast": future,
            "summary": {"points": round(sum(x["total_points"] or 0 for x in series), 1),
                        "minutes": round(minutes, 1), "xg": round(xg, 3), "xa": round(xa, 3),
                        "xgi_per90": round((xg + xa) * 90 / minutes, 3) if minutes else None,
                        "games_recorded": len(series)},
        }
    return {"season": season, "data_as_of": manifest["created_at"], "players": players,
            "note": "Actuals include finalized current-season gameweeks only. Missing observations are not filled with invented scores. Forecasts are separate model estimates."}


def player_analytics(root):
    root = Path(root)
    report = root / "reports/player_analytics.json"
    if report.exists():
        return json.loads(report.read_text())
    snapshot = root / "reports/snapshot.json"
    if not snapshot.exists():
        raise DataError("Refresh data first to load player charts.")
    state = json.loads(snapshot.read_text())
    history = pd.read_csv(root / "data/history.csv.gz")
    return build_analytics(history, pd.DataFrame(state["players"]), state["manifest"])
