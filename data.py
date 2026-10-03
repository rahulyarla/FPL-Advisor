"""Vaastav history plus finalized official FPL results, normalized by player/GW.

Numeric player IDs are seasonal. Stable FPL codes are used for cross-season form;
missing codes are deliberately isolated by season. Prices and xP are never ML inputs.
"""

import io
import re
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .http import DataError, utc_now

API = "https://fantasy.premierleague.com/api/"
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
STATS = ["total_points", "minutes", "goals_scored", "assists", "clean_sheets",
         "goals_conceded", "saves", "bonus", "bps", "influence", "creativity",
         "threat", "expected_goals", "expected_assists", "expected_goals_conceded",
         "starts", "yellow_cards", "red_cards", "defensive_contribution"]


def season_order(season, gw=0):
    return int(season[:4]) * 100 + int(gw)


def season_from_bootstrap(bootstrap):
    events = bootstrap.get("events", [])
    if not events or not bootstrap.get("elements") or not bootstrap.get("teams"):
        raise DataError("FPL returned an empty or incomplete season snapshot.")
    first = min(datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00")) for e in events)
    year = first.year if first.month >= 6 else first.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def next_event(bootstrap, now=None):
    now = now or datetime.now(timezone.utc)
    future = [e for e in bootstrap["events"]
              if datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00")) > now]
    if not future:
        raise DataError("There is no future deadline in the current FPL season.")
    return min(future, key=lambda e: e["id"])


def bool_value(value):
    return str(value).lower() in ("true", "1", "1.0")


def csv_from_bytes(body):
    try:
        return pd.read_csv(io.BytesIO(body))
    except (pd.errors.ParserError, UnicodeDecodeError, pd.errors.EmptyDataError) as exc:
        raise DataError("A source CSV is empty or malformed.") from exc


def normalize_vaastav(raw, players, fixtures, season):
    required = {"element", "fixture", "total_points", "minutes"}
    if not required.issubset(raw):
        raise DataError(f"{season} CSV lacks {sorted(required - set(raw.columns))}")
    raw = raw.copy().drop_duplicates(["element", "fixture"], keep="last")
    player_map = players.set_index("id").to_dict("index")
    fixture_map = {int(x["id"]): x for x in fixtures.to_dict("records")}
    records = []
    for r in raw.to_dict("records"):
        f = fixture_map.get(int(r["fixture"]))
        if not f:
            raise DataError(f"Unknown fixture {r['fixture']} in {season}.")
        gw = r.get("GW", r.get("round", f.get("event")))
        if pd.isna(gw) or pd.isna(f.get("team_h_score")) or pd.isna(f.get("team_a_score")):
            continue  # unplayed/provisional rows do not train the model
        player = player_map.get(int(r["element"]), {})
        position = r.get("position")
        if position == "AM" or int(player.get("element_type", 0)) == 5:
            continue  # The retired Assistant Manager chip is not a player position.
        if position not in POSITIONS.values():
            position = POSITIONS.get(int(player.get("element_type", 0)))
        if not position:
            raise DataError(f"Unknown position for element {r['element']} in {season}.")
        home = bool_value(r.get("was_home", False))
        code = player.get("code", 0)
        code = int(code) if pd.notna(code) else 0
        rec = {"season": season, "element": int(r["element"]), "code": code,
               "gw": int(gw), "fixture": int(r["fixture"]), "position": position,
               "name": r.get("name", player.get("web_name", str(r["element"]))),
               "team_id": int(f["team_h"] if home else f["team_a"]),
               "home_count": int(home), "fixture_count": 1,
               "defensive_stats_available": int("defensive_contribution" in raw)}
        for stat in STATS:
            value = r.get(stat, 0)
            rec[stat] = float(value) if pd.notna(value) else 0.0
        records.append(rec)
    return aggregate_gameweeks(pd.DataFrame(records))


def aggregate_gameweeks(df):
    if df.empty:
        return df
    keys = ["season", "element", "gw"]
    spec = {s: "sum" for s in STATS}
    spec.update({"code": "last", "position": "last", "name": "last", "team_id": "last",
                 "home_count": "sum", "fixture_count": "sum", "defensive_stats_available": "max"})
    result = df.groupby(keys, as_index=False).agg(spec)
    if (result.fixture_count <= 0).any() or not result.position.isin(POSITIONS.values()).all():
        raise DataError("Invalid player/gameweek records.")
    result["player_key"] = [f"code:{int(c)}" if c > 0 else f"{s}:{int(e)}"
                            for c, s, e in zip(result.code, result.season, result.element)]
    result["order"] = [season_order(s, g) for s, g in zip(result.season, result.gw)]
    return result


def official_gameweek(client, bootstrap, fixtures, season, gw, warnings, summary_cache=None):
    summary_cache = {} if summary_cache is None else summary_cache
    payload = client.json(f"{API}event/{gw}/live/", cache=False)
    if not payload.get("elements"):
        raise DataError(f"Official results for completed GW{gw} are empty.")
    player_map = {int(p["id"]): p for p in bootstrap["elements"]}
    fixture_map = {int(f["id"]): f for f in fixtures.to_dict("records")
                   if pd.notna(f.get("event")) and int(f["event"]) == gw}
    by_team = defaultdict(list)
    for f in fixture_map.values():
        if not bool_value(f.get("finished")):
            raise DataError(f"GW{gw} has an unfinished fixture, despite the completed event flag.")
        by_team[int(f["team_h"])].append(f)
        by_team[int(f["team_a"])].append(f)
    records = []
    for row in payload["elements"]:
        p = player_map.get(int(row["id"]))
        if not p:
            continue
        explained = [int(e["fixture"]) for e in row.get("explain", []) if int(e["fixture"]) in fixture_map]
        if not explained:
            # FPL has no fixture record for an unregistered or unavailable element.
            # Do not invent earlier zero-point histories for later signings.
            continue
        assigned = [fixture_map[f] for f in explained]
        teams = set.intersection(*[{int(f["team_h"]), int(f["team_a"])} for f in assigned])
        team_id = int(p["team"])
        if team_id not in teams:
            # Transfer between PL clubs: element-summary has the actual historical side.
            if int(p["id"]) not in summary_cache:
                summary_cache[int(p["id"])] = client.json(f"{API}element-summary/{p['id']}/", cache=False)
            detail = summary_cache[int(p["id"])]
            histories = [h for h in detail.get("history", []) if int(h.get("round", 0)) == gw]
            if not histories:
                raise DataError(f"Cannot resolve historical team for player {p['id']} in GW{gw}.")
            first = histories[0]
            f = fixture_map[int(first["fixture"])]
            team_id = int(f["team_h"] if first["was_home"] else f["team_a"])
            warnings.append(f"Resolved GW{gw} historical team for transferred player {p['web_name']}.")
        stats = row["stats"]
        rec = {"season": season, "element": int(p["id"]), "code": int(p["code"]),
               "gw": gw, "position": POSITIONS[int(p["element_type"])],
               "name": f"{p['first_name']} {p['second_name']}", "team_id": team_id,
               "fixture_count": len(assigned),
               "home_count": sum(int(int(f["team_h"]) == team_id) for f in assigned),
               "defensive_stats_available": int("defensive_contribution" in stats)}
        for stat in STATS:
            rec[stat] = float(stats.get(stat, 0) or 0)
        records.append(rec)
    result = aggregate_gameweeks(pd.DataFrame(records))
    if result.empty or len(result) < 100:
        raise DataError(f"GW{gw} has too little usable official data ({len(result)} players).")
    return result


def sync(client, config, progress=lambda message: None):
    progress("Fetching current FPL players, prices and availability")
    bootstrap = client.json(API + "bootstrap-static/", cache=False)
    season = season_from_bootstrap(bootstrap)
    fixtures = pd.DataFrame(client.json(API + "fixtures/", cache=False))
    if not {"id", "event", "team_h", "team_a"}.issubset(fixtures):
        raise DataError("Official fixtures are missing required columns.")
    upcoming = next_event(bootstrap)
    completed = sorted(int(e["id"]) for e in bootstrap["events"]
                       if e.get("finished") and e.get("data_checked") and int(e["id"]) < upcoming["id"])
    # Completed individual matches in an unfinished/unverified GW are still
    # provisional. Hide those results from both player and team-context features.
    unfinalized = ~fixtures.event.isin(completed)
    fixtures.loc[unfinalized, ["team_h_score", "team_a_score"]] = np.nan
    # A live/in-progress GW is deliberately excluded even if some matches have finished.
    warnings = []
    ignored = [int(e["id"]) for e in bootstrap["events"]
               if int(e["id"]) < upcoming["id"] and int(e["id"]) not in completed]
    if ignored:
        warnings.append(f"Unfinalized gameweeks excluded from training: {ignored}.")
    raw_root = (f"https://raw.githubusercontent.com/{config['vaastav_repository']}/"
                f"{config['vaastav_ref']}/data")
    historical = []
    fixture_sets = {}
    history_manifest = []
    automatic = config["historical_seasons"] == "auto"
    count = int(config.get("history_seasons_to_use", 3))
    if not 1 <= count <= 8:
        raise DataError("history_seasons_to_use must be 1–8.")
    start = int(season[:4])
    seasons = ([f"{y}-{str(y+1)[-2:]}" for y in range(start - 1, max(2015, start - count - 4), -1)]
               if automatic else config["historical_seasons"])
    for past in seasons:
        if not re.fullmatch(r"\d{4}-\d{2}", past):
            raise DataError(f"Invalid historical season {past!r}.")
        if season_order(past) >= season_order(season):
            continue
        progress(f"Checking Vaastav historical data for {past}")
        prefix = f"{raw_root}/{past}"
        body = client.get(prefix + "/gws/merged_gw.csv", optional=automatic)
        if body is None:
            warnings.append(f"Historical season {past} is not yet available in Vaastav; using an earlier complete season.")
            continue
        raw = csv_from_bytes(body)
        players = csv_from_bytes(client.get(prefix + "/players_raw.csv"))
        past_fixtures = csv_from_bytes(client.get(prefix + "/fixtures.csv"))
        normal = normalize_vaastav(raw, players, past_fixtures, past)
        if normal.empty or int(normal.gw.max()) < 38:
            if automatic:
                warnings.append(f"Incomplete Vaastav season {past} excluded from historical training.")
                continue
            raise DataError(f"Configured training season {past} is incomplete. Remove it from historical_seasons.")
        historical.append(normal)
        fixture_sets[past] = past_fixtures
        history_manifest.append({"season": past, "records": len(normal), "latest_gameweek": int(normal.gw.max())})
        if automatic and len(historical) >= count:
            break
    if not historical:
        raise DataError("At least one complete historical season is needed to train the model.")
    # Also check the current Vaastav season; official finalized results take precedence.
    progress(f"Checking Vaastav's {season} snapshot")
    current_body = client.get(f"{raw_root}/{season}/gws/merged_gw.csv", optional=True)
    current_vaastav_gw = None
    if current_body:
        raw = csv_from_bytes(current_body)
        current_vaastav_gw = int(raw.get("GW", raw.get("round", pd.Series([0]))).max())
        historical_current = normalize_vaastav(raw, pd.DataFrame(bootstrap["elements"]), fixtures, season)
        historical_current = historical_current[historical_current.gw.isin(completed)]
    else:
        historical_current = pd.DataFrame()
    current = []
    summary_cache = {}
    for gw in completed:
        progress(f"Refreshing finalized official results: GW{gw}")
        current.append(official_gameweek(client, bootstrap, fixtures, season, gw, warnings, summary_cache))
    current_frame = pd.concat(current, ignore_index=True) if current else pd.DataFrame()
    if not historical_current.empty and not current_frame.empty:
        # Retain historical player position/code metadata where available, but not stale scores.
        old = historical_current.set_index(["season", "element", "gw"])
        for idx, r in current_frame.iterrows():
            key = (r.season, r.element, r.gw)
            if key in old.index:
                current_frame.at[idx, "position"] = old.loc[key, "position"]
    elif not historical_current.empty:
        current_frame = historical_current
    if not current_frame.empty:
        historical.append(current_frame)
    history = pd.concat(historical, ignore_index=True)
    if history.duplicated(["season", "element", "gw"]).any():
        raise DataError("Duplicate player/gameweek rows after refreshing sources.")
    if ((history.season == season) & (history.gw >= int(upcoming["id"]))).any():
        raise DataError("Current/future gameweek results must not enter training.")
    fixture_sets[season] = fixtures
    manifest = {"created_at": utc_now(), "season": season,
                "next_gameweek": int(upcoming["id"]), "deadline": upcoming["deadline_time"],
                "completed_gameweeks": completed, "vaastav_current_latest_gameweek": current_vaastav_gw,
                "historical_seasons": history_manifest, "records": len(history),
                "live_refresh": not client.offline, "warnings": sorted(set(warnings)),
                "sources": client.sources,
                "data_policy": "Vaastav history; finalized official current results; current FPL prices and flags."}
    return history.sort_values(["order", "element"]).reset_index(drop=True), fixture_sets, bootstrap, manifest
