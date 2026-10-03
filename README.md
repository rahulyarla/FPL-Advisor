# FPL Advisor

A working local FPL dashboard with trained player forecasts, a best XI and captain,
transfer comparisons, and a wildcard builder. It follows the position-specific
gradient-boosted regression approach in [Benjamin Tindal's FPL-Auto](https://github.com/bentindal/FPL-Auto).
The application code is a new implementation, with a current-data pipeline and a
different squad optimizer. It is not a claim to reproduce that project's season scores.

## Start on Windows

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/) if needed.
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
in this browser's local storage. Public entry IDs are sent only to official FPL
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

- One `sklearn.ensemble.GradientBoostingRegressor` for GK, DEF, MID and FWD.
- The original core settings: 110 trees, learning rate 0.1, maximum depth 3,
  and position-specific maximum features of 5/10/20/10.
- Added regularization: minimum 15 samples per leaf; reproducible random seed.
- Rolling averages of prior points, minutes, goals, assists, xG/xA, saves,
  defensive contribution and other statistics over 3 and 8 recorded gameweeks.
- Team and opponent context uses results strictly before the predicted GW;
  home/away and scheduled match counts are fixture metadata.
- Double-gameweek rows are aggregated before lagging. A later match in the
  same GW cannot supply form for an earlier match in that GW.
- Scraped `xP`, same-GW performance and final-season cumulative totals are
  excluded from the feature list. Prices/ownership are shown in the UI but are
  not retrospective training features.
- Points and past statistics are normalized per scheduled fixture. Predictions
  are made for each upcoming fixture and summed into gameweek totals. Blank GWs
  get zero points and double GWs sum both fixtures.

The latest complete historical season is used for forward validation:

| Block | Purpose | Model training |
|---|---|---|
| GW21–26 | Development | Only records before GW21 |
| GW27–32 | Development | Only records before GW27 |
| GW33–38 | Separate test | Only records before GW33 |

Development scores select gradient boosting or a simple recent-form baseline for
each position. The baseline is `0.65 × previous-three-GW mean points + 0.35 ×
previous-eight-GW mean points`, with a training-only position prior for new players.
Method selection uses players with pre-GW recent average minutes of at least 45
where enough such records exist. The untouched final block reports the selected
method's RMSE and MAE. After that evaluation, the selected method is fitted on all
available completed data for live forecasts. Historical test statistics are not
claimed to be independent live forecasts for this season.

The dashboard reports both the method and its errors; the full per-position,
per-block results and feature importances are in `reports/model_checks.json`.
Models are retrained when the normalized feature/target data changes or the
scikit-learn version changes, rather than needlessly on every price refresh.

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
   Generated `data/`, `models/`, `reports/` and `user/` files are ignored by Git.
2. Open **Actions → Refresh FPL forecasts → Run workflow** for the first run.
3. The workflow then runs at **05:17 and 17:17 UTC** and produces downloadable
   `fpl-forecasts` artifacts containing the forecast CSV, snapshot, model checks
   and data manifest. It does not write team changes to FPL.

The workflow is a background report generator, not a hosted interactive dashboard.
To see a scheduled result locally, extract its `reports/` directory into this
project, replacing the older reports. GitHub schedules may be delayed and require
Actions to remain enabled; review the run status before relying on a deadline update.

## Files and attribution

`fpl_advisor/` contains the data client, feature generation, model, optimizer, CLI
and dashboard. `data/manifest.json` records source URLs, SHA-256 hashes, snapshot
time, gameweek coverage and warnings. The ZIP includes trained models and a saved
data snapshot, plus a reference wildcard in `reports/example_wildcard.json`.
That reference squad is a £100m example, not your imported team.

Vaastav Anand's source license is preserved in `VAastav_LICENSE.txt`. The data is
owned by its original providers, as noted in that license. FPL-Auto's algorithm
and author are credited; its source files are not bundled or copied here.
