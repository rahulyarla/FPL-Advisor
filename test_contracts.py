"""Decision-relevant regression tests: leakage, identity, money and legal squads."""

import json
import tempfile
import unittest
import io
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_advisor.data import STATS, aggregate_gameweeks, normalize_vaastav, season_from_bootstrap
from fpl_advisor.features import FEATURES, FixtureContext, build_training_frame, form_features
from fpl_advisor.http import DataError, write_json
from fpl_advisor.pipeline import selection
from fpl_advisor.model import data_fingerprint
from fpl_advisor.selection import optimize, recommend, validate_squad


def record(element, gw, points, minutes=90, season="2025-26", code=None, fixture_count=1):
    values = {s: 0.0 for s in STATS}
    values.update(total_points=points, minutes=minutes)
    return {"season": season, "element": element, "code": element + 100 if code is None else code,
            "gw": gw, "position": "MID", "name": str(element), "team_id": 1,
            "home_count": 1, "fixture_count": fixture_count,
            "defensive_stats_available": 1, **values}


def fixture(gw, h=1, a=2, hs=1, away_score=1, fixture_id=None):
    return {"id": fixture_id or gw, "event": gw, "team_h": h, "team_a": a,
            "team_h_score": hs, "team_a_score": away_score, "finished": True}


def player_pool():
    rows = []
    element = 1
    for club in range(1, 11):
        for position in ("GK", "DEF", "MID", "FWD"):
            rows.append({"id": element, "name": f"P{element}", "position": position,
                         "team_id": club, "club": str(club), "price": 5.0, "cost": 50,
                         "selectable": True, "availability": 1.0,
                         "gw6": float(12 - club), "gw7": float(11 - club)})
            element += 1
    return pd.DataFrame(rows)


class TemporalContracts(unittest.TestCase):
    def test_double_gameweek_sums_then_lags_entire_gameweek(self):
        raw = pd.DataFrame([record(1, 1, 5, 90), record(1, 1, 8, 90), record(1, 2, 4, 90)])
        history = aggregate_gameweeks(raw)
        panel, form = form_features(history)
        self.assertEqual(len(panel), 2)
        self.assertEqual(form.loc[0, "total_points_mean3"], 0)
        self.assertEqual(form.loc[1, "total_points_mean3"], 6.5)
        self.assertEqual(form.loc[1, "minutes_mean3"], 90)

    def test_current_gameweek_performance_cannot_change_its_features(self):
        source = pd.DataFrame([record(1, 1, 3), record(1, 2, 8), record(1, 3, 2)])
        fixtures = {"2025-26": pd.DataFrame([fixture(1), fixture(2), fixture(3)])}
        original = build_training_frame(aggregate_gameweeks(source), fixtures)
        changed = source.copy()
        changed.loc[changed.gw == 2, STATS] = 999
        revised = build_training_frame(aggregate_gameweeks(changed), fixtures)
        np.testing.assert_equal(original.loc[1, FEATURES].to_numpy(), revised.loc[1, FEATURES].to_numpy())
        self.assertNotEqual(original.loc[2, "points_last"], revised.loc[2, "points_last"])
        self.assertNotIn("xP", FEATURES)
        self.assertNotIn("target", FEATURES)
        self.assertNotIn("total_points", FEATURES)

    def test_reused_seasonal_ids_do_not_mix_players(self):
        records = [record(1, 38, 12, season="2024-25", code=100),
                   record(1, 1, 3, season="2025-26", code=200)]
        panel, form = form_features(aggregate_gameweeks(pd.DataFrame(records)))
        at_new_player = panel.index[panel.code == 200][0]
        self.assertEqual(form.loc[at_new_player, "history_count"], 0)
        self.assertEqual(form.loc[at_new_player, "points_last"], 0)

    def test_missing_stable_code_is_isolated_by_season(self):
        records = [record(1, 38, 12, season="2024-25", code=0),
                   record(1, 1, 3, season="2025-26", code=0)]
        panel, form = form_features(aggregate_gameweeks(pd.DataFrame(records)))
        self.assertTrue((form.history_count == 0).all())

    def test_stable_code_carries_known_prior_season_form(self):
        records = [record(1, 38, 12, season="2024-25", code=100),
                   record(9, 1, 3, season="2025-26", code=100)]
        panel, form = form_features(aggregate_gameweeks(pd.DataFrame(records)))
        self.assertEqual(form.loc[1, "points_last"], 12)

    def test_fixture_context_excludes_same_week_and_future_scores(self):
        base = pd.DataFrame([fixture(1, hs=2), fixture(2, hs=3), fixture(3, hs=4)])
        changed = base.copy()
        changed.loc[changed.event >= 2, "team_h_score"] = 900
        old = FixtureContext(base).values(1, 2)
        new = FixtureContext(changed).values(1, 2)
        self.assertEqual(old, new)

    def test_unfinished_fixture_score_is_excluded_from_context(self):
        partial = fixture(1, hs=900)
        partial["finished"] = False
        context = FixtureContext(pd.DataFrame([partial, fixture(2)]))
        self.assertAlmostEqual(context.values(1, 2)["own_goals_for"], 1.35)

    def test_duplicate_fixture_and_assistant_manager_rows_are_removed(self):
        players = pd.DataFrame([{"id": 1, "code": 100, "element_type": 3},
                                {"id": 2, "code": 200, "element_type": 5}])
        a = {"element": 1, "fixture": 1, "position": "MID", "total_points": 3,
             "minutes": 90, "was_home": True, "round": 1}
        b = {**a, "element": 2, "position": "AM", "total_points": 15}
        result = normalize_vaastav(pd.DataFrame([a, a, b]), players, pd.DataFrame([fixture(1)]), "2024-25")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0].total_points, 3)

    def test_season_is_detected_from_api_deadlines(self):
        bootstrap = {"events": [{"deadline_time": "2027-08-13T17:30:00Z"}], "elements": [{}], "teams": [{}]}
        self.assertEqual(season_from_bootstrap(bootstrap), "2027-28")

    def test_fingerprint_survives_csv_and_detects_actual_result_changes(self):
        source = pd.DataFrame([record(1, 1, 3), record(1, 2, 8), record(1, 3, 2)])
        source["expected_assists"] = [0.13, 0.21, 0.09]
        fixtures = {"2025-26": pd.DataFrame([fixture(1), fixture(2), fixture(3)])}
        frame = build_training_frame(aggregate_gameweeks(source), fixtures)
        reloaded = pd.read_csv(io.StringIO(frame.to_csv(index=False)))
        self.assertEqual(data_fingerprint(frame), data_fingerprint(reloaded))
        reloaded.loc[0, "target"] += 1
        self.assertNotEqual(data_fingerprint(frame), data_fingerprint(reloaded))


class DecisionContracts(unittest.TestCase):
    def test_budget_club_positions_and_formation(self):
        pool = player_pool()
        result = optimize(pool, [6, 7], budget=75, seconds=10)
        squad = validate_squad(pool, result["squad"])
        self.assertLessEqual(squad.cost.sum(), 750)
        self.assertLessEqual(squad.groupby("team_id").size().max(), 3)
        for plan in result["plans"]:
            self.assertEqual(len(plan["starters"]), 11)
            self.assertIn(plan["captain"], plan["starters"])
            self.assertIn(plan["vice_captain"], plan["starters"])
            self.assertNotEqual(plan["captain"], plan["vice_captain"])

    def test_captain_uses_next_gw_rather_than_horizon_total(self):
        pool = player_pool()
        pool.loc[pool.id == 4, ["gw6", "gw7"]] = [30, 0]
        pool.loc[pool.id == 8, ["gw6", "gw7"]] = [29, 50]
        result = optimize(pool, [6, 7], budget=80, seconds=10)
        self.assertEqual(result["plans"][0]["captain"], 4)
        self.assertEqual(result["plans"][1]["captain"], 8)

    def test_exact_selling_price_prevents_unaffordable_upgrade(self):
        pool = player_pool()
        owned = optimize(pool, [6], budget=75, seconds=10)["squad"]
        old = next(i for i in owned if pool.set_index("id").loc[i, "position"] == "MID")
        new = next(int(r.id) for r in pool.itertuples() if r.position == "MID" and r.id not in owned)
        pool.loc[pool.id == old, ["cost", "price"]] = [100, 10]
        pool.loc[pool.id == new, ["cost", "price", "gw6", "gw7"]] = [90, 9, 60, 60]
        prices = {str(i): 5.0 for i in owned}
        result = optimize(pool, [6], mode="transfers", owned=owned, bank=0, selling_prices=prices,
                          free_transfers=1, max_transfers=1, seconds=10)
        self.assertNotIn(new, result["bought"])
        result_with_real_cash = optimize(pool, [6], mode="transfers", owned=owned, bank=4,
                                         selling_prices=prices, free_transfers=1, max_transfers=1, seconds=10)
        self.assertIn(new, result_with_real_cash["bought"])
        self.assertGreaterEqual(result_with_real_cash["remaining_bank"], 0)

    def test_small_paid_transfer_loses_to_holding(self):
        pool = player_pool()
        owned = optimize(pool, [6], budget=75, seconds=10)["squad"]
        extra = next(int(r.id) for r in pool.itertuples() if r.id not in owned)
        pool.loc[pool.id == extra, "gw6"] = 11.1
        prices = {str(i): 5.0 for i in owned}
        result = recommend(pool, [6], mode="transfers", owned=owned, bank=0,
                           selling_prices=prices, free_transfers=0, max_transfers=1, seconds=10)
        self.assertEqual(result["transfer_count"], 0)
        self.assertEqual(result["hit_points"], 0)

    def test_impossible_locks_fail_clearly(self):
        with self.assertRaises(DataError):
            optimize(player_pool(), [6], locked=[1, 2, 3, 4], budget=100, seconds=5)

    def test_duplicate_squad_fails(self):
        with self.assertRaises(DataError):
            validate_squad(player_pool(), [1] * 15)

    def test_missing_or_excess_selling_prices_fail(self):
        pool = player_pool()
        owned = optimize(pool, [6], budget=75, seconds=10)["squad"]
        with self.assertRaises(DataError):
            optimize(pool, [6], mode="transfers", owned=owned, selling_prices={}, seconds=5)
        with self.assertRaises(DataError):
            optimize(pool, [6], mode="transfers", owned=owned,
                     selling_prices={str(i): 10 for i in owned}, seconds=5)

    def test_transfer_api_requires_confirmed_economics(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / "config.json", {"horizon": 5, "discount": .8})
            write_json(root / "reports/snapshot.json", {"players": [], "manifest": {"forecast_weeks": [6]}})
            with self.assertRaisesRegex(DataError, "Confirm"):
                selection(root, {"mode": "transfers", "owned": [1] * 15})


if __name__ == "__main__":
    unittest.main()
