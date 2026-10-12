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
LEGACY_FEATURES = LAG_FEATURES + ["home_fraction", "scheduled_games", "own_goals_for", "own_goals_against",
                                "opponent_goals_for", "opponent_goals_against", "gameweek", "scoring_era"]
LONG_STATS = ["total_points", "minutes", "goals_scored", "assists", "bonus", "saves",
              "expected_goals", "expected_assists", "defensive_contribution"]
LAG_FEATURES += [f"{s}_mean20" for s in LONG_STATS] + [
    "points_ewm", "minutes_ewm", "points_std8", "played_rate8", "sixty_rate8",
    "xg_per90", "xa_per90", "goals_per90", "assists_per90", "points_per90",
    "xg_observed_rate8", "defensive_observed_rate8", "weeks_since_record"]
CONTEXT_FEATURES = ["home_fraction", "scheduled_games", "own_goals_for", "own_goals_against",
                    "opponent_goals_for", "opponent_goals_against", "gameweek", "scoring_era",
                    "own_clean_sheet_rate", "opponent_clean_sheet_rate",
                    "own_home_goals", "opponent_away_goals", "own_xg", "own_xga", "opponent_xg",
                    "opponent_xga", "external_xg_available"]
FEATURES = LAG_FEATURES + CONTEXT_FEATURES
FEATURE_VERSION = "deadline-safe-gw-v3"


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
        if s in LONG_STATS:
            out[f"{s}_mean20"] = past.groupby(keys).rolling(20, min_periods=1).mean().reset_index(level=0, drop=True).sort_index().fillna(0)
        if s in ("minutes", "total_points"):
            out["minutes_ewm" if s == "minutes" else "points_ewm"] = past.groupby(keys).transform(
                lambda x: x.ewm(halflife=4, adjust=False, ignore_na=True).mean()).fillna(0)
        if s == "total_points":
            out["points_std8"] = past.groupby(keys).rolling(8, min_periods=2).std().reset_index(level=0, drop=True).sort_index().fillna(0)
    for name, values in {
        "played_rate8": (panel.minutes > 0).astype(float),
        "sixty_rate8": (panel.minutes / panel.fixture_count >= 60).astype(float),
        "xg_observed_rate8": panel.get("xg_stats_available", panel.season.str[:4].astype(int).ge(2022).astype(float)),
        "defensive_observed_rate8": panel.defensive_stats_available.astype(float),
    }.items():
        past = values.groupby(keys).shift(1)
        out[name] = past.groupby(keys).rolling(8, min_periods=1).mean().reset_index(level=0, drop=True).sort_index().fillna(0)
    for stat, name in [("expected_goals", "xg"), ("expected_assists", "xa"),
                       ("goals_scored", "goals"), ("assists", "assists"), ("total_points", "points")]:
        # Shrink short, noisy per-90 records towards zero, using only previous observations.
        out[f"{name}_per90"] = 90 * out[f"{stat}_mean8"] / (out.minutes_mean8 + 20)
    previous_order = panel.order.groupby(keys).shift(1)
    out["weeks_since_record"] = (panel.order - previous_order).clip(0, 100).fillna(100)
    out["history_count"] = panel.groupby("player_key").cumcount().clip(upper=50)
    out["minutes_trend"] = out.minutes_mean3 - out.minutes_mean8
    out["points_trend"] = out.total_points_mean3 - out.total_points_mean8
    return panel, out


class FixtureContext:
    def __init__(self, fixtures, max_gw=38):
        if "event" in fixtures and fixtures.event.notna().any():
            max_gw = max(max_gw, int(fixtures.event.max()))
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
                xh, xa = f.get("team_h_xg", np.nan), f.get("team_a_xg", np.nan)
                results[h].append((gw, float(f["team_h_score"]), float(f["team_a_score"]), 1, xh, xa))
                results[a].append((gw, float(f["team_a_score"]), float(f["team_h_score"]), 0, xa, xh))
        teams = {key[0] for key in self.by_team_gw}
        for team in teams:
            games = sorted(results[team], key=lambda x: x[0])
            for gw in range(1, max_gw + 2):
                previous = [g for g in games if g[0] < gw][-8:]
                # Small shrinkage towards the league scoring prior, fitted nowhere on future results.
                home_games = [g for g in previous if g[3]]
                away_games = [g for g in previous if not g[3]]
                xg_games = [g for g in previous if pd.notna(g[4]) and pd.notna(g[5])]
                self.form[(team, gw)] = ((sum(g[1] for g in previous) + 4.05) / (len(previous) + 3),
                                        (sum(g[2] for g in previous) + 4.05) / (len(previous) + 3),
                                        (sum(g[2] == 0 for g in previous) + 0.75) / (len(previous) + 3),
                                        (sum(g[1] for g in home_games) + 4.5) / (len(home_games) + 3),
                                        (sum(g[1] for g in away_games) + 3.6) / (len(away_games) + 3),
                                        (sum(float(g[4]) for g in xg_games) + 4.05) / (len(xg_games) + 3),
                                        (sum(float(g[5]) for g in xg_games) + 4.05) / (len(xg_games) + 3),
                                        min(1, len(xg_games) / 3))

    def values(self, team_id, gw, cutoff=None, specific_fixture=None):
        cutoff = gw if cutoff is None else cutoff
        games = self.by_team_gw.get((int(team_id), int(gw)), [])
        if specific_fixture is not None:
            selected_games = [specific_fixture]
        else:
            selected_games = games
        prior = (1.35, 1.35, .25, 1.5, 1.2, 1.35, 1.35, 0)
        own = self.form.get((int(team_id), int(cutoff)), prior)
        opposing = [self.form.get((int(op), int(cutoff)), prior) for op, _ in selected_games]
        mean = lambda i: np.mean([p[i] for p in opposing]) if opposing else prior[i]
        return {"home_fraction": np.mean([h for _, h in selected_games]) if selected_games else 0.5,
                "scheduled_games": len(games), "own_goals_for": own[0], "own_goals_against": own[1],
                "opponent_goals_for": mean(0), "opponent_goals_against": mean(1),
                "own_clean_sheet_rate": own[2], "opponent_clean_sheet_rate": mean(2),
                "own_home_goals": own[3], "opponent_away_goals": mean(4),
                "own_xg": own[5], "own_xga": own[6], "opponent_xg": mean(5),
                "opponent_xga": mean(6), "external_xg_available": min(own[7], mean(7))}


def build_training_frame(history, fixture_sets):
    panel, features = form_features(history)
    contexts = {s: FixtureContext(f) for s, f in fixture_sets.items()}
    rows = [contexts[r.season].values(r.team_id, r.gw) for r in panel.itertuples()]
    context = pd.DataFrame(rows, index=panel.index)
    context["gameweek"] = panel.gw
    context["scoring_era"] = [max(0, int(s[:4]) - 2024) for s in panel.season]
    result = pd.concat([panel[["season", "element", "code", "position", "order", "gw", "name", "fixture_count"]], features, context], axis=1)
    result["target"] = panel.total_points / panel.fixture_count
    result["played_target"] = (panel.minutes > 0).astype(int)
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
