"""Hourly scheduled checks fetch only official deadlines, not the whole model."""

import json
import os
import urllib.request
from datetime import datetime, timezone


def near_deadline(events, now):
    for event in events:
        deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
        if 0 < (deadline - now).total_seconds() <= 4 * 3600:
            return True
    return False


def main():
    full = os.environ.get("FPL_REFRESH_EVENT") != "schedule" or os.environ.get("FPL_REFRESH_SCHEDULE") == "17 5,17 * * *"
    if not full:
        try:
            req = urllib.request.Request("https://fantasy.premierleague.com/api/bootstrap-static/", headers={"User-Agent": "FPL-Advisor/3.1"})
            with urllib.request.urlopen(req, timeout=20) as response:
                events = json.load(response)["events"]
            full = near_deadline(events, datetime.now(timezone.utc))
        except (OSError, ValueError, KeyError):
            full = False
            print("Deadline check unavailable; regular twice-daily refreshes remain scheduled.")
    print("Refresh required:", full)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("refresh=" + str(full).lower() + "\n")


if __name__ == "__main__":
    main()
