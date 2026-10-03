import argparse
import json
import sys
import time
from pathlib import Path

from .http import DataError, write_json
from .pipeline import run, selection
from .server import serve


def main(argv=None):
    parser = argparse.ArgumentParser(description="FPL Advisor: refreshed data, validated forecasts and legal squads.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    subs = parser.add_subparsers(dest="command")
    update = subs.add_parser("run", help="Refresh sources, train when data changes, and write forecasts")
    update.add_argument("--offline", action="store_true", help="Use HTTP caches instead of the internet")
    update.add_argument("--cached", action="store_true", help="Use the bundled saved snapshot without refreshing sources")
    update.add_argument("--force-train", action="store_true")
    ui = subs.add_parser("serve", help="Open the local dashboard")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--open", action="store_true")
    ui.add_argument("--auto-hours", type=float, help="Refresh automatically while this process stays running")
    choose = subs.add_parser("recommend", help="Create a legal lineup, transfers or wildcard")
    choose.add_argument("--mode", choices=("lineup", "transfers", "wildcard"), default="wildcard")
    choose.add_argument("--team-json", type=Path, help="Current squad, bank, selling_prices and free_transfers")
    choose.add_argument("--budget", type=float, default=100.0)
    choose.add_argument("--max-transfers", type=int, default=2)
    watch = subs.add_parser("watch", help="Refresh on an interval while the process stays running")
    watch.add_argument("--interval-hours", type=float, default=12)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            run(args.root, offline=args.offline, cached=args.cached, force=args.force_train, progress=print)
        elif args.command == "recommend":
            payload = json.loads(args.team_json.read_text()) if args.team_json else {}
            payload.update({"mode": args.mode, "budget": args.budget, "max_transfers": args.max_transfers})
            result = selection(args.root, payload)
            write_json(args.root / "user/latest_selection.json", result)
            print(json.dumps(result, indent=2))
        elif args.command == "watch":
            if args.interval_hours < 1:
                raise DataError("Refresh interval must be at least one hour.")
            while True:
                try:
                    run(args.root, progress=print)
                except DataError as exc:
                    print(f"Refresh failed; keeping prior reports: {exc}", file=sys.stderr)
                time.sleep(args.interval_hours * 3600)
        else:
            serve(args.root, port=getattr(args, "port", 8765), open_browser=getattr(args, "open", True),
                  auto_hours=getattr(args, "auto_hours", None))
    except (DataError, OSError) as exc:
        print(f"FPL Advisor: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
