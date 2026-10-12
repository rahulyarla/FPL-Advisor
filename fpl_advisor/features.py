"""Deadline-safe features. A player has exactly one row per gameweek.

All performance columns are shifted by one gameweek before rolling, including
double gameweeks. Fixture context uses team results strictly before that GW.
"""

from collections import defaultdict

import numpy as np
import pandas as pd

from .data import STATS, POSITIONS, season_order, bool_value
from .http import DataError

FORM_STATS = ["total_points", "minutes", "goals_scored", "assists", "clean_sheets",
              "goals_conceded", "saves", "bonus", "bps", "influence", "creativity",
              "threat", "expected_goals", "expected_assists", "expected_goals_conceded",
              "starts", "yellow_cards", "red_cards", "defensive_contribution"]
LAG_FEATURES = [f"{s}_mean{w}" for s in FORM_STATS for w in (3, 8)] + [
    "minutes_last", "points_last", "history_count", "minutes_trend", "points_trend"]
CONTEXT_FEATURES = ["home_fraction", "scheduled_games", "own_goals_for", "own_goals_against",
                    "opponent_goals_for", "opponent_goals_against", "gameweek", "scoring_era"]
FEATURES = LAG_FEATURES + CONTEXT_FEATURES
FEATURE_VERSION = "deadline-safe-gw-v1"


def form_features(history):
    panel = history.sort_values(["player_key", "order"]).reset_index(drop=True).copy()
    if panel.duplicated(["player_key", "order"]).any():
        raise DataError("A player has multiple rows in the same gameweek. Aggregate before feature generation.")
    keys = panel.player_key
    out = pd.DataFrame(index=panel.index)
    for s in FORM_STATS:
        per_game = panel[s].astype(float) / panel.fixture_count.clip(lower=1)
        past = per_game.groupby(keys).shift(1)
        for window in (3, 8):
            average = past.groupby(keys).rolling(window, min_periods=1).mean()
            out[f"{s}_mean{window}"] = average.reset_index(level=0, drop=True).sort_index().fillna(0)
        if s in ("minutes", "total_points"):
            out["minutes_last" if s == "minutes" else "points_last"] = past.fillna(0)
    out["history_count"] = panel.groupby("player_key").cumcount().clip(upper=50)
    out["minutes_trend"] = out.minutes_mean3 - out.minutes_mean8
    out["points_trend"] = out.total_points_mean3 - out.total_points_mean8
    return panel, out


class FixtureContext:
    def __init__(self, fixtures, max_gw=38):
        self.by_team_gw = defaultdict(list)
        self.form = {}
        results = defaultdict(list)
        for f in fixtures.to_dict("records"):
            if pd.isna(f.get("event")):
                continue
            gw = int(f["event"])
            h, a = int(f["team_h"]), int(f["team_a"])
            self.by_team_gw[(h, gw)].append((a, 1))
            self.by_team_gw[(a, gw)].append((h, 0))
            if (bool_value(f.get("finished", True)) and pd.notna(f.get("team_h_score"))
                    and pd.notna(f.get("team_a_score"))):
                results[h].append((gw, float(f["team_h_score"]), float(f["team_a_score"])))
                results[a].append((gw, float(f["team_a_score"]), float(f["team_h_score"])))
        teams = {key[0] for key in self.by_team_gw}
        for team in teams:
            games = sorted(results[team], key=lambda x: x[0])
            for gw in range(1, max_gw + 2):
                previous = [g for g in games if g[0] < gw][-8:]
                # Small shrinkage towards the league scoring prior, fitted nowhere on future results.
                self.form[(team, gw)] = ((sum(g[1] for g in previous) + 4.05) / (len(previous) + 3),
                                        (sum(g[2] for g in previous) + 4.05) / (len(previous) + 3))

    def values(self, team_id, gw, cutoff=None, specific_fixture=None):
        cutoff = gw if cutoff is None else cutoff
        games = self.by_team_gw.get((int(team_id), int(gw)), [])
        if specific_fixture is not None:
            selected_games = [specific_fixture]
        else:
            selected_games = games
        own = self.form.get((int(team_id), int(cutoff)), (1.35, 1.35))
        opposing = [self.form.get((int(op), int(cutoff)), (1.35, 1.35)) for op, _ in selected_games]
        return {"home_fraction": np.mean([h for _, h in selected_games]) if selected_games else 0.5,
                "scheduled_games": len(games), "own_goals_for": own[0], "own_goals_against": own[1],
                "opponent_goals_for": np.mean([p[0] for p in opposing]) if opposing else 1.35,
                "opponent_goals_against": np.mean([p[1] for p in opposing]) if opposing else 1.35}


def build_training_frame(history, fixture_sets):
    panel, features = form_features(history)
    contexts = {s: FixtureContext(f) for s, f in fixture_sets.items()}
    rows = [contexts[r.season].values(r.team_id, r.gw) for r in panel.itertuples()]
    context = pd.DataFrame(rows, index=panel.index)
    context["gameweek"] = panel.gw
    context["scoring_era"] = [max(0, int(s[:4]) - 2024) for s in panel.season]
    result = pd.concat([panel[["season", "element", "position", "order", "gw", "name"]], features, context], axis=1)
    result["target"] = panel.total_points / panel.fixture_count
    result["baseline_form"] = features.total_points_mean3 * 0.65 + features.total_points_mean8 * 0.35
    if not np.isfinite(result[FEATURES].to_numpy()).all():
        raise DataError("Non-finite model features.")
    return result


def future_form(history, bootstrap, season, next_gw):
    order = season_order(season, next_gw)
    past = history[history.order < order].copy()
    dummy = []
    for p in bootstrap["elements"]:
        key = f"code:{int(p['code'])}" if int(p.get("code", 0)) > 0 else f"{season}:{p['id']}"
        dummy.append({"player_key": key, "order": order, "element": int(p["id"]), "code": int(p["code"]),
                      "season": season, "gw": next_gw, "fixture_count": 1,
                      "position": POSITIONS[int(p["element_type"])], **{s: 0.0 for s in STATS}})
    combined = pd.concat([past, pd.DataFrame(dummy)], ignore_index=True)
    panel, features = form_features(combined)
    features["element"] = panel.element
    return features[panel.order == order].set_index("element")
