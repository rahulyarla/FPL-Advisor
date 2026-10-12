# Model development, version 3

Measured 12 October 2026. This extends FPL-Auto's position-specific idea with a larger chronological development process, playing-time modeling, ensembles and an auditable forecast feedback loop.

## Data and features

The pool has **202,738 player–gameweek observations**, versus 88,215 in the supplied v2 snapshot. It includes eight complete seasons (2018–19 through 2025–26) and finalized 2026–27 GWs 1–5. GW6 was not data-checked at refresh and is excluded. The unusual 2019–20 restart numbering (GWs 39–47) is preserved and receives valid previous-match context.

There are **82 deadline-safe features**: earlier 3/8-GW form; selected 20-GW form; exponentially weighted points/minutes; playing and 60-minute consistency; shrunk attacking output per 90; points variability; missing-stat indicators; previous club/opponent goals, clean sheets and home/away scoring; verified Understat team xG/xGA. Stable FPL codes carry known form across seasons; seasonal IDs cannot merge unrelated players. Double-GW rows are aggregated before lagging. Missing outcomes are not manufactured as zero points.

Understat team histories matched all 380 fixtures per historical season using exact club aliases, calendar date, venue, final score and reciprocal xGA. Current-season matches are eligible only after FPL finalization. Context then uses strictly earlier GWs, even if the downloaded history contains later matches. Source failures produce missing indicators/warnings and leave the core pipeline usable. Only team match data is fetched; there is no fuzzy player-name join or season-total xG leak.

Old FPL exports can use Windows-1252; both UTF-8 and those historical names are supported. HTTP gzip responses are decoded with a size limit. Missing xG/defensive-contribution columns are explicitly marked instead of being treated as measured zero performance.

## Development, selection and test

The protocol was fixed before the expanded test was evaluated.

| Season | Gameweeks | Purpose |
|---|---|---|
| 2023–24 | 20–38 | Development, earlier period |
| 2024–25 | 1–12 | Development |
| 2024–25 | 13–25 | Development |
| 2024–25 | 26–38 | Development |
| 2025–26 | 1–12 | Frozen-recipe test |
| 2025–26 | 13–25 | Frozen-recipe forward test |
| 2025–26 | 26–38 | Frozen-recipe forward test |

**42,029 development predictions** and **29,338 test predictions** replace the smaller 12-GW development/6-GW test blocks. Each block trains only before its start, then remains fixed within that block. Later test blocks can train on earlier completed test GWs, reflecting periodic retraining. Test outcomes do not select candidates, history windows, weights or calibration gain. Features within a block use earlier observed GWs, as a one-GW forecasting benchmark would. Historical final schedules remain a limitation.

Eight methods per position are tried: recent form; the original gradient-boosting estimator; histogram boosting with long, recent and strongly regularized settings; a playing-chance classifier times conditional point regression; regularized linear regression; and the long-history boosting configuration without external xG. Settings compare 3/4/6/8-season windows and recency decay. Development also tries 25/50/75% blends of the three leading methods and correction gains of 0/0.5/1.

Selection minimizes **75% regular-player RMSE + 25% all-player RMSE**. Regular means prior three-fixture mean minutes of at least 45, decided before the outcome. The objective does not reward predicting zero for every unselected player while ignoring usable squad players. Boosting disables implicit random early-stopping splits. Seeds are fixed; parameters/specifications are part of the model fingerprint.

The GB reference uses FPL-Auto/v2's 110 trees, depth 3, learning rate 0.1, minimum leaf 15 and position-specific feature limits. It is refitted on each fold's latest three available seasons using the expanded lag history. It is an estimator reference, not the old saved model's predictions. The prior v2 report had already evaluated the last six GWs of 2025–26; this larger historical benchmark is therefore not described as a wholly new prospective experiment.

## Measured full-season results

Errors below are points per fixture for regular players; all methods use identical test rows.

| Position | Test rows | Selected RMSE | GB reference RMSE | Recent-form RMSE | Selected MAE |
|---|---:|---:|---:|---:|---:|
| GK | 748 | 2.659 | 2.689 | 2.913 | 2.059 |
| DEF | 3,146 | 3.069 | 3.106 | 3.392 | 2.330 |
| MID | 3,502 | 2.954 | 2.972 | 3.226 | 2.133 |
| FWD | 806 | 3.344 | 3.380 | 3.618 | 2.499 |
| Combined | 8,202 | 3.014 | 3.042 | 3.304 | 2.238 |

Combined regular-player RMSE falls by **0.94%** versus the GB reference and **8.79%** versus recent form. This is a modest gain over the reference. All-player RMSE is **1.920** versus **1.937** and **2.085** respectively. Detailed player-GW total errors, bias, all-player metrics and fold results are in `reports/model_checks.json`; individual predictions/actuals are in `reports/backtest_predictions.csv.gz`.

GK, MID and FWD select a 50/50 regularized-linear/playing-chance blend. DEF selects 75% regularized boosting and 25% playing-chance regression. Selected production windows contain four to six available seasons. Exact component rows/seasons are reported; pool rows must not be confused with every component's fitted row count. No historical residual correction won development in this release: all selected calibration gains are zero. The correction machinery remains available for future refreshes when it provides evidence of improvement.

## Learning from actual predictions

1. An online refresh records each player's forecasts, base forecasts, playing-time features, availability, stable code, model family, fingerprint, creation time and target deadlines. Only the latest pre-deadline snapshot for each origin GW/model family is retained; completed origin GWs are not overwritten with hindsight.
2. When FPL marks a GW finished **and** data-checked, the saved forecast is matched to the player's whole-GW actual total. Season, ID, code, forecast time and training cutoff must all agree. No result, code mismatch, provisional GW or post-deadline forecast means no evaluation row.
3. Actuals enter regular model retraining. Historical out-of-fold residuals can tune small minute-bucket biases using only earlier development folds. The historical test uses the development-frozen bias; production may subsequently learn from those now-past test errors without relabeling that refit as the tested model.
4. Live correction is separate. It requires at least three distinct finalized GWs of one-GW forecasts from version 3. Corrections use position/minute buckets, recency weighting, 300-row shrinkage and a ±0.5-point-per-fixture bound. Each validation GW uses residuals from earlier GWs. Gain 0/0.25/0.5/1 is checked; a correction needs at least 0.5% improvement in the balanced RMSE objective without increasing MAE. Otherwise gain remains zero.
5. Live corrections apply only to the next GW. Longer-horizon errors are displayed separately; one-GW calibration is not claimed to validate them. Per-player actuals train the model, but no single lucky haul creates an unrestricted personalized bias.

Genuine earlier forecasts from the supplied v1 ZIP (GW6, 3 October) and v2 snapshot (GW7, 11 October) were recovered with their original timestamps. The v1 prediction used a saved-data run and is explicitly tagged `cached`; neither legacy family can tune v3. There are no finalized actuals for these saved forecast targets yet, so the release has **no measured v3 live error or active live correction**. It does not invent historical forecasts for GWs 1–5.

The archive lives in `data/forecast_archive/`; preserve it when moving/upgrading the local folder. GitHub's scheduled workflow commits only this public player-forecast ledger, caches raw downloads/models, and offers reports plus archive as artifacts. Personal squad/rival files in `user/` are never included. GitHub schedules and repository settings still control whether a background run happens.

## Configuration and limits

Default `history_seasons_to_use` is 8 (up to 10), `model_search` is `standard`, `feedback_min_gameweeks` is 3 and `external_team_xg` is true. `quick` retains four candidate methods for faster experiments; use standard for the published comparison. At least three complete seasons are required. First full refresh can take several minutes; bundled reports load immediately. Price/flag-only updates reuse the model, but forecasts still update.

Historical rules, BPS and injury availability differ across seasons. Recency weights and era/missingness features mitigate this, but do not reconstruct today's rules in every old season. There are no historical deadline-status snapshots, premium expected-minutes feed, unverified future odds, cup workload source or news NLP model. Future weeks reuse current form and availability, and the one-GW historical test does not validate all five-GW totals. Scenario odds remain illustrative Gaussian exposure diagnostics.

Prediction error cannot be eliminated: actual football returns are noisy, and improvements on one historical season can fail in later seasons. Monitor the prospective ledger before treating a gain as durable.

## Primary research and implementation references

- [Original FPL-Auto](https://github.com/bentindal/FPL-Auto): position-specific gradient-boosting foundation.
- [Vaastav FPL dataset](https://github.com/vaastav/Fantasy-Premier-League): historical official FPL exports; original source license is retained.
- [Official FPL API](https://fantasy.premierleague.com/api/bootstrap-static/): current prices, availability, fixtures and finalized gameweek results.
- [Understat](https://understat.com/league/EPL) and its public league-data endpoint used by its own dashboard: team match xG/xGA. Optional; failure is reported.
- [Groos, OpenFPL (2025)](https://arxiv.org/abs/2508.09992): public-data position ensembles, multiple form horizons and prospective evaluation. This release is inspired by those principles and does not reproduce OpenFPL's exact models or commercial benchmark claims.
- [scikit-learn histogram boosting](https://scikit-learn.org/stable/auto_examples/ensemble/plot_hgbt_regression.html): regularization, efficient fitting and the caveat about random internal validation for time-series data.
- [GitHub cache documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching) and [workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data): cache accelerates fitting; committed forecast archives provide persistence independently of cache expiry.

Football-Data match statistics/odds were considered but not added. Closing odds may occur after FPL deadlines, and fixture/name joins plus actual historical availability would need validation. No commercial forecasts or paywalled data were copied.

The bundled estimator uses scikit-learn 1.8.0, pinned in requirements. Cache hashing tolerates decimal half-way parser noise; saved fixture fields retain only model context and FDR metadata, while source hashes/raw HTTP caches preserve provenance.
