# FPL Advisor v3

A working local FPL dashboard with trained player forecasts, a best XI and captain,
transfer comparisons, and a wildcard builder. It follows the position-specific
gradient-boosted regression approach in [Benjamin Tindal's FPL-Auto](https://github.com/bentindal/FPL-Auto).
The application code is a new implementation, with a current-data pipeline and a
different squad optimizer. It is not a claim to reproduce that project's season scores.

## What's new in v3

- **Larger development data:** eight complete historical seasons plus finalized current GWs, currently 202,738 player–GW observations. Validation has 42,029 predictions across two seasons; the separate historical test covers all 38 GWs of 2025–26 with 29,338 predictions.
- **Tuned position ensembles:** compare eight candidate methods and blends, including regularized boosting, a regularized linear model and a model of playing chance followed by points conditional on playing. Development chooses the useful history window instead of assuming that older data always helps.
- **Error learning:** retrain on new finalized results; tune shrinkage corrections using earlier out-of-fold prediction errors. Save real forecasts before deadlines and compare them with actual FPL points. Small live corrections require at least three finalized version 3 GWs and must pass a chronological accuracy check.
- **More information:** longer form, playing-time consistency, attacking output per 90, clean-sheet context and verified Understat team xG. Missing old statistics are marked and older scoring eras are downweighted.
- **Visible accuracy:** Model checks shows RMSE, MAE, the estimator reference, data coverage and the live forecast-error history. Player graphs can overlay recorded pre-deadline predictions. Historical predictions/actuals are downloadable.

On the same full-season regular-player test, selected RMSE was **3.014** points per fixture versus **3.042** for the fixed gradient-boosting reference and **3.304** for recent form. The gain over the reference is about **1%**, with lower RMSE in every position. This is a historical result, not a guarantee of future improvement. See [MODEL_DEVELOPMENT.md](MODEL_DEVELOPMENT.md) for the protocol, position results and limits.

Open **Model checks** after starting the dashboard. The live error table starts empty until a saved forecast's GW is finalized. Historical backtests are not presented as predictions that were made live.

## Rivals and graphs retained from v2

- **Optional rivals:** add any number of FPL entry IDs or official team URLs, including bulk imports. Include/exclude and remove individual rivals. Their snapshots are saved by season in `user/rivals.json` and refreshed with the local dashboard's scheduled data update.
- **Two recommendations:** the usual points-first plan remains available. Check **Also build a version that considers my rivals** to calculate both versions, then switch between **Standard plan** and **With rivals**. This works for lineups, transfers and wildcards.
- **Secondary influence:** choose **Protect a lead** for shared exposure or **Chase upside** for differentials. Influence defaults to 8% and cannot exceed 15%. The alternative may sacrifice at most 1% of predicted points by default, configurable from 0% to a hard maximum of 2%. The limit applies to every forecast gameweek and to the weighted horizon, after your hits. Zero influence returns the exact standard plan.
- **Player graphs:** a points graph for every squad player; inspect actual/forecast points, minutes, xG and xA; compare up to three players and select a recent window. A squad fixture heatmap shows projected points and opponents, including blank and double weeks.
- **Rival context:** squad ownership, deadline captains, used chips, finalized cumulative points and paired next-GW scenarios. All included rivals receive equal weight, so adding more teams cannot increase the rival influence.

### Use the rival option

1. Import/check your own team on **My team & transfers** as before.
2. Open **Rivals**, paste entry numbers or official links, and click **Add rivals**. You can paste multiple lines or comma-separated IDs.
3. Choose which rivals to include, your protect/chase preference, influence and allowed point sacrifice.
4. On **My team & transfers** or **Wildcard builder**, check **Also build a version that considers my rivals**, then calculate normally.
5. Compare the two columns and use **Standard plan** / **With rivals** to view each XI, captain and squad. Identical plans are a valid result when a different plan is not worthwhile within the limits.
6. Open **Player graphs**, or click a recommended player, to inspect the underlying form and fixtures.

### What the rival calculation means

Rival teams do **not** become features in the player points model. Subtracting a fixed rival score from your expected score would not change which squad maximizes expected points. The optional optimizer instead adds a small, explicit exposure preference: it favors covering popular rival selections when protecting, and lower shared exposure when chasing. Hard point constraints keep this preference secondary.

Rival future XIs/captains are projected by enumerating legal formations within their last public squads. Future transfers, paid hits, chip choices, autosubs and vice-captain fallback are unknown. A previously active Triple Captain or Bench Boost is not assumed to repeat in the next gameweek.

The comparison runs 5,000 reproducible, paired scenarios using historical position RMSE. A shared player gets the same simulated points in all teams. Other player and gameweek errors are independent Gaussian approximations. **Scenario percentages are not calibrated probabilities of beating a rival or winning your league.** They are diagnostics of next-GW relative exposure and risk, rather than season-rank forecasts.

Charts use finalized current-season results only, preserve double-gameweek totals, and keep actuals separate from predictions. Unrecorded observations are not filled with invented scores. Chart values can be read by hovering or keyboard focus; chart comparisons cover a selected recent window, while their totals table covers the finalized season.

The GitHub folder layout has been restored: package code is under `fpl_advisor/`, web assets under `fpl_advisor/static/`, tests under `tests/` and workflows under `.github/workflows/`. Download the entire repository ZIP; keep these folders intact.

## Updating an earlier copy

Close the old adviser's command window first. Download this repository with
**Code → Download ZIP**, extract the entire ZIP, open the extracted project
folder, and double-click **START_WINDOWS.bat**. Your saved squad uses the same
browser address and season. If updating a v2 copy that already has saved rivals,
copy its `user/` folder and `data/forecast_archive/` folder into the new project before starting it. The forecast archive preserves your accuracy history. Never upload
that folder to GitHub.

## Start on Windows

1. Install **Python 3.12 or 3.13 (64-bit)** from [python.org](https://www.python.org/downloads/) if needed.
2. Extract the entire ZIP into a normal folder. Do not run the launcher inside the ZIP.
3. Double-click **START_WINDOWS.bat**. The first launch installs the required Python packages.
4. Your browser opens the dashboard at **http://127.0.0.1:8765**. Keep the terminal window open.

The project includes trained models, a saved dataset and forecasts, so the dashboard
can show its initial snapshot immediately after setup. The launcher starts a fresh
online update in the background, then refreshes every 12 hours while it stays running.
Use **Refresh data & model** any time, especially before the FPL deadline. Turning
off the computer or closing the terminal stops these scheduled local updates.

## Use it with your team

1. Open **My team & transfers**.
2. Copy the entry number from your FPL Points page, for example the `123456` in
   `https://fantasy.premierleague.com/entry/123456/event/5/`, and click **Import team**.
3. Confirm the team name and all 15 players. Public picks are the last deadline's
   snapshot and may miss later transfers. You can add/remove players manually.
4. For lineup recommendations, click **Best XI & captain**.
5. For transfers, enter your **actual current bank, remaining free transfers and
   every selling price**, then check the confirmation box. Buying prices are not
   necessarily your selling prices. Imported bank is also a deadline snapshot.
6. Click **Compare transfers with holding**. The result shows purchases, sales,
   hits and the modeled gain over keeping your squad.
7. On **Wildcard builder**, use a £100m reference budget or check the option to
   use your actual current squad selling value and bank. Optional player locks
   let you keep particular picks.

The app does not submit transfers or activate chips in your FPL account. It uses
public, read-only data and requires no FPL password. Your editable squad is stored
in this browser's local storage. Rival snapshots are saved locally in `user/rivals.json`;
this folder is ignored by Git and excluded from the public repository. Public entry IDs are sent only to official FPL
endpoints when you explicitly import a team.

## Data updates

[Vaastav's repository](https://github.com/vaastav/Fantasy-Premier-League) stopped
weekly updates after 2024–25. Its README now describes three major snapshots:
start of season, after the January transfer window, and end of season. Simply
pulling that repository every day would not supply all the newest results.

This project combines:

- **Vaastav history:** automatically finds the latest three complete prior
  seasons, downloads `gws/merged_gw.csv`, `players_raw.csv` and `fixtures.csv`,
  and checks the files again on refresh using conditional HTTP requests.
- **Vaastav current-season snapshot:** checks its available gameweeks and
  retains useful historical player metadata.
- **Official current FPL:** refreshes players, prices, clubs, positions,
  availability, future fixtures, and results from finalized gameweeks.
  Official finalized results take precedence over the older repository snapshot.
- **Historical club changes:** uses the real fixture sides, and resolves
  current-season transfers with official player histories where necessary.

A gameweek must be `finished` **and** `data_checked` before its results enter
training. In-progress gameweeks are excluded. Failed downloads stop the refresh;
the previous forecast snapshot remains available with its original timestamp.

The current FPL season and next future deadline are detected from the API. There
are no hard-coded player IDs or gameweek numbers in the pipeline. Numeric IDs are
scoped to their season; stable FPL player codes carry past form across seasons.

## Model and validation

All performance features are lagged by a whole gameweek. Double-GW player records are summed before lagging and normalized per scheduled fixture. Same-GW results, scraped `xP`, final-season totals, current ownership and current prices are excluded from historical model inputs. The forecast predicts each scheduled fixture and sums its points into a GW total; blank weeks receive zero.

Development uses four rolling blocks across two historical seasons. It compares eight candidate methods, short/long history windows, blends and small residual corrections. Each fold trains strictly before its first GW; no player rows are randomly split. The recipe is written to `models/frozen_recipe_POSITION.json` before any test prediction. Testing covers the next full season in three forward blocks; earlier test results can enter later-block model fitting, but never alter the frozen recipe or its test calibration.

Production refits the selected history windows on the latest finalized results. Out-of-fold historical errors supply conservative residual calibration if it won development. The current selected models blend a playing-chance model with regularized linear or boosting models. Their windows cover four to six available seasons; the eight-season pool remains available to the search. Model checks reports pool size, while `reports/model_checks.json` records the exact component training rows/seasons.

Every genuine online refresh saves the latest pre-deadline forecast for that origin GW in `data/forecast_archive/`. Finalized actuals are matched by season, ID and stable code. Repeated refreshes count once per player/GW/horizon. The application retains forecasts for all horizons; live corrections are learned from one-GW forecasts of the current model family and apply only to the next GW. A correction must improve later-GW RMSE without increasing MAE and is capped at 0.5 points per fixture before its learned gain and availability adjustment. Legacy saved forecasts are auditable but cannot tune version 3.

Retraining is automatic when finalized features/targets, tuning specifications, model version, search mode or scikit-learn version change. A price-only refresh reuses the model. `reports/backtest_predictions.csv.gz` contains development/test predictions and actuals; `reports/forecast_results.csv` appears once a saved forecast has a finalized result. Detailed design and research: [MODEL_DEVELOPMENT.md](MODEL_DEVELOPMENT.md).

## Squad decisions

SciPy's mixed-integer optimizer jointly chooses a 15-player squad, legal starting
XI and captain for each gameweek in the horizon. It enforces:

- 2 GK, 5 DEF, 5 MID and 3 FWD;
- at most three players per club;
- 11 starters, one goalkeeper, at least three defenders, two midfielders and one forward;
- a captain among the starters, using that gameweek's forecast;
- an integer-tenths budget that accounts for each owned player's actual selling price;
- a configurable transfer cap and four points per transfer beyond your remaining free allowance.

The default horizon is five GWs with weights `1, 0.8, 0.64, 0.512, 0.4096`.
Transfers are compared against the best legal lineups for the held squad, after
hits. Bench cover has a small 2% tie-breaking weight, rather than counting all
bench points as if Bench Boost were active. Solver results are validated before
being returned; a time-limited feasible result is labeled and its gap recorded.

## Important practical limits

The forecasts use the latest observed form for later weeks. Official availability
probabilities are held constant across the horizon, so check injury recovery news.
New players can have little usable history. Older rule eras do not score identically;
a scoring-era feature helps but does not reconstruct old stats under today's rules.

Historical fixture assignments are final published schedules, not archived
deadline snapshots. The evaluation measures point prediction per fixture, not
mini-league survival, a full season of executed transfers, or a guaranteed rank.
The optimizer does not simulate future transfers, vice-captain fallback or exact
automatic substitutions, and does not value a saved free transfer's future option.
Small expected gains can be overwhelmed by normal model error.

## Other ways to run

macOS/Linux:

```bash
chmod +x start.sh
./start.sh
```

Manual setup and commands, from this project folder:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt

python -m fpl_advisor serve --open --auto-hours 12
python -m fpl_advisor run
python -m fpl_advisor run --cached
python -m fpl_advisor run --offline
python -m fpl_advisor recommend --mode wildcard --budget 100
python -m fpl_advisor watch --interval-hours 12
python -m unittest discover -s tests -v
```

`--cached` uses the bundled normalized snapshot. `--offline` rebuilds from the
download cache, if an earlier online refresh has populated it. Both are saved-data
modes, and are not claims of current prices. To calculate personal transfers from
the command line, fill a copy of `team.example.json` and pass `--team-json`.

## Run automatically on GitHub

`.github/workflows/refresh.yml` is ready for a **repository you own**. It is not
enabled on Benjamin Tindal's repository and no changes were pushed there.

1. Put this project's source files in your own GitHub repository's default branch.
   The included normalized snapshot/models can be kept. New runtime files are ignored by Git; the workflow deliberately commits only the public forecast archive. Never commit `user/`.
2. Open **Actions → Refresh FPL forecasts → Run workflow** for the first run.
3. The workflow then runs at **05:17 and 17:17 UTC** and produces downloadable
   `fpl-forecasts` artifacts containing the forecast CSV, snapshot, model checks
   and data manifest, plus the forecast archive (90-day retention subject to repository settings). It caches downloads/models and commits only public player forecasts in `data/forecast_archive/` so feedback survives separate runs. It does not write team changes to FPL.

The workflow is a background report generator, not a hosted interactive dashboard.
To see a scheduled result locally, extract its `reports/` directory into this
project, replacing the older reports. GitHub schedules may be delayed and require
Actions to remain enabled; review the run status before relying on a deadline update.

## Files and attribution

`fpl_advisor/` contains the data client, feature generation, model, optimizer, CLI
and dashboard. `data/manifest.json` records source URLs, SHA-256 hashes, snapshot
time, gameweek coverage and warnings. The ZIP includes trained models and a saved
data snapshot, plus a reference wildcard in `reports/example_wildcard.json`. Optional team xG is sourced from [Understat](https://understat.com/league/EPL); its source URLs and hashes are recorded in the manifest.
That reference squad is a £100m example, not your imported team.

Vaastav Anand's source license is preserved in `VAastav_LICENSE.txt`. The data is
owned by its original providers, as noted in that license. FPL-Auto's algorithm
and author are credited; its source files are not bundled or copied here.

## Dashboard research and testing

The added views take inspiration from LiveFPL's ownership context, FPL Review's draft comparisons and Fantasy Football Hub's player comparison tools. Source links and the resulting choices are in [DASHBOARD_RESEARCH.md](DASHBOARD_RESEARCH.md). No paid projections or third-party website code are bundled.

See [TESTING.md](TESTING.md) for verified checks and limitations. `Test FPL Advisor` runs Python contracts on Linux and Windows, plus HTTP/DOM and desktop/mobile Chromium checks on GitHub Actions. Your normal launcher needs Python only; Node dependencies are used solely by optional developer/CI tests.
