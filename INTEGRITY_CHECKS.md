# Anti-cheating checks: public team similarity

This feature measures how similar public FPL deadline teams have been. It does
not infer copying, identify an AI tool, establish intent, or determine guilt.
Popular players, shared advice, common constraints and chance can produce high
overlap. Use the measurements as review prompts alongside other evidence.

## Use the dashboard

1. Open **Anti-cheating checks**.
2. Paste entry IDs or official FPL team links into **List A** and **List B**.
   Separate them with newlines, commas, semicolons or spaces.
3. Select the first and last gameweek. Only deadlines that have passed in the
   current FPL season are offered. In-progress weeks are labeled.
4. Select squad overlap, starting-XI overlap or combined similarity.
5. Set the similarity threshold anywhere from **0 to 100%**. An optional second
   setting requires a minimum percentage of weeks to individually reach that threshold.
6. Click **Run checks**. Every A entry is compared independently with every B
   entry. Repeated IDs within a list are removed; self-comparisons are skipped.
7. Read the **Similarity matrix**, select a cell for its history, switch to the
   **Detailed table**, or download the CSV.

### Read the visual results

- **Similarity matrix:** List A is on the rows and List B on the columns. Each
  cell shows the chosen overlap measure, rounded to two decimals. A fixed sequential
  0–100% scale makes the same score the same colour across teams and filtered views.
  This is an overlap heatmap, not a Pearson correlation matrix or a cheating probability.
- **Outlined cells** meet all applied period settings, including the repeated-week
  requirement and complete coverage. The check mark provides a non-colour cue.
  **Partial history** is explicitly marked; **No data**, **Self / Skipped**, and
  filtered-out intersections are separate states, never substituted with 0%.
- **Period average / Latest gameweek** changes the values shown in the matrix and
  closest-reference bars. The chosen metric and GW range are printed above the grid.
  The latest value always means the last selected GW, even when that observation is
  missing. Outlines and the matching-pairs filter still use the full period rules.
- **Search**, **List A entry**, and **Only pairs meeting the period settings** filter
  the matrix, detailed table, overview cards and closest-reference bars together.
  The overview's highest average is the largest available period average in that view;
  partial coverage is labeled. The original period summary still describes all loaded
  pairs. The CSV exports the full applied report, independently of display filters.
- **Closest reference for each checked entry:** one horizontal bar per A entry shows
  its highest-scoring visible B entry. The chart shows the ten highest of these matches,
  not a mean across references. Focus on a particular A entry to inspect its best match.
  Bars use a fixed 0–100% scale. A dashed marker indicates the numeric threshold;
  coverage and repeated-week rules still decide whether period settings are met.
- **Selected pair:** click a matrix cell or bar, or use the table's **Timeline**.
  Cards show the chosen period average, latest value, coverage and longest streak.
  The line chart uses a fixed 0–100% axis and leaves gaps for unavailable observations.
  A heat strip labels every selected GW. Breakdown bars show squad overlap, XI overlap
  and captain agreement, each averaged over its own available observations. Expand
  **Player-by-player history and source links** for underlying players and public picks.

The matrix renders up to **12 A entries × 8 B entries** per page. Separate A/B page
controls cover all loaded entries without fetching more FPL data. Team labels stick
while the grid scrolls, and entry IDs remain visible when long names are shortened.
Cells support keyboard focus and activation; full names, IDs, coverage and values
are included in accessible labels. The detailed table retains its 100-row pages.

**Apply settings to loaded history** recalculates thresholds and metrics without
contacting FPL again. It also supports a smaller period within the loaded range.
A larger period or changed ID lists requires **Run checks**. **Cancel** stops new
reads and retains the previous completed report. Reads already in flight can take
up to the 20-second request timeout to stop.
**Refresh gameweeks** updates the available deadline range while preserving your
edited lists. Reopening the tab also updates metadata once its one-minute cache expires.

The interface supports many entries with background loading, three network workers,
deduplicated reads and 100-row table pages. A single run can produce up to 10,000
pairs; split larger comparisons into batches. Many IDs and a long period can take
time. Per-entry progress and coverage gaps are shown.

## Exact definitions

| Measure | Definition |
| --- | --- |
| Squad overlap | Common players divided by 15, multiplied by 100. Twelve shared players = 80%. This is not Jaccard similarity. |
| Starting-XI overlap | Common players in public pick positions 1–11 divided by 11, multiplied by 100. Scored points multipliers do not define starters. |
| Captain agreement | 100% for the same captain, 0% for different captains; unavailable if either captain is unknown. |
| Combined similarity | 60% squad overlap + 30% XI overlap + 10% captain agreement. These are explicit screening weights, not a trained cheating model. |
| Period average | Arithmetic mean of the chosen measure across comparable gameweeks, with equal weight per GW. |
| Weeks meeting threshold | Number and percentage of comparable weeks whose unrounded score reaches the chosen threshold. |
| Longest streak | Longest consecutive run of matching gameweeks; missing weeks break the streak. |
| Bench-order agreement | Fraction of the four bench slots occupied by the same player in the same slot. Context only. |
| Inferred squad changes | Jaccard overlap of incoming/outgoing player-ID sets between adjacent deadline squads. Not private transfer orders or timing. |

For example, an average threshold of 80% asks whether the chosen period's mean
score is at least 80%. A repeated-week requirement of 100% additionally requires
every comparable week to reach 80%. A repeated-week requirement of 0% adds no extra
condition. Threshold endpoints are inclusive and calculations use full precision;
display percentages are rounded to two decimals.

**Threshold met** requires complete coverage of the requested period, a qualifying
average, and the optional repeated-week requirement. A pair with only one available
week out of five is labeled **Insufficient coverage**, even if that week is 100%.
Missing data is not filled with zeros or silently treated as evidence of agreement.
The table still exposes the average of available weeks alongside its coverage.

Unknown captain data makes the combined measure unavailable for that week, while
the squad and XI measures can remain usable. Vice-captain agreement is retained
as a separate diagnostic in the result data. Identical squads with different
captains have a combined score of 90%, rather than a claimed probability of cheating.

The timeline shows shared and differing squad players, captain choices, chips,
inferred incoming/outgoing players and links to each public FPL deadline squad.
Scores and points totals are not used as a substitute for player overlap.

## Special cases and limitations

- Free Hit changes are temporary. Inferred squad-change similarity is unavailable
  when either adjacent squad used Free Hit. Wildcard changes remain visible.
- If neither entry changed its squad, the change-similarity value is unavailable,
  not a misleading 100% matching transfer decision.
- Entries that joined later, unavailable weeks, malformed public responses and
  unknown captain flags have explicit coverage gaps.
- Player IDs are compared within the current official season. The API does not
  provide an arbitrary historical-season selector for this feature.
- A double or blank gameweek still represents one deadline decision and one time
  observation; it does not receive extra weight because more matches were played.
- The feature cannot see unsubmitted drafts, future transfers, private messages
  or the reasoning behind a manager's choices. It does not apply a league's voting
  rules or automatically accuse, report, contact or sanction anyone.

## Sources, refresh and storage

The feature reads official public FPL bootstrap data, entry profiles and
`entry/<id>/event/<gw>/picks/`. It loads one earlier gameweek when available to
describe adjacent squad changes. It works independently of model forecasts, saved
rivals and the age of the main forecasting snapshot.

Metadata is cached for one minute; entry histories are cached privately for 15
minutes and scoped to the season. **Fetch fresh public history** bypasses this
short cache. HTTP 403/429 stops public reads instead of attempting an access bypass.
Response sizes and network concurrency are bounded.

Entry lists, cached responses, snapshots and reports live only in ignored
`user/integrity/`. CSV downloads contain numeric IDs and measurements rather than
user-controlled team names in spreadsheet cells. This avoids spreadsheet formula
injection. No individual comparison result or entry list is included in the code
release, public forecast archives, or scheduled GitHub refreshes.
