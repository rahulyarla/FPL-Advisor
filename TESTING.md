# Verification for this delivery

Data and trained-model snapshot: 3 October 2026.

- 88,215 player-gameweek training observations across three complete historical
  seasons and finalized 2026–27 GW1–5.
- 667 current players forecast for GW6–10.
- Vaastav's current `merged_gw.csv` snapshot reached GW1; the official refresh
  supplied the subsequent finalized gameweeks.
- A second online update succeeded. Re-running the equivalent saved data keeps
  the existing validated model, including after a CSV serialization round trip.
- 18 automated Python contract checks passed, covering same-GW leakage, double
  GWs, historical fixture boundaries, unfinished results, seasonal ID reuse,
  stable player codes, budgets, club limits, legal formations, captaincy,
  transfer hits, selling prices and data-change fingerprints.
- Local HTTP routes and real £100m optimization passed. The reference wildcard
  left £1.1m and forecast 62.1 points including captaincy for GW6. This is a
  generic reference squad, not a recommendation based on your personal team.
- Eight interface/HTTP interaction checks passed in a DOM environment: loading,
  searching, squad editing, wildcard result rendering, lineup/captain rendering,
  financial confirmation, transfer comparison, and model/import validation.
- Dashboard JavaScript syntax was checked.

Visual browser validation on desktop/mobile was not completed: the available
headless browser crashed in this execution environment. The DOM checks do not
verify pixel layout. No live FPL transfer, chip or lineup change was made.

## Separate test block

The method for each position was selected on 2025–26 GW21–32 and tested on
GW33–38, with training restricted to earlier gameweeks in each block. These
figures concern players whose pre-game recent average minutes were at least 45.
The target is gameweek points divided by scheduled fixture count.

| Position | Selected method | Test RMSE | Recent-form baseline RMSE |
|---|---|---:|---:|
| GK | Gradient boosting | 2.52 | 2.72 |
| DEF | Gradient boosting | 2.80 | 3.16 |
| MID | Gradient boosting | 2.87 | 3.21 |
| FWD | Gradient boosting | 3.58 | 3.77 |

Full results, counts, development blocks and feature importances are in
`reports/model_checks.json`. Historical fixture schedules are final schedules,
so this is not a reconstruction of every piece of deadline-time information.
