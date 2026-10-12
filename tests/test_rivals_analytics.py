"""Contracts for optional rivals, shared outcomes, season identity and chart data."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from fpl_advisor.analytics import build_analytics
from fpl_advisor.http import DataError, write_json
from fpl_advisor.pipeline import selection, public_team
from fpl_advisor.rivals import (compare_recommendations, prepare_rivals, project_rival,
                                parse_entry_id, scenario_comparison, import_rival,
                                rivals_state, edit_rival, refresh_rivals)
from fpl_advisor.selection import optimize, validate_squad
from test_contracts import player_pool, record


class RivalContracts(unittest.TestCase):
    def setUp(self):
        self.frame = player_pool()
        self.frame["gw6"] = 10 + (40 - self.frame.id) * .01
        self.frame["gw7"] = 9 + (40 - self.frame.id) * .01
        self.frame["error_scale"] = 2.0
        self.standard = optimize(self.frame, [6, 7], budget=80, seconds=5)
        self.rival = {"entry_id": 101, "team_name": "Rival", "source_gameweek": 5,
                      "squad": self.standard["squad"], "enabled": True}
        self.options = {"mode": "wildcard", "budget": 80, "seconds": 5}

    def test_ids_and_official_links(self):
        self.assertEqual(parse_entry_id(" 123 "), 123)
        self.assertEqual(parse_entry_id("https://fantasy.premierleague.com/entry/123/event/5/"), 123)
        for bad in ["0", "-1", "1.5", "https://elsewhere.test/entry/123/", "abc"]:
            with self.subTest(bad=bad), self.assertRaises(DataError):
                parse_entry_id(bad)

    def test_zero_influence_returns_identical_standard_plan(self):
        result = compare_recommendations(self.frame, [6, 7], [self.rival], self.options,
                                         influence=0, samples=200)
        self.assertEqual(result["standard"], result["rival_aware"])

    def test_rival_influence_changes_close_choices_and_preserves_forecasts(self):
        before = self.frame.copy(deep=True)
        result = compare_recommendations(self.frame, [6, 7], [self.rival], self.options,
                                         style="chase", influence=.15, max_points_loss=.02, samples=200)
        c = result["comparison"]
        self.assertFalse(c["same_squad"] and c["same_next_captain"])
        pd.testing.assert_frame_equal(before, self.frame)

    def test_forecast_floor_per_week_and_after_hits(self):
        prices = {str(i): 5 for i in self.standard["squad"]}
        options = {"mode": "transfers", "owned": self.standard["squad"], "bank": 0,
                   "selling_prices": prices, "max_transfers": 2, "free_transfers": 0, "seconds": 5}
        for style in ["protect", "chase"]:
            result = compare_recommendations(self.frame, [6, 7], [self.rival], options,
                                             style=style, influence=.15, max_points_loss=.01, samples=200)
            base, aware = result["standard"], result["rival_aware"]
            self.assertGreaterEqual(aware["discounted_expected_points_after_hits"],
                                    .99 * base["discounted_expected_points_after_hits"] - .01)
            for k, plan in enumerate(aware["plans"]):
                actual = plan["expected_points_with_captain"] - (aware["hit_points"] if k == 0 else 0)
                baseline = base["plans"][k]["expected_points_with_captain"] - (base["hit_points"] if k == 0 else 0)
                self.assertGreaterEqual(actual, .99 * baseline - .01)
            self.assertLessEqual(aware["transfer_count"], 2)
            self.assertGreaterEqual(aware["remaining_bank"], 0)
            validate_squad(self.frame, aware["squad"])

    def test_many_rivals_do_not_amplify_influence(self):
        one, exposure_one = prepare_rivals(self.frame, [6, 7], [self.rival])
        many = [{**self.rival, "entry_id": i} for i in range(1, 76)]
        all_rivals, exposure_many = prepare_rivals(self.frame, [6, 7], many)
        self.assertEqual(len(all_rivals), 75)
        self.assertEqual(exposure_one, exposure_many)

    def test_duplicate_and_disabled_entries(self):
        rival = {**self.rival, "enabled": False, "entry_id": 102, "squad": [999]}
        prepared, _ = prepare_rivals(self.frame, [6], [self.rival, self.rival, rival])
        self.assertEqual(len(prepared), 1)
        with self.assertRaisesRegex(DataError, "at least one"):
            prepare_rivals(self.frame, [6], [rival])

    def test_projected_rival_has_legal_xi_and_next_gw_captain(self):
        projected = project_rival(self.frame, [6, 7], self.rival)
        for plan in projected["plans"]:
            self.assertEqual(len(set(plan["starters"])), 11)
            best = max(plan["starters"], key=lambda i: self.frame.set_index("id").loc[i, f"gw{plan['gameweek']}"])
            self.assertEqual(plan["captain"], best)

    def test_shared_players_cancel_in_paired_scenarios(self):
        projected, _ = prepare_rivals(self.frame, [6], [self.rival])
        plan = {"plans": projected[0]["plans"], "hit_points": 0}
        result = scenario_comparison(self.frame, [6], plan, plan, projected, samples=200)
        row = result["rivals"][0]["standard"]
        self.assertEqual(row["relative_score_sd"], 0)
        self.assertEqual(row["ahead_scenario_pct"], 0)
        self.assertEqual(row["tied_scenario_pct"], 100)

    def test_blank_week_has_no_random_points(self):
        self.frame["gw6"] = 0
        self.frame["games6"] = 0
        projected, _ = prepare_rivals(self.frame, [6], [self.rival])
        plan = {"plans": projected[0]["plans"], "hit_points": 0}
        result = scenario_comparison(self.frame, [6], plan, plan, projected, samples=200)
        self.assertEqual(result["weighted_horizon_scenarios"]["standard"]["p90"], 0)

    def test_overly_strong_rival_settings_are_rejected(self):
        for kwargs in [{"influence": .16}, {"max_points_loss": .03}, {"style": "anything"}, {"influence": float('nan')}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(DataError):
                compare_recommendations(self.frame, [6], [self.rival], self.options, samples=200, **kwargs)


class StoreContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.frame = player_pool()
        self.ids = optimize(self.frame, [6], budget=80, seconds=5)["squad"]
        write_json(self.root / "config.json", {"horizon": 2, "discount": .8, "solver_seconds": 5, "scenario_samples": 200})
        write_json(self.root / "reports/snapshot.json", {"players": self.frame.to_dict("records"),
                   "manifest": {"season": "2026-27", "forecast_weeks": [6, 7], "completed_gameweeks": [1, 2, 3, 4, 5]}})
        self.info = {"entry_id": 101, "team_name": "Rival", "season": "2026-27", "squad": self.ids,
                     "source_gameweek": 5, "history": [], "chips_used": []}

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_add_replace_disable_and_remove(self):
        with patch("fpl_advisor.pipeline.public_team", return_value=self.info):
            import_rival(self.root, 101)
            import_rival(self.root, 101)
            self.assertEqual(len(rivals_state(self.root)["rivals"]), 1)
            edit_rival(self.root, 101, enabled=False)
            import_rival(self.root, 101)
            self.assertFalse(rivals_state(self.root)["rivals"][0]["enabled"])
            edit_rival(self.root, 101, remove=True)
            self.assertEqual(rivals_state(self.root)["rivals"], [])

    def test_own_team_cannot_be_imported_as_rival(self):
        with self.assertRaisesRegex(DataError, "own entry"):
            import_rival(self.root, 101, own_entry_id=101)

    def test_removed_rival_is_not_readded_by_refresh(self):
        with patch("fpl_advisor.pipeline.public_team", return_value=self.info):
            self.assertIsNone(import_rival(self.root, 101, replace_only=True))
            self.assertEqual(rivals_state(self.root)["rivals"], [])

    def test_seasons_do_not_mix_rival_ids(self):
        with patch("fpl_advisor.pipeline.public_team", return_value=self.info):
            import_rival(self.root, 101)
        snapshot = json.loads((self.root / "reports/snapshot.json").read_text())
        snapshot["manifest"]["season"] = "2027-28"
        write_json(self.root / "reports/snapshot.json", snapshot)
        self.assertEqual(rivals_state(self.root)["rivals"], [])

    def test_failed_refresh_preserves_and_labels_snapshot(self):
        with patch("fpl_advisor.pipeline.public_team", return_value=self.info):
            import_rival(self.root, 101)
        with patch("fpl_advisor.pipeline.public_team", side_effect=DataError("Offline")):
            result = refresh_rivals(self.root)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["rivals"][0]["squad"], self.ids)
        self.assertEqual(result["rivals"][0]["refresh_error"], "Offline")

    def test_standard_api_ignores_all_rival_settings_when_off(self):
        (self.root / "user").mkdir()
        (self.root / "user/rivals.json").write_text("corrupted")
        normal = selection(self.root, {"mode": "wildcard", "budget": 80})
        off = selection(self.root, {"mode": "wildcard", "budget": 80, "compare_rivals": False,
                                   "rival_style": "invalid", "rival_influence": 999})
        self.assertEqual(normal["squad"], off["squad"])
        self.assertEqual(normal["plans"], off["plans"])

    def test_compare_api_filters_own_id(self):
        with patch("fpl_advisor.pipeline.public_team", return_value=self.info):
            import_rival(self.root, 101)
        with self.assertRaisesRegex(DataError, "at least one"):
            selection(self.root, {"mode": "wildcard", "budget": 80, "entry_id": 101, "compare_rivals": True})

    def test_public_import_separates_active_chip_and_finalized_history(self):
        write_json(self.root / "data/bootstrap.json", {})
        profile = {"name": "QA team"}
        history = {"current": [{"event": 5, "points": 50, "total_points": 250},
                                {"event": 6, "points": 12, "total_points": 262}],
                   "chips": [{"name": "3xc", "event": 6}]}
        picks = {"picks": [{"element": i, "position": k+1, "is_captain": k==0} for k, i in enumerate(self.ids)],
                 "active_chip": "3xc", "entry_history": {"bank": 4}}
        with patch("fpl_advisor.pipeline.Client.json", side_effect=[profile, history, picks]):
            result = public_team(self.root, 101)
        self.assertEqual(result["source_gameweek"], 6)
        self.assertEqual(result["total_points_through_completed"], 250)
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["active_chip_at_deadline"], "3xc")


class ChartContracts(unittest.TestCase):
    def test_current_season_only_and_finalized_gameweeks_only(self):
        history = pd.DataFrame([record(1, 1, 99, season="2025-26"),
                                record(1, 1, 4, season="2026-27"),
                                record(1, 2, 100, season="2026-27")])
        frame = player_pool().iloc[:1]
        manifest = {"season": "2026-27", "created_at": "2026-10-12", "completed_gameweeks": [1], "forecast_weeks": [6]}
        report = build_analytics(history, frame, manifest)
        self.assertEqual(report["players"]["1"]["summary"]["points"], 4)
        self.assertEqual(report["players"]["1"]["forecast"][0]["points"], 11)

    def test_double_gameweek_graphs_sum_points_and_minutes(self):
        history = pd.DataFrame([record(1, 1, 4, season="2026-27"), record(1, 1, 8, season="2026-27")])
        manifest = {"season": "2026-27", "created_at": "2026-10-12", "completed_gameweeks": [1], "forecast_weeks": [6]}
        report = build_analytics(history, player_pool().iloc[:1], manifest)
        series = report["players"]["1"]["history"]
        self.assertEqual(series[0]["total_points"], 12)
        self.assertEqual(series[0]["minutes"], 180)

    def test_missing_player_history_remains_missing(self):
        history = pd.DataFrame([record(999, 1, 4, season="2026-27")])
        manifest = {"season": "2026-27", "created_at": "2026-10-12", "completed_gameweeks": [1], "forecast_weeks": [6]}
        report = build_analytics(history, player_pool().iloc[:1], manifest)
        player = report["players"]["1"]
        self.assertEqual(player["history"], [])
        self.assertIsNone(player["summary"]["xgi_per90"])
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
