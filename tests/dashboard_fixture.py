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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from fpl_advisor import server, pipeline
from fpl_advisor.analytics import build_analytics
from fpl_advisor.http import write_json, utc_now
from fpl_advisor.selection import optimize
from test_contracts import player_pool, record


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
        write_json(root / "config.json", {"horizon":2,"discount":.8,"solver_seconds":5,"scenario_samples":500})
        write_json(root / "reports/snapshot.json", {"manifest":manifest,"players":frame.to_dict("records"),"validation":validation})
        write_json(root / "data/bootstrap.json", {})
        history = pd.DataFrame([{**record(i,gw,3+(i+gw)%8,season="2026-27"),"expected_goals":.3,"expected_assists":.2}
                                for i in frame.id for gw in range(1,6)])
        write_json(root / "reports/player_analytics.json", build_analytics(history,frame,manifest))
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
        server.serve(root,port=args.port)


if __name__ == "__main__":
    main()
