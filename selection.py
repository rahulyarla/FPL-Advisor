"""Joint squad, legal XI and captain optimization with actual selling-price budgets."""

from collections import Counter

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from .http import DataError

COUNTS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
MIN_XI = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
MAX_XI = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}


def tenths(value):
    number = float(value) * 10
    if not np.isfinite(number) or number < 0 or abs(number - round(number)) > 1e-6:
        raise DataError("Money must be non-negative and expressed to one decimal place, in £m.")
    return int(round(number))


def validate_squad(frame, ids):
    ids = [int(i) for i in ids]
    if len(ids) != 15 or len(set(ids)) != 15:
        raise DataError("Select exactly 15 distinct players: 2 GK, 5 DEF, 5 MID and 3 FWD.")
    lookup = frame.set_index("id")
    missing = set(ids) - set(lookup.index)
    if missing:
        raise DataError(f"Players absent from the current season: {sorted(missing)}.")
    squad = lookup.loc[ids]
    if Counter(squad.position) != COUNTS:
        raise DataError("The squad needs 2 goalkeepers, 5 defenders, 5 midfielders and 3 forwards.")
    if squad.groupby("team_id").size().max() > 3:
        raise DataError("The squad has more than three players from one club.")
    return squad


def optimize(frame, weeks, mode="wildcard", owned=None, bank=0.0, selling_prices=None,
             budget=100.0, free_transfers=1, max_transfers=2, discount=0.8,
             locked=None, excluded=None, seconds=30):
    if mode not in ("wildcard", "lineup", "transfers"):
        raise DataError("Choose lineup, transfers or wildcard.")
    if not weeks or not 0 < float(discount) <= 1:
        raise DataError("A forecast horizon and discount in (0,1] are required.")
    free_transfers, max_transfers = int(free_transfers), int(max_transfers)
    if not 0 <= free_transfers <= 5 or not 0 <= max_transfers <= 15:
        raise DataError("Free transfers must be 0–5 and the transfer limit 0–15.")
    owned = set(int(i) for i in (owned or []))
    locked = set(int(i) for i in (locked or []))
    excluded = set(int(i) for i in (excluded or []))
    if locked & excluded:
        raise DataError("A player cannot be both locked and excluded.")
    if mode in ("lineup", "transfers") or owned:
        validate_squad(frame, sorted(owned))
    if mode == "lineup":
        candidates = frame[frame.id.isin(owned)].copy()
    else:
        # Existing players can be kept despite injury flags, but new purchases
        # must be selectable and have at least 75% official availability.
        candidates = frame[(frame.selectable & (frame.availability >= .75)) | frame.id.isin(owned | locked)].copy()
        candidates = candidates[~candidates.id.isin(excluded)]
    candidates = candidates.sort_values("id").reset_index(drop=True)
    if len(candidates) < 15:
        raise DataError("Too few eligible candidates after exclusions.")
    if not locked.issubset(set(candidates.id)):
        raise DataError("A locked player is missing or unavailable in this season.")
    n, horizon = len(candidates), len(weeks)
    # x = squad; y[g] = starting XI; c[g] = captain; h = paid transfer count.
    variables = n * (1 + 2 * horizon) + 1
    hit_idx = variables - 1
    score = np.zeros(variables)
    lower = np.zeros(variables)
    upper = np.ones(variables)
    upper[hit_idx] = 15
    rows, cols, values, lbs, ubs = [], [], [], [], []

    def constraint(mapping, low=-np.inf, high=np.inf):
        row = len(lbs)
        for col, value in mapping.items():
            if value:
                rows.append(row)
                cols.append(int(col))
                values.append(float(value))
        lbs.append(float(low))
        ubs.append(float(high))

    position_indices = {pos: np.flatnonzero(candidates.position.to_numpy() == pos).tolist() for pos in COUNTS}
    for pos, indices in position_indices.items():
        constraint({i: 1 for i in indices}, COUNTS[pos], COUNTS[pos])
    for club in candidates.team_id.unique():
        indices = np.flatnonzero(candidates.team_id.to_numpy() == club)
        constraint({int(i): 1 for i in indices}, high=3)
    for i, player_id in enumerate(candidates.id):
        if player_id in locked or (mode == "lineup" and player_id in owned):
            lower[i] = upper[i] = 1
    spend = candidates.cost.to_numpy(dtype=float).copy()
    sale_prices = {}
    if owned and mode != "lineup":
        selling_prices = selling_prices or {}
        missing_prices = [i for i in owned if str(i) not in selling_prices and i not in selling_prices]
        if missing_prices:
            raise DataError("Confirm actual selling prices for every owned player before calculating transfers/wildcards.")
        for i in owned:
            sale = tenths(selling_prices.get(str(i), selling_prices.get(i)))
            current = int(frame.set_index("id").loc[i, "cost"])
            if sale > current:
                raise DataError("A selling price cannot exceed the player's current buying price.")
            sale_prices[i] = sale
        limit = tenths(bank) + sum(sale_prices.values())
        for i, player_id in enumerate(candidates.id):
            if player_id in owned:
                spend[i] = sale_prices[int(player_id)]
    else:
        limit = tenths(budget)
    if mode != "lineup":
        constraint({i: spend[i] for i in range(n)}, high=limit)
    new_indices = [i for i, player_id in enumerate(candidates.id) if player_id not in owned]
    if mode == "transfers":
        constraint({i: 1 for i in new_indices}, high=max_transfers)
        constraint({**{i: 1 for i in new_indices}, hit_idx: -1}, high=free_transfers)
        score[hit_idx] = 4.0
    else:
        upper[hit_idx] = 0
    for k, gw in enumerate(weeks):
        offset_y = n * (1 + 2 * k)
        offset_c = offset_y + n
        points = candidates[f"gw{gw}"].to_numpy()
        if not np.isfinite(points).all():
            raise DataError("Non-finite forecast points.")
        weight = discount ** k
        score[offset_y:offset_y + n] = -weight * points
        score[offset_c:offset_c + n] = -weight * points
        # Tiny reserve-cover preference. It is not an exact automatic-substitution simulation.
        score[:n] -= 0.02 * weight * points
        constraint({offset_y + i: 1 for i in range(n)}, 11, 11)
        constraint({offset_c + i: 1 for i in range(n)}, 1, 1)
        for pos, indices in position_indices.items():
            constraint({offset_y + i: 1 for i in indices}, MIN_XI[pos], MAX_XI[pos])
        for i in range(n):
            constraint({offset_y + i: 1, i: -1}, high=0)
            constraint({offset_c + i: 1, offset_y + i: -1}, high=0)
    matrix = coo_matrix((values, (rows, cols)), shape=(len(lbs), variables)).tocsc()
    result = milp(score, integrality=np.ones(variables), bounds=Bounds(lower, upper),
                  constraints=LinearConstraint(matrix, lbs, ubs),
                  options={"time_limit": float(seconds), "mip_rel_gap": .001})
    if result.x is None or result.status not in (0, 1):
        raise DataError("No feasible squad fits these constraints. Increase the budget, relax locks or allow more transfers.")
    rounded = np.rint(result.x)
    if np.abs(result.x - rounded).max() > 1e-4:
        raise DataError("The solver did not find a valid integer squad within the time limit.")
    selected_ids = candidates.loc[rounded[:n] > .5, "id"].astype(int).tolist()
    squad = validate_squad(frame, selected_ids).reset_index()
    if mode != "lineup" and sum(spend[rounded[:n] > .5]) > limit + 1e-4:
        raise DataError("Solver result exceeds the budget.")
    plans = []
    for k, gw in enumerate(weeks):
        yi, ci = n * (1 + 2 * k), n * (2 + 2 * k)
        starter_ids = candidates.loc[rounded[yi:yi + n] > .5, "id"].astype(int).tolist()
        captain_ids = candidates.loc[rounded[ci:ci + n] > .5, "id"].astype(int).tolist()
        starters = squad[squad.id.isin(starter_ids)]
        if len(starters) != 11 or len(captain_ids) != 1 or captain_ids[0] not in starter_ids:
            raise DataError("Invalid XI/captain returned by solver.")
        counts = Counter(starters.position)
        if any(counts[p] < MIN_XI[p] or counts[p] > MAX_XI[p] for p in COUNTS):
            raise DataError("Invalid starting formation returned by solver.")
        captain = int(captain_ids[0])
        others = starters[starters.id != captain].sort_values(f"gw{gw}", ascending=False)
        vice = int(others.iloc[0].id)
        bench = squad[~squad.id.isin(starter_ids)]
        reserve_gk = bench[bench.position == "GK"].id.astype(int).tolist()
        reserves = bench[bench.position != "GK"].sort_values(f"gw{gw}", ascending=False).id.astype(int).tolist()
        expected = float(starters[f"gw{gw}"].sum() + starters.set_index("id").loc[captain, f"gw{gw}"])
        plans.append({"gameweek": gw, "starters": starter_ids, "captain": captain, "vice_captain": vice,
                      "bench_goalkeeper": reserve_gk[0], "bench_order": reserves,
                      "formation": f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}",
                      "expected_points_with_captain": round(expected, 3)})
    bought = sorted(set(selected_ids) - owned) if owned else []
    sold = sorted(owned - set(selected_ids)) if owned else []
    paid = max(0, len(bought) - free_transfers) if mode == "transfers" else 0
    if mode == "transfers" and len(bought) > max_transfers:
        raise DataError("Solver result exceeds the transfer allowance.")
    cash_remaining = (limit - sum(spend[rounded[:n] > .5])) / 10 if mode != "lineup" else None
    value = sum(discount ** i * p["expected_points_with_captain"] for i, p in enumerate(plans)) - paid * 4
    return {"mode": mode, "squad": selected_ids, "plans": plans, "bought": bought, "sold": sold,
            "transfer_count": len(bought), "hit_points": paid * 4,
            "remaining_bank": round(cash_remaining, 1) if cash_remaining is not None else None,
            "market_squad_value": round(float(squad.price.sum()), 1),
            "discounted_expected_points_after_hits": round(value, 3),
            "solver_status": "optimal_within_tolerance" if result.status == 0 else "feasible_time_limit",
            "relative_gap": float(result.mip_gap) if getattr(result, "mip_gap", None) is not None else None,
            "assumptions": ["No further transfers during this forecast horizon.",
                            "Latest observed form and official availability are held constant in later weeks.",
                            "Vice-captain fallback and automatic substitutions are not simulated.",
                            "Bench cover has a small tie-breaking weight."]}


def recommend(frame, weeks, **options):
    chosen = optimize(frame, weeks, **options)
    if options.get("owned") and options.get("mode") != "lineup":
        hold = optimize(frame, weeks, mode="lineup", owned=options["owned"],
                        discount=options.get("discount", .8), seconds=options.get("seconds", 30))
        chosen["hold"] = hold
        gain = chosen["discounted_expected_points_after_hits"] - hold["discounted_expected_points_after_hits"]
        # Do not advise a negative-value change just because reserve tie-breaking favoured it.
        if options.get("mode") == "transfers" and gain <= 0 and chosen["transfer_count"]:
            chosen = {**hold, "mode": "transfers", "bought": [], "sold": [], "hit_points": 0,
                      "transfer_count": 0, "remaining_bank": float(options.get("bank", 0)), "hold": hold}
            gain = 0.0
        chosen["modeled_gain_over_hold"] = round(gain, 3)
    return chosen
