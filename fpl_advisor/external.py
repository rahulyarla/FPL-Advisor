"""Optional Understat team xG, matched to verified FPL fixtures without fuzzy names.

Only completed matches enter the context; FixtureContext later applies the strict
GW cutoff. Failures leave missing indicators and warnings, never fabricated xG.
"""

import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .data import bool_value, csv_from_bytes
from .http import DataError


def club_key(name):
    value = re.sub(r"[^a-z0-9]", "", str(name).lower())
    aliases = {"mancity": "manchestercity", "manutd": "manchesterunited", "spurs": "tottenham",
               "tottenhamhotspur": "tottenham", "wolves": "wolverhamptonwanderers",
               "nottmforest": "nottinghamforest", "westbrom": "westbromwichalbion",
               "sheffieldutd": "sheffieldunited", "newcastle": "newcastleunited",
               "westham": "westhamunited", "leicester": "leicestercity", "ipswich": "ipswichtown",
               "brightonandhovealbion": "brighton", "brightonhovealbion": "brighton",
               "leedsunited": "leeds", "afcbournemouth": "bournemouth"}
    return aliases.get(value, value)


def attach_team_xg(fixtures, teams, payload):
    result = fixtures.copy()
    result["team_h_xg"], result["team_a_xg"] = np.nan, np.nan
    titles = {int(t["id"]): club_key(t["name"]) for t in teams}
    histories = {}
    for team in payload.get("teams", {}).values():
        title = club_key(team["title"])
        for match in team.get("history", []):
            key = (title, str(match["date"])[:10], match["h_a"], int(match["scored"]), int(match["missed"]))
            histories.setdefault(key, []).append(match)
    matched = 0
    for index, fixture in result.iterrows():
        if (not bool_value(fixture.get("finished", True)) or pd.isna(fixture.get("team_h_score"))
                or pd.isna(fixture.get("team_a_score")) or not fixture.get("kickoff_time")):
            continue
        h, a = titles.get(int(fixture.team_h)), titles.get(int(fixture.team_a))
        date, hs, away = str(fixture.kickoff_time)[:10], int(fixture.team_h_score), int(fixture.team_a_score)
        home = histories.get((h, date, "h", hs, away), [])
        visitor = histories.get((a, date, "a", away, hs), [])
        if len(home) != 1 or len(visitor) != 1:
            continue
        hx, ax = float(home[0]["xG"]), float(visitor[0]["xG"])
        # Reciprocal xGA confirms both club histories refer to the same match.
        if not np.isfinite([hx, ax]).all() or min(hx, ax) < 0:
            continue
        if abs(hx - float(visitor[0]["xGA"])) > .001 or abs(ax - float(home[0]["xGA"])) > .001:
            continue
        result.at[index, "team_h_xg"], result.at[index, "team_a_xg"] = hx, ax
        matched += 1
    return result, matched


def enrich_fixture_sets(client, fixture_sets, bootstrap, manifest, config, progress=lambda message: None):
    raw_root = f"https://raw.githubusercontent.com/{config['vaastav_repository']}/{config['vaastav_ref']}/data"

    def enrich(item):
        season, fixtures = item
        try:
            if season == manifest["season"]:
                teams = bootstrap["teams"]
            else:
                body = client.get(f"{raw_root}/{season}/teams.csv", optional=True)
                if body:
                    teams = csv_from_bytes(body).to_dict("records")
                else:
                    raw = client.json(f"{raw_root}/{season}/raw.json", optional=True)
                    teams = raw.get("teams", []) if raw else []
            if not teams:
                return season, fixtures, 0, "No historical club-name mapping available."
            payload = client.json(f"https://understat.com/getLeagueData/EPL/{season[:4]}", optional=True,
                                  extra_headers={"X-Requested-With": "XMLHttpRequest"})
            if not payload or not isinstance(payload.get("teams"), dict):
                return season, fixtures, 0, "Understat team data unavailable."
            enhanced, count = attach_team_xg(fixtures, teams, payload)
            return season, enhanced, count, None if count else "No verified FPL/Understat match pairs."
        except (DataError, ValueError, KeyError, TypeError) as exc:
            return season, fixtures, 0, str(exc)

    coverage, warnings = [], []
    progress("Checking optional Understat team xG against completed FPL fixture dates and scores")
    # Independent seasons; cap concurrency to avoid overloading public services.
    with ThreadPoolExecutor(max_workers=3) as executor:
        for season, enhanced, count, problem in executor.map(enrich, fixture_sets.items()):
            fixture_sets[season] = enhanced
            coverage.append({"season": season, "matched_fixtures": count})
            progress(f"Understat {season}: {count} verified match pairs")
            if problem:
                warnings.append(f"Optional Understat {season}: {problem}")
    manifest["external_team_xg"] = coverage
    manifest["sources"] = list({s["url"]: s for s in manifest.get("sources", []) + client.sources}.values())
    manifest["warnings"] = sorted(set(manifest.get("warnings", []) + warnings))
    manifest["data_policy"] += " Optional verified Understat team xG from earlier completed matches only."
    return fixture_sets
