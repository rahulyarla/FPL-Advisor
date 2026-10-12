"""Temporal and identity contracts for model development and prospective learning."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from fpl_advisor.data import aggregate_gameweeks, csv_from_bytes, season_order
from fpl_advisor.external import attach_team_xg
from fpl_advisor.features import FEATURES, FixtureContext, build_training_frame
from fpl_advisor.feedback import archive_snapshot, reconcile, evaluate_feedback, live_bias, point_correction
from fpl_advisor.learning import MODEL_VERSION, split_plan, train
from fpl_advisor.model import forecast
from fpl_advisor.model import data_fingerprint
from fpl_advisor.pipeline import run
from fpl_advisor.http import DataError, write_json
from test_contracts import record, fixture


class FakeCandidate:
    """Cheap estimator substitute to audit the actual training orchestration."""
    root = None
    test_season = "2025-26"
    test_calls = 0

    def __init__(self, name, position):
        self.name, self.position = name, position

    def fit(self, frame):
        self.last_order = int(frame.order.max())
        self.mean = float(frame.target.mean())
        return self

    def predict(self, frame):
        if self.last_order >= int(frame.order.min()):
            raise AssertionError("Estimator was trained on its evaluation gameweek.")
        if frame.season.iloc[0] == self.test_season:
            path = self.root / f"frozen_recipe_{self.position}.json"
            if not path.exists():
                raise AssertionError("Test prediction was computed before the recipe was frozen.")
            FakeCandidate.test_calls += 1
        offset = {"form_baseline": .8, "legacy_v2": .3, "hist_recent": 0, "hist_smooth": .1}[self.name]
        return np.full(len(frame), self.mean + offset)


class ConstantModel:
    def predict(self, frame):
        return np.full(len(frame), 4.)


def snapshot(gw=2, points=4, family=MODEL_VERSION, created="2026-08-13T10:00:00+00:00"):
    return {"manifest": {"season": "2026-27", "next_gameweek": gw, "completed_gameweeks": list(range(1, gw)),
                          "forecast_weeks": [gw], "created_at": "2026-08-13T09:00:00+00:00",
                          "forecast_created_at": created, "live_refresh": True, "model_fingerprint": "test"},
            "validation": {"model_version": family},
            "players": [{"id": 1, "name": "Player", "position": "MID", "minutes_recent": 90,
                         "availability": 1, f"gw{gw}": points, f"base_gw{gw}": points, f"games{gw}": 1}]}


def bootstrap():
    return {"events": [{"id": 2, "deadline_time": "2026-08-15T10:00:00Z"},
                        {"id": 3, "deadline_time": "2026-08-22T10:00:00Z"}],
            "elements": [{"id": 1, "code": 101}]}


class FeedbackContracts(unittest.TestCase):
    def test_latest_pre_deadline_snapshot_counts_once(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(archive_snapshot(d, snapshot(), bootstrap()))
            self.assertTrue(archive_snapshot(d, snapshot(points=5, created="2026-08-14T10:00:00Z"), bootstrap()))
            self.assertFalse(archive_snapshot(d, snapshot(points=99, created="2026-08-15T10:00:01Z"), bootstrap()))
            h = aggregate_gameweeks(pd.DataFrame([record(1, 2, 8, season="2026-27")]))
            rows, warnings = reconcile(d, h, {"season": "2026-27", "completed_gameweeks": [2]})
            self.assertFalse(warnings)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows.iloc[0].prediction, 5)
            self.assertEqual(rows.iloc[0].actual, 8)

    def test_provisional_or_missing_results_are_not_zero_filled(self):
        with tempfile.TemporaryDirectory() as d:
            archive_snapshot(d, snapshot(), bootstrap())
            h = aggregate_gameweeks(pd.DataFrame([record(1, 1, 8, season="2026-27")]))
            self.assertTrue(reconcile(d, h, {"season": "2026-27", "completed_gameweeks": [1]})[0].empty)
            self.assertTrue(reconcile(d, h, {"season": "2026-27", "completed_gameweeks": [2]})[0].empty)

    def test_changed_stable_code_cannot_match_reused_id(self):
        with tempfile.TemporaryDirectory() as d:
            archive_snapshot(d, snapshot(), bootstrap())
            h = aggregate_gameweeks(pd.DataFrame([record(1, 2, 8, season="2026-27", code=999)]))
            self.assertTrue(reconcile(d, h, {"season": "2026-27", "completed_gameweeks": [2]})[0].empty)

    def test_cached_offline_and_untimestamped_forecasts_are_not_live_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            for key in ("cached_run", "offline_run"):
                s = snapshot(); s["manifest"][key] = True
                self.assertFalse(archive_snapshot(d, s, bootstrap()))
            s = snapshot(); del s["manifest"]["forecast_created_at"]
            self.assertFalse(archive_snapshot(d, s, bootstrap()))

    def test_same_gw_training_is_rejected_even_if_timestamp_looks_valid(self):
        with tempfile.TemporaryDirectory() as d:
            archive_snapshot(d, snapshot(), bootstrap())
            path = next(Path(d).rglob("*.gz")); obj = json.loads(gzip.decompress(path.read_bytes()))
            obj["trained_through"] = season_order("2026-27", 2)
            path.write_bytes(gzip.compress(json.dumps(obj).encode()))
            h = aggregate_gameweeks(pd.DataFrame([record(1, 2, 8, season="2026-27")]))
            self.assertTrue(reconcile(d, h, {"season": "2026-27", "completed_gameweeks": [2]})[0].empty)

    def test_double_gameweek_actual_is_the_whole_gw_total(self):
        with tempfile.TemporaryDirectory() as d:
            s = snapshot(points=10); s["players"][0]["games2"] = 2
            archive_snapshot(d, s, bootstrap())
            h = aggregate_gameweeks(pd.DataFrame([record(1, 2, 6, season="2026-27"), record(1, 2, 7, season="2026-27")]))
            rows, _ = reconcile(d, h, {"season": "2026-27", "completed_gameweeks": [2]})
            self.assertEqual(rows.iloc[0].actual, 13)
            self.assertEqual(rows.iloc[0].games, 2)

    def test_invalid_archive_warns_without_creating_observations(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data/forecast_archive/2026-27/bad.json.gz"; path.parent.mkdir(parents=True); path.write_bytes(b"bad")
            h = aggregate_gameweeks(pd.DataFrame([record(1, 2, 8, season="2026-27")]))
            rows, warnings = reconcile(d, h, {"season": "2026-27", "completed_gameweeks": [2]})
            self.assertTrue(rows.empty); self.assertEqual(len(warnings), 1)

    def learning_rows(self, family=MODEL_VERSION, weeks=4, residual=1):
        return pd.DataFrame([{"gw": gw, "element": player, "position": "MID", "model_family": family,
                              "prediction": 4., "base_prediction": 4., "actual": 4.+residual,
                              "minutes_recent": 90., "games": 1, "availability": 1., "lead": 1,
                              "created_at": f"2026-08-{gw:02d}T10:00:00Z"}
                             for gw in range(1, weeks+1) for player in range(120)])

    def test_feedback_requires_distinct_gws_and_same_model_family(self):
        for rows in (self.learning_rows(weeks=2), self.learning_rows(family="legacy-v2")):
            result = evaluate_feedback(rows)
            self.assertEqual(result["correction_gain"], 0)
            self.assertFalse(result["corrections"])

    def test_predictable_bias_can_pass_later_gw_validation_and_is_bounded(self):
        rows = self.learning_rows()
        result = evaluate_feedback(rows)
        self.assertGreater(result["correction_gain"], 0)
        self.assertEqual(result["correction_validation"]["gameweeks"], [3, 4])
        self.assertLessEqual(abs(point_correction(result, "MID", 90, 1, 1)), .5)
        self.assertEqual(point_correction(result, "MID", 90, 0, 1), 0)
        self.assertEqual(point_correction(result, "MID", 90, 1, 0), 0)

    def test_no_improvement_keeps_uncorrected_forecast(self):
        result = evaluate_feedback(self.learning_rows(residual=0))
        self.assertEqual(result["correction_gain"], 0)

    def test_later_results_cannot_change_earlier_bias(self):
        rows = self.learning_rows()
        original = live_bias(rows[rows.gw < 3])
        rows.loc[rows.gw >= 3, "actual"] = 1000
        self.assertEqual(original, live_bias(rows[rows.gw < 3]))


class ExpandedModelContracts(unittest.TestCase):
    def model_frame(self):
        rows = []
        for year in (2023, 2024, 2025):
            season = f"{year}-{str(year+1)[-2:]}"
            for pos in ("GK", "DEF", "MID", "FWD"):
                for gw in range(1, 39):
                    for element in range(8):
                        rows.append({**{f: 0. for f in FEATURES}, "season": season, "gw": gw,
                                     "element": element, "code": element+100, "position": pos,
                                     "order": season_order(season, gw), "target": 4.+element%2,
                                     "minutes_mean3": 90., "fixture_count": 1, "played_target": 1,
                                     "baseline_form": 4.})
        return pd.DataFrame(rows)

    def test_full_season_test_and_prior_development_do_not_overlap(self):
        blocks = split_plan(self.model_frame())
        dev = {(s, gw) for s, a, b, role in blocks if role == "development" for gw in range(a, b+1)}
        test = {(s, gw) for s, a, b, role in blocks if role == "test" for gw in range(a, b+1)}
        self.assertFalse(dev & test)
        self.assertEqual({g for s, g in test}, set(range(1, 39)))
        self.assertTrue(all(season_order(s, g) < min(season_order(t, w) for t, w in test) for s, g in dev))

    def test_actual_search_freezes_recipe_before_test_and_never_trains_on_holdout(self):
        with tempfile.TemporaryDirectory() as d:
            FakeCandidate.root, FakeCandidate.test_calls = Path(d), 0
            with patch("fpl_advisor.learning.Candidate", FakeCandidate):
                bundle, retrained = train(self.model_frame(), d, search="quick")
            self.assertTrue(retrained)
            self.assertGreater(FakeCandidate.test_calls, 0)
            self.assertEqual(bundle["report"]["test_rows"], 38*8*4)
            self.assertEqual(bundle["report"]["development_rows"], (19+38)*8*4)
            audit = pd.read_csv(Path(d) / "backtest_predictions.csv.gz")
            self.assertTrue((audit.train_before <= audit.season.str[:4].astype(int)*100+audit.gw).all())

    def test_recipe_selection_does_not_depend_on_test_targets(self):
        frame = self.model_frame()
        recipes = []
        for altered in (False, True):
            with tempfile.TemporaryDirectory() as d:
                FakeCandidate.root = Path(d)
                x = frame.copy()
                if altered:
                    x.loc[x.season == "2025-26", "target"] += 30
                with patch("fpl_advisor.learning.Candidate", FakeCandidate):
                    bundle, _ = train(x, d, search="quick")
                recipes.append({p:r["recipe"] for p,r in bundle["report"]["positions"].items()})
        self.assertEqual(recipes[0], recipes[1])

    def test_long_form_and_team_xg_cannot_see_current_gw(self):
        raw = pd.DataFrame([record(1, gw, gw) for gw in range(1, 9)])
        fixtures = pd.DataFrame([{**fixture(gw), "team_h_xg": gw/10, "team_a_xg": .8} for gw in range(1, 9)])
        x = build_training_frame(aggregate_gameweeks(raw), {"2025-26": fixtures})
        changed = raw.copy(); changed.loc[changed.gw >= 5, "expected_goals"] = 1000
        revised = fixtures.copy(); revised.loc[revised.event >= 5, "team_h_xg"] = 1000
        y = build_training_frame(aggregate_gameweeks(changed), {"2025-26": revised})
        np.testing.assert_equal(x.loc[4, FEATURES].to_numpy(), y.loc[4, FEATURES].to_numpy())
        self.assertNotEqual(x.loc[5, "expected_goals_mean20"], y.loc[5, "expected_goals_mean20"])

    def test_pandemic_gameweek_numbering_retains_context_after_gw38(self):
        context = FixtureContext(pd.DataFrame([fixture(39, hs=5), fixture(40)]))
        self.assertGreater(context.values(1, 40)["own_goals_for"], 1.35)

    def test_old_windows_encoded_player_names_are_supported(self):
        self.assertEqual(csv_from_bytes("id,name\n1,André\n".encode("cp1252")).iloc[0]["name"], "André")

    def test_cache_identity_ignores_parser_noise_at_decimal_halfway_ties(self):
        a=self.model_frame();b=a.copy()
        a.loc[0,"expected_assists_mean8"]=.015640625-1e-16
        b.loc[0,"expected_assists_mean8"]=.015640625+1e-16
        self.assertEqual(data_fingerprint(a),data_fingerprint(b))
        b.loc[0,"target"]+=1
        self.assertNotEqual(data_fingerprint(a),data_fingerprint(b))

    def test_live_correction_is_not_extrapolated_to_untested_longer_horizons(self):
        history = aggregate_gameweeks(pd.DataFrame([record(1,g,4,season="2026-27") for g in (1,2,3)]))
        boot = {"events":[{"id":4},{"id":5}],"teams":[{"id":1,"short_name":"AAA"},{"id":2,"short_name":"BBB"}],
                "elements":[{"id":1,"code":101,"web_name":"P","first_name":"Player","second_name":"One",
                             "element_type":3,"team":1,"now_cost":50}]}
        bundle = {"methods":{"MID":"adaptive_ensemble"},"models":{"MID":ConstantModel()},"priors":{"MID":4},
                  "report":{"positions":{"MID":{"test_regular_players":{"rmse":3}}}}}
        values,_ = forecast(history,pd.DataFrame([fixture(4),fixture(5)]),boot,{"season":"2026-27","next_gameweek":4},bundle,
                            horizon=2,feedback={"correction_gain":1,"corrections":{"MID:regular":.5}})
        self.assertEqual(values.iloc[0].gw4,4.5); self.assertEqual(values.iloc[0].gw5,4.)

    def test_inconsistent_saved_history_is_rejected_before_training(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);history=aggregate_gameweeks(pd.DataFrame([record(1,3,4,season="2026-27")]))
            (root/'data').mkdir();history.to_csv(root/'data/history.csv.gz',index=False,compression='gzip')
            write_json(root/'config.json',{"horizon":1,"discount":.8})
            write_json(root/'data/fixtures.json',{"2026-27":[]})
            write_json(root/'data/bootstrap.json',{})
            write_json(root/'data/manifest.json',{"season":"2026-27","next_gameweek":2,"completed_gameweeks":[1]})
            with self.assertRaisesRegex(DataError,"disagree"):
                run(root,cached=True)
            self.assertFalse((root/'models/model.joblib').exists())


class ExternalSourceContracts(unittest.TestCase):
    def payload(self):
        return {"teams": {"1":{"title":"Manchester City","history":[{"date":"2025-08-01 15:00:00","h_a":"h","scored":2,"missed":1,"xG":2.2,"xGA":.8}]},
                          "2":{"title":"Tottenham","history":[{"date":"2025-08-01 15:00:00","h_a":"a","scored":1,"missed":2,"xG":.8,"xGA":2.2}]}}}

    def test_external_xg_requires_exact_date_venue_scores_and_reciprocal_pair(self):
        fixtures = pd.DataFrame([{**fixture(1, hs=2, away_score=1), "kickoff_time":"2025-08-01T15:00:00Z"}])
        teams = [{"id":1,"name":"Man City"},{"id":2,"name":"Spurs"}]
        enhanced, count = attach_team_xg(fixtures, teams, self.payload())
        self.assertEqual(count, 1); self.assertEqual(enhanced.iloc[0].team_h_xg, 2.2)
        fixtures["team_h_score"] = 9
        self.assertEqual(attach_team_xg(fixtures, teams, self.payload())[1], 0)
        payload = self.payload(); payload["teams"]["2"]["history"][0]["xGA"] = 8
        fixtures["team_h_score"] = 2
        self.assertEqual(attach_team_xg(fixtures, teams, payload)[1], 0)

    def test_unfinished_matches_never_attach_xg(self):
        fixtures = pd.DataFrame([{**fixture(1, hs=2, away_score=1), "finished":False,"kickoff_time":"2025-08-01T15:00:00Z"}])
        self.assertEqual(attach_team_xg(fixtures,[{"id":1,"name":"Man City"},{"id":2,"name":"Spurs"}],self.payload())[1],0)


if __name__ == "__main__":
    unittest.main()
