# Validation of v2

Verified on 12 October 2026 (Australia/Sydney).

- **39 Python regression contracts passed:** original leakage, identity, budgets, positions, formations and captain tests; rival point limits (including hits), disabled/duplicate rivals, 75-entry normalization, shared-player cancellation, blank-week outcomes, season-scoped persistence, failed-refresh preservation and finalized chart data.
- **20 HTTP/DOM dashboard checks passed** against a real loopback server and a temporary fixture: all assets/tabs load, import fills 15 players, every squad player has a graph, three-player comparisons show actuals and forecasts separately, fixtures render, IDs/links import and deduplicate, own entry is rejected, rival exclusions/removals persist, both versions work for lineups/wildcards/transfers, and economics confirmation remains required. No uncaught JavaScript errors occurred.
- **An online data refresh succeeded:** the current report targets GW7–11 of 2026–27 with 667 players. FPL marked only GW1–5 as both finished and data-checked, so GW6 provisional results were excluded. Training records were unchanged, and the validated fitted models were correctly reused.
- **The production-sized optimizer/scenario check passed:** all 667 current players, a £100m budget, five forecast GWs, three synthetic rival squads and 5,000 paired draws. Both squads were legal, the rival squad respected the budget, and every GW plus the weighted horizon stayed within the 1% point floor. These synthetic rivals were used only for QA, not saved as real rival teams.

Production check result:

```json
{
  "players": 667,
  "forecast_weeks": [
    7,
    8,
    9,
    10,
    11
  ],
  "rivals": 3,
  "scenario_draws": 5000,
  "standard_weighted_points": 209.298,
  "rival_weighted_points": 208.787,
  "remaining_bank": 0.4,
  "squad_changes": [
    61,
    68,
    112,
    115,
    169,
    176,
    236,
    332,
    356,
    426,
    427,
    651
  ],
  "solver_status": "optimal_within_tolerance",
  "seconds": 0.98
}
```

## Rendering and CI

[GitHub Actions run 38187325069](https://github.com/rahulyarla/FPL-Advisor/actions/runs/38187325069) **passed** for implementation commit `82e201313703069e1bc5c8fe88cb5075e8914469`:

- 39 Python contracts passed on Linux/Python 3.12 and Windows/Python 3.13.
- 20 HTTP/DOM dashboard checks passed.
- 22 Chromium checks passed at desktop 1440×1000 and mobile 390×844, including graphs, comparisons, rival imports, version switching and page-width checks.

Local Chromium installation was blocked by a failed/truncated browser download.
The successful GitHub runner supplied the actual browser verification. These
are automated checks, not a claim of a separate human pixel-perfect review.

`.github/workflows/test.yml` runs the contracts on Linux/Python 3.12 and
Windows/Python 3.13, then the DOM checks and `dashboard_browser.cjs` in Chromium
at 1440×1000 and 390×844. That browser suite checks all squad charts, player
comparisons, rivals, version switching, keyboard-readable chart values and page
width. See the GitHub Actions run for its actual status; configured checks are
not claimed as passed before the run completes.

## Reproduce developer checks

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
# In one terminal:
python tests/dashboard_fixture.py --port 8877
# In another terminal, from this project:
npm install --no-save jsdom playwright
npx playwright install chromium
node tests/dashboard_dom.cjs
node tests/dashboard_browser.cjs
```

The fixture uses its own temporary reports and mocked FPL imports. It does not
change real saved squads, rivals or models. Node is unnecessary for normal use.
