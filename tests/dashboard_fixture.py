"""A temporary local dashboard with deterministic, mocked public FPL imports.

Used only by browser/DOM checks. It never contacts FPL or modifies the real
project's user files, forecasts, models or rivals.
"""

import argparse
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
import gzip

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from fpl_advisor import server, pipeline
from fpl_advisor.analytics import build_analytics
from fpl_advisor.http import write_json, utc_now
from fpl_advisor.selection import optimize
from fpl_advisor.qualitative import evidence, apply_evidence
from test_contracts import player_pool, record
from test_integrity import FakeReader
import fpl_advisor.integrity as integrity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shutil.copytree(source / "fpl_advisor/static", root / "fpl_advisor/static")
        frame = player_pool()
        frame["gw6"] = 10 + (40-frame.id)*.01
        frame["gw7"] = 9 + (40-frame.id)*.01
        for name, value in {"full_name": "", "ownership": 20.0, "news": "", "risk": "", "status": "a",
                            "minutes_recent": 90.0, "history_count": 5, "error_scale": 2.0,
                            "fixtures6": "Fixture (H)", "fixtures7": "Fixture (A)", "games6": 1, "games7": 1}.items():
            frame[name] = value
        frame["full_name"] = "Fixture player " + frame.id.astype(str)
        frame["code"] = frame.id + 100
        frame["next_points"] = frame.gw6
        frame["horizon_points"] = frame.gw6+frame.gw7
        frame["weighted_points"] = frame.gw6+.8*frame.gw7
        manifest = {"season": "2026-27", "created_at": utc_now(), "completed_gameweeks": [1,2,3,4,5],
                    "forecast_weeks": [6,7], "next_gameweek": 6, "live_refresh": True,
                    "deadline": (datetime.now(timezone.utc)+timedelta(days=7)).isoformat()}
        validation = {"validation_season": "2025-26", "positions": {pos:{"selected_method":"gradient_boosting",
                      "test_regular_players":{"rmse":2.0,"rows":100},"training_rows":1000,
                      "blocks":[{"role":"test","regular_players":{"form_baseline":{"rmse":3.0}}}]}
                      for pos in ["GK","DEF","MID","FWD"]}}
        # Representative v3 UI data, explicitly synthetic and never an evaluated real forecast.
        validation.update({"model_version":"feedback-ensemble-v3.0", "validation_policy":"Fixture: rolling development followed by a separate full-season historical test.",
                           "seasons":{"2023-24":1000,"2024-25":1000,"2025-26":1000,"2026-27":200},
                           "rows":3200,"development_rows":400,"test_rows":400,"feature_count":82,"candidate_count":8,
                           "aggregate_test_regular":{"selected":{"rmse":2.0},"legacy":{"rmse":2.1},"baseline":{"rmse":3.0}}})
        for result in validation["positions"].values():
            result.update({"selected_method":"adaptive_ensemble","recipe":{"components":["ridge_recent","hurdle"],"weights":[.5,.5]},
                           "test_legacy_regular_players":{"rmse":2.1},"test_baseline_regular_players":{"rmse":3.0}})
            result["test_regular_players"]["mae"]=1.5
        feedback = {"model_family":"feedback-ensemble-v3.0", "correction_gain":0,"matched_forecasts":80,
                    "status":"Synthetic test fixture: waiting for a third finalized GW before a live correction can be checked.",
                    "gameweeks":[{"model_family":"feedback-ensemble-v3.0","gameweek":w,"lead":1,"rows":40,"rmse":2.0,"mae":1.5,"mean_error":.2} for w in [4,5]]}
        write_json(root / "config.json", {"horizon":2,"discount":.8,"solver_seconds":5,"scenario_samples":500})
        write_json(root / "reports/snapshot.json", {"manifest":manifest,"players":frame.to_dict("records"),"validation":validation,"feedback":feedback})
        write_json(root / "data/bootstrap.json", {})
        history = pd.DataFrame([{**record(i,gw,3+(i+gw)%8,season="2026-27"),"expected_goals":.3,"expected_assists":.2}
                                for i in frame.id for gw in range(1,6)])
        history.to_csv(root / "data/history.csv.gz",index=False,compression="gzip")
        write_json(root / "data/fixtures.json",{})
        write_json(root / "data/manifest.json",manifest)
        import fpl_advisor.qualitative as qualitative
        from fpl_advisor.qual_sources import imports
        docs=[{"id":"fixture1","source":"Fixture creator","kind":"youtube","url":"https://www.youtube.com/watch?v=abcdefghijk",
               "title":"Synthetic FPL GW6","text":"Fixture player 1 is a great pick.","coverage":"transcript","published_at":utc_now(),"fetched_at":utc_now()}]
        def fake_collect(root_path,progress=None,online=True):
            return {"documents":[*docs,*imports(root_path)],"settings":{"lookback_days":7},"coverage":[{"source":"Fixture creator","kind":"youtube","items":1,"transcripts":1,"status":"transcripts","details":[]},{"source":"Fixture blocked source","items":0,"status":"unavailable","details":[{"reason":"Synthetic unavailable coverage"}]}]}
        qualitative.collect=fake_collect
        q=evidence(fake_collect(root),frame.to_dict("records"),manifest)
        candidates,learned=apply_evidence(frame.to_dict("records"),[6,7],q)
        q["learning"]={"status":"Synthetic fixture: no finalized paired results.","recommended_gain":0,"gameweeks":[]}
        write_json(root / "reports/snapshot.json", {"manifest":manifest,"players":frame.to_dict("records"),"validation":validation,"feedback":feedback,
                                                  "qualitative":q,"qualitative_candidate_players":candidates,"qualitative_learned_players":learned})
        analytics=build_analytics(history,frame,manifest)
        for p in analytics["players"].values():
            p["recorded_predictions"]=[{"gameweek":h["gameweek"],"predicted":h["total_points"]-.2} for h in p["history"] if h["gameweek"] in [4,5]]
        write_json(root / "reports/player_analytics.json", analytics)
        (root/'reports/backtest_predictions.csv.gz').write_bytes(gzip.compress(b'season,gw,prediction,actual\n2025-26,1,4,5\n'))
        (root/'reports/forecast_results.csv').write_text('season,gw,prediction,actual\n2026-27,4,4,5\n')
        squad = optimize(frame,[6,7],budget=80,seconds=5)["squad"]

        def fake_import(root_path, entry_id):
            from fpl_advisor.rivals import parse_entry_id
            entry = parse_entry_id(entry_id)
            return {"entry_id":entry,"team_name":f"Fixture team {entry}","source_gameweek":5,
                    "season":"2026-27","squad":squad,"bank":.3,"free_transfers":None,"selling_prices":{},
                    "last_deadline_starters":squad[:11],"last_deadline_captain":squad[0],
                    "chips_used":[],"active_chip_at_deadline":None,"total_points_through_completed":300,
                    "history":[{"gameweek":w,"points":60,"total_points":60*w} for w in range(1,6)],
                    "imported_at":utc_now(),"note":"Deterministic public-import fixture; no FPL request was made."}

        server.public_team = fake_import
        pipeline.public_team = fake_import
        integrity.PublicReader = FakeReader
        server.serve(root,port=args.port)


if __name__ == "__main__":
    main()
