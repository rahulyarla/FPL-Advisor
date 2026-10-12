"""Optional rival exposure preferences, with strict limits on forecast sacrifice.

Rivals never enter player model training. A score difference's expected value
alone cannot change the optimal squad when the rival's score is constant.
The secondary objective therefore explicitly expresses a cover/differential
preference, and labels its simulations as illustrative rather than calibrated.
"""

import json
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from .http import DataError, utc_now, write_json
from .selection import COUNTS, MIN_XI, MAX_XI, recommend, validate_squad

_STORE_LOCK = threading.RLock()


def parse_entry_id(value):
    text = str(value).strip()
    if not text.isdigit():
        parsed = urlparse(text)
        pieces = parsed.path.strip("/").split("/")
        if parsed.hostname != "fantasy.premierleague.com" or len(pieces) < 2 or pieces[0] != "entry":
            raise DataError("Enter an FPL entry ID or its official /entry/NUMBER/ URL.")
        text = pieces[1]
    if not text.isdigit() or int(text) < 1:
        raise DataError("An FPL entry ID must be a positive whole number.")
    return int(text)


def _season(root):
    report = Path(root) / "reports/snapshot.json"
    if not report.exists():
        raise DataError("Refresh the player data before adding rivals.")
    return str(json.loads(report.read_text())["manifest"]["season"])


def _read_store(root):
    path = Path(root) / "user/rivals.json"
    if not path.exists():
        return {"schema_version": 1, "seasons": {}}
    try:
        data = json.loads(path.read_text())
        if data.get("schema_version") != 1 or not isinstance(data.get("seasons"), dict):
            raise ValueError()
        return data
    except (ValueError, TypeError) as exc:
        raise DataError("The saved rivals file is unreadable. Restore user/rivals.json from a backup.") from exc


def rivals_state(root):
    season = _season(root)
    with _STORE_LOCK:
        entries = _read_store(root)["seasons"].get(season, [])
    return {"season": season, "rivals": entries,
            "note": "Public squads reflect the last deadline. Later transfers and future chips are unknown. Rivals are saved locally and are not uploaded to GitHub."}


def import_rival(root, entry_id, own_entry_id=None, replace_only=False):
    # Import lazily to keep the data-refresh pipeline independent of this store.
    from .pipeline import public_team, state
    entry = parse_entry_id(entry_id)
    if own_entry_id and entry == parse_entry_id(own_entry_id):
        raise DataError("Your own entry cannot be added as a rival.")
    current = state(root)
    if not current.get("ready"):
        raise DataError("Refresh the player data before adding rivals.")
    season = current["manifest"]["season"]
    info = public_team(root, entry)
    if info.get("season", season) != season:
        raise DataError("The data season changed during import. Try importing this rival again.")
    import pandas as pd
    validate_squad(pd.DataFrame(current["players"]), info["squad"])
    info = {**info, "season": season, "enabled": True, "imported_at": utc_now(), "refresh_error": None}
    with _STORE_LOCK:
        data = _read_store(root)
        entries = data["seasons"].setdefault(season, [])
        index = next((i for i, r in enumerate(entries) if int(r["entry_id"]) == entry), None)
        if replace_only and index is None:
            return None
        if index is None:
            entries.append(info)
        else:
            info["enabled"] = entries[index].get("enabled", True)
            entries[index] = info
        write_json(Path(root) / "user/rivals.json", data)
    return info


def edit_rival(root, entry_id, remove=False, enabled=None):
    entry = parse_entry_id(entry_id)
    if not remove and not isinstance(enabled, bool):
        raise DataError("Choose whether this rival is included.")
    with _STORE_LOCK:
        season = _season(root)
        data = _read_store(root)
        entries = data["seasons"].setdefault(season, [])
        if not any(int(r["entry_id"]) == entry for r in entries):
            raise DataError("This rival is not in your saved list.")
        if remove:
            data["seasons"][season] = [r for r in entries if int(r["entry_id"]) != entry]
        else:
            for rival in entries:
                if int(rival["entry_id"]) == entry:
                    rival["enabled"] = enabled
        write_json(Path(root) / "user/rivals.json", data)
    return rivals_state(root)


def refresh_rivals(root, progress=lambda message: None):
    entries = rivals_state(root)["rivals"]
    errors = []
    for number, rival in enumerate(entries, 1):
        progress(f"Refreshing rival {number}/{len(entries)}")
        try:
            import_rival(root, rival["entry_id"], replace_only=True)
        except Exception as exc:
            errors.append({"entry_id": rival["entry_id"], "error": str(exc)})
            with _STORE_LOCK:
                data = _read_store(root)
                for old in data["seasons"].get(_season(root), []):
                    if int(old["entry_id"]) == int(rival["entry_id"]):
                        old["refresh_error"] = str(exc)
                write_json(Path(root) / "user/rivals.json", data)
    return {**rivals_state(root), "errors": errors}


def project_rival(frame, weeks, rival):
    """Enumerate legal formations, avoiding a MILP solve for every rival."""
    squad = validate_squad(frame, rival["squad"])
    plans = []
    for gw in weeks:
        field = f"gw{gw}"
        ranked = {pos: sorted((int(i) for i in squad.index[squad.position == pos]),
                              key=lambda i: (-float(squad.loc[i, field]), i)) for pos in COUNTS}
        best = None
        for defenders in range(MIN_XI["DEF"], MAX_XI["DEF"] + 1):
            for midfielders in range(MIN_XI["MID"], MAX_XI["MID"] + 1):
                forwards = 10 - defenders - midfielders
                if not MIN_XI["FWD"] <= forwards <= MAX_XI["FWD"]:
                    continue
                starters = (ranked["GK"][:1] + ranked["DEF"][:defenders]
                            + ranked["MID"][:midfielders] + ranked["FWD"][:forwards])
                captain = min(starters, key=lambda i: (-float(squad.loc[i, field]), i))
                points = sum(float(squad.loc[i, field]) for i in starters) + float(squad.loc[captain, field])
                if best is None or points > best[0] + 1e-9:
                    best = points, starters, captain
        plans.append({"gameweek": int(gw), "starters": best[1], "captain": best[2],
                      "expected_points_with_captain": round(best[0], 3)})
    return {**rival, "plans": plans}


def prepare_rivals(frame, weeks, entries):
    unique = {}
    for rival in entries:
        if not rival.get("enabled", True):
            continue
        entry = parse_entry_id(rival["entry_id"])
        unique[entry] = rival
    if not unique:
        raise DataError("Add and include at least one rival in the Rivals tab first.")
    projected = [project_rival(frame, weeks, r) for r in unique.values()]
    exposure = {}
    for k, gw in enumerate(weeks):
        counts = Counter()
        for rival in projected:
            plan = rival["plans"][k]
            counts.update(plan["starters"])
            counts[plan["captain"]] += 1
        exposure[gw] = {int(i): value / len(projected) for i, value in counts.items()}
    return projected, exposure


def _coefficients(plan):
    counts = Counter(plan["starters"])
    counts[int(plan["captain"])] += 1
    return counts


def scenario_comparison(frame, weeks, standard, aware, projected, discount=.8, samples=5000, seed=42):
    """Paired outcome draws: a shared player has the SAME score in all teams.

    This is a diagnostic under independent Gaussian errors, not a calibrated
    league-winning probability. Shared player/captain exposure is retained;
    teammate, opponent and cross-week error dependence is not estimated.
    """
    samples = int(samples)
    if not 100 <= samples <= 20000:
        raise DataError("Scenario sample count must be between 100 and 20,000.")
    rng = np.random.default_rng(seed)
    index = {int(i): k for k, i in enumerate(frame.id)}
    totals = {name: np.full(samples, -float(result.get("hit_points", 0)))
              for name, result in [("standard", standard), ("rival_aware", aware)]}
    # Keep only the two next-GW outcomes and one vector per rival. The rival
    # count has no fixed application cap, and does not amplify objective weight.
    next_own, next_rivals = {}, []
    for k, gw in enumerate(weeks):
        means = frame[f"gw{gw}"].to_numpy(dtype=float)
        scales = frame["error_scale"].to_numpy(dtype=float) if "error_scale" in frame else np.full(len(frame), 3.0)
        games = frame[f"games{gw}"].to_numpy(dtype=float) if f"games{gw}" in frame else np.ones(len(frame))
        deviations = np.nan_to_num(scales, nan=3.0) * np.sqrt(np.maximum(games, 0))
        outcomes = means + rng.standard_normal((samples, len(frame))) * deviations

        def draw(plan):
            coefficients = _coefficients(plan)
            columns = [index[i] for i in coefficients]
            weights = np.array(list(coefficients.values()), dtype=float)
            return (outcomes[:, columns] * weights).sum(axis=1)

        for name, result in [("standard", standard), ("rival_aware", aware)]:
            scores = draw(result["plans"][k])
            totals[name] += discount ** k * scores
            if k == 0:
                next_own[name] = scores - float(result.get("hit_points", 0))
        if k == 0:
            next_rivals = [draw(r["plans"][0]) for r in projected]
    rows = []
    for r, scores in zip(projected, next_rivals):
        row = {"entry_id": r["entry_id"], "team_name": r.get("team_name", str(r["entry_id"])),
               "source_gameweek": r.get("source_gameweek"),
               "rival_next_expected_points": r["plans"][0]["expected_points_with_captain"]}
        for name in next_own:
            difference = next_own[name] - scores
            result = standard if name == "standard" else aware
            expected = (result["plans"][0]["expected_points_with_captain"]
                        - result.get("hit_points", 0) - row["rival_next_expected_points"])
            row[name] = {"expected_next_gap": round(float(expected), 2),
                         "relative_score_sd": round(float(difference.std()), 2),
                         "ahead_scenario_pct": round(float((difference > 1e-9).mean() * 100), 1),
                         "tied_scenario_pct": round(float((abs(difference) <= 1e-9).mean() * 100), 1)}
        rows.append(row)
    bands = {}
    for name, values in totals.items():
        low, median, high = np.quantile(values, [.1, .5, .9])
        bands[name] = {"p10": round(float(low), 1), "median": round(float(median), 1), "p90": round(float(high), 1)}
    return {"samples": samples, "seed": seed, "next_gameweek": int(weeks[0]), "rivals": rows,
            "weighted_horizon_scenarios": bands,
            "note": "Illustrative scenarios, not calibrated probabilities. Errors use historical position RMSE, independent players and weeks, and shared player outcomes. Rival squads are held fixed with forecast-best legal XIs and captains; future transfers, hits, chips, autosubs and vice-captain fallback are unknown."}


def compare_recommendations(frame, weeks, entries, options, style="protect", influence=.08,
                            max_points_loss=.01, samples=5000):
    influence, max_points_loss = float(influence), float(max_points_loss)
    if style not in ("protect", "chase") or not np.isfinite(influence) or not 0 <= influence <= .15:
        raise DataError("Choose protect/chase and rival influence between 0 and 15%.")
    if not np.isfinite(max_points_loss) or not 0 <= max_points_loss <= .02:
        raise DataError("The maximum forecast sacrifice must be between 0 and 2%.")
    projected, exposure = prepare_rivals(frame, weeks, entries)
    standard = recommend(frame, weeks, **options)
    value = standard["discounted_expected_points_after_hits"]
    floor = value - abs(value) * max_points_loss
    gw_floors = {}
    for k, p in enumerate(standard["plans"]):
        points = p["expected_points_with_captain"] - (standard["hit_points"] if k == 0 else 0)
        gw_floors[p["gameweek"]] = points - abs(points) * max_points_loss
    reason = None
    if influence == 0:
        aware = standard
        reason = "Zero influence returns the exact standard plan."
    else:
        try:
            aware = recommend(frame, weeks, **options, rival_exposure=exposure, rival_style=style,
                              rival_influence=influence, min_expected_points=floor,
                              min_gameweek_points=gw_floors)
        except DataError as exc:
            aware = standard
            reason = "The rival solve did not find an alternative within the point limits; kept the standard plan. " + str(exc)
    if aware["discounted_expected_points_after_hits"] < floor - .01:
        raise DataError("The rival plan exceeded the allowed forecast sacrifice.")
    comparison = {
        "style": style, "influence": influence, "max_points_loss": max_points_loss,
        "minimum_weighted_points_after_hits": round(floor, 3),
        "weighted_points_difference": round(aware["discounted_expected_points_after_hits"] - value, 3),
        "rivals_count": len(projected), "same_squad": set(aware["squad"]) == set(standard["squad"]),
        "same_next_xi": set(aware["plans"][0]["starters"]) == set(standard["plans"][0]["starters"]),
        "same_next_captain": aware["plans"][0]["captain"] == standard["plans"][0]["captain"],
        "different_players": sorted(set(aware["squad"]) ^ set(standard["squad"])),
        "fallback_reason": reason,
        "exposure": [{"id": int(i), "squad_ownership_pct": round(100 * sum(int(i) in r["squad"] for r in projected) / len(projected), 1),
                      "projected_effective_ownership_pct": round(100 * exposure[weeks[0]].get(int(i), 0), 1)}
                     for i in frame.id if any(int(i) in r["squad"] for r in projected)],
        "note": "Rival exposure is a secondary selection preference. Player forecasts are unchanged. The point limit applies to every forecast GW and the weighted horizon, after your transfer hits. More rivals do not increase its strength.",
    }
    return {"standard": standard, "rival_aware": aware, "comparison": comparison,
            "scenarios": scenario_comparison(frame, weeks, standard, aware, projected,
                                               discount=options.get("discount", .8), samples=samples)}
