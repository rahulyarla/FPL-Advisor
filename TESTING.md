# Validation of v3.1

## Qualitative extension

Verified locally on 12 October 2026:

- 84 Python contracts cover the existing model/optimizer plus source access failures,
  timestamps, player attribution, negation/ambiguity, duplicate creators and cited
  origins, opinion limits, confirmed official next-match absences, contradictory
  reports, late/cached exclusion, finalized stable-code matching and chronological
  influence selection. Harmful qualitative influence remains zero.
- 31 HTTP/DOM checks pass against a temporary synthetic fixture, including source
  coverage, timestamped player evidence, trends/overlays, both qualitative forecast
  views, independent rival comparisons, source editing and text imports.
- Real public-source reads retrieved English captions for two recent FPL videos
  from each of the four requested channels. RSS posts from r/FantasyPL were readable;
  comment reads were blocked and reported as incomplete coverage.
- No finalized prospective qualitative outcomes exist yet. The default learned
  soft-influence gain is zero. Historical v3 metrics below do not establish the
  effectiveness of qualitative evidence.

The [release CI run](https://github.com/rahulyarla/FPL-Advisor/actions/runs/38233478664)
passed 84 contracts on Linux/Python 3.12 and Windows/Python 3.13, 31 HTTP/DOM checks
and 40 real Chromium desktop/mobile checks. Its screenshots were visually reviewed;
mobile tables scroll inside their panels. A subsequent small layout correction
gives the transcript-import fields full usable width on mobile and adds two browser
width guards.

## Earlier v3 statistical benchmark

Verified 12 October 2026 (Australia/Sydney).

- **61 Python regression contracts passed.** Includes the prior squad/rival contracts plus expanded temporal tests: same-GW leakage, long-form/team-xG cutoffs, complete and disjoint development/test periods, training strictly before holdout, recipe frozen before test, test-label changes cannot select the recipe, finalized-result matching, stable-code identity, pre-deadline timestamps, duplicate refresh handling, cached/offline replay exclusion, whole double-GW totals, corrupt-archive warnings, correction shrinkage/gates and no unvalidated long-horizon correction. Old Windows-encoded CSVs and decimal half-way cache rounding are covered.
- **23 HTTP/DOM dashboard checks passed.** Real loopback server with a temporary synthetic fixture: retained team/rival/graph flows plus v3 coverage/comparison rows, finalized-GW error charts, archived-prediction overlays and separate historical/live result downloads. No uncaught JavaScript errors.
- **A complete online refresh passed.** Eight full historical seasons plus finalized current GWs produce 202,738 observations. Understat matches all 380 fixtures per historical season. Current forecasts cover GW7–11, 667 players; only GWs 1–5 are finished and data-checked. GW6 provisional data is excluded.
- **The normal refresh correctly reused the validated model.** Native-source features and saved-data round trips have the same model fingerprint. Snapshot and fitted model fingerprints agree. Prices/flags are refreshed even when finalized training data is unchanged.
- **Full chronological model development passed.** 42,029 development predictions and 29,338 full-season test predictions, with per-position forecasts and actuals exported. Selected regular-player RMSE is 3.014 versus the fixed GB reference 3.042 and recent form 3.304. See [MODEL_DEVELOPMENT.md](MODEL_DEVELOPMENT.md) and `reports/model_checks.json`; this is an expanded historical benchmark, not a measured prospective v3 success.
- **The production-sized selection/scenario check passed.** All 667 players, five GWs, £100m budget, three synthetic rivals and 5,000 shared-player paired draws. Both squads were legal and all 1% per-GW/weighted point floors held. Synthetic rivals were not saved as real teams.
- **Real forecast ledger initialized.** Three files: original GW6 v1 forecast, GW7 v2 and current GW7 v3. Legacy timestamps/provenance are preserved. Their target outcomes are not finalized yet: zero evaluated live v3 rows and no active live correction are the correct release state.

Production verification:

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
  "records": 202738,
  "completed_gameweeks": [
    1,
    2,
    3,
    4,
    5
  ],
  "rivals": 3,
  "scenario_draws": 5000,
  "standard_weighted_points": 198.353,
  "rival_weighted_points": 198.311,
  "seconds": 6.52,
  "feedback_matched": 0,
  "feedback_gain": 0.0,
  "model_retrained_on_price_refresh": false,
  "manifest_matches_model": true,
  "archive_files": 3
}
```

## Desktop/mobile CI

[GitHub Actions run 38198739791](https://github.com/rahulyarla/FPL-Advisor/actions/runs/38198739791) **passed** for implementation commit `2cba585f5875574a218078f3d388c387b9169bdd`:

- 61 Python contracts passed on Linux/Python 3.12 and Windows/Python 3.13.
- 23 HTTP/DOM dashboard checks passed.
- 30 Chromium checks passed at desktop 1440×1000 and mobile 390×844, including v3 model comparisons, finalized-GW error charts, recorded forecast overlays, retained rival flows and page-width checks.

The captured desktop/mobile Model checks screenshots were visually checked in this session. They use the synthetic test fixture, not claimed live prediction results. On mobile, wide metric tables scroll within their panels; the page itself stays within the viewport. The two images are available in the run's `dashboard-screenshots` artifact.

Local Chromium download was unavailable; the successful GitHub runner supplied browser verification. The workflow continues to run these checks for source updates. No FPL password or personal squad is involved in these tests.

## Reproduce

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
# One terminal:
python tests/dashboard_fixture.py --port 8877
# Another terminal, from this folder:
npm install --no-save jsdom playwright
npx playwright install chromium
node tests/dashboard_dom.cjs
node tests/dashboard_browser.cjs
```

The synthetic fixture owns temporary reports and mocked FPL imports. It never changes real squads, rivals, forecasts or models. Node is needed only for optional developer/CI checks. The runtime uses scikit-learn 1.8.0, matching the bundled model; normal launchers use Python 3.12/3.13 and pip.
