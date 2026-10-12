# Qualitative evidence in FPL Advisor 3.1

The quantitative version 3 ensemble remains the statistical foundation. This
extension reads recent public football text and produces an inspectable, optional
alternative. It does not claim that popularity implies better player performance.

## Sources and actual access

Defaults are the verified YouTube channels [FPL Mate](https://www.youtube.com/@FPLMate),
[FPL Tom](https://www.youtube.com/@FPLTom), [FPL Raptor](https://www.youtube.com/@FPLRaptor),
[Let's Talk FPL](https://www.youtube.com/@LetsTalkFPL) and
[r/FantasyPL](https://www.reddit.com/r/FantasyPL/). Sources are editable in the dashboard.
Channel handles and IDs were checked against their public pages on 12 October 2026.

YouTube discovery uses the public upload feeds. Each enabled channel supplies up
to two recent relevant uploads by default. The reader attempts English captions
with `youtube-transcript-api==1.2.4`, preserving timestamps and whether captions
are generated. A genuine live check retrieved captions from all four creators.
UCL-only videos are excluded, as are clips explicitly targeting another FPL GW
or season. RSS discovery is limited to the platform's recent feed, so it is not
an exhaustive search of every video in a busy channel's archive.

Captions can be unavailable, disabled, blocked or mistranscribed. A failed caption
read becomes a labeled description-only item with zero forecast influence. Failed
reads have a six-hour backoff. The software does not download audiovisual files,
claim to have watched images, or analyse private/live chats automatically.

Reddit uses public RSS posts and attempts public comment feeds. An optional,
already-approved OAuth access token in `FPL_REDDIT_ACCESS_TOKEN` enables structured
post/comment reads. RSS is not assumed equivalent to complete API coverage: the
live environment allowed posts but denied comment reads. The coverage table shows
the actual number read, including zero, and any failures. A Reddit access token
may expire; the app does not obtain or refresh account credentials.

Public chat excerpts, inaccessible transcripts and official club reports can be
imported as text with an https source link, original publication time and optional
link to the original cited report. Imported text is stored under ignored `user/`.
Only source URLs on an explicit official-club/Premier League domain allowlist
receive the primary-source evidence weight. No arbitrary imported URL is fetched.
The collector also follows up to five cited links on those official domains per
refresh. An official article is usable only if its publication timestamp and
article body can be identified; otherwise the coverage panel records the gap.

The initial window is seven days, with a 72-hour exponential decay. Generated
captions receive a further 10% discount. A read older than 24 hours stops influencing
choices until refreshed; its original timestamp is preserved. User-pasted text
expires with the publication lookback rather than masquerading as a fresh fetch.

## What the text reader understands

The built-in reader uses transparent football phrase rules and unique player-name
matches, including selected unambiguous nicknames. It recognises injuries, fitness,
rotation, starting expectations, tactical roles, penalties/set pieces, captain
mentions, buy/sell opinions and price-movement discussion. Each claim records its
player, category, source, URL, caption timestamp, publication/fetch times and weight.

This is a conservative extraction system, not a general language model. Ambiguous
multi-player clauses, conditional claims, detected sarcasm and negations are held
for review with zero influence. Unresolved pronouns, misheard names and phrasing
outside the rule vocabulary can be missed. It does not turn a video title into a
spoken recommendation. Source cards expose the relevant passage for inspection.
Caption links point to the start of a grouped passage, approximately within 20 seconds.

Weights are evidence scores, not calibrated probabilities that a claim is true:
official domains start at 0.95, creators at 0.65, Reddit at 0.35, other imports at
0.30, then receive recency/caption discounts. These initial weights are assumptions.
Views, subscriber counts and historical creator ranks do not establish reliability.

Exact repeated passages are deduplicated. Multiple videos from one creator count
as one independent voice per claim category. Shared cited reports count as one
origin; one Reddit author has one vote per category. Cross-source copies with
changed wording and undisclosed original sources cannot always be identified.

## Combining the forecasts

There are three usable views:

| View | Behaviour |
| --- | --- |
| Stats only | Original statistical forecasts and official FPL availability |
| Stats + qualitative | Experimental bounded adjustments, with sources and reasons |
| Validated influence only | Soft adjustments multiplied by the learned gain, initially zero; fresh confirmed official next-match absences still apply |

All changes affect the next GW only. A positive opinion cannot manufacture playing
time or revive a player with zero forecast points. Soft opinion changes are limited
to 5% of the player's forecast and 0.25 points per fixture. Independent news
corroboration requires either an official source or at least two original voices
with evidence weight of at least 0.30. Reported second-hand claims cannot satisfy
the two-voice requirement alone.

Corroborated injury/rotation/starting news has small fractional effects. Existing
FPL fitness flags prevent the same fitness information being applied twice.
Contradictory fitness reports withhold the soft news adjustment. Corroborated tactical
roles and set pieces receive an additional small effect; conflicting roles withhold
it. The combined soft change is capped at -20%/+10% of next-GW points.

There is one factual exception: an official report published after the statistical
snapshot, explicitly ruling the player out of the next match/current GW, with
weight >=0.50 sets qualitative-view next-GW points and expected minutes to zero.
The latest official fitness/starting report takes precedence. This constraint is
excluded from soft-influence tuning. It does not infer a recovery date.

Captain recommendations, price-rise/fall discussion and disagreements remain
visible context. Price discussion is not a price-prediction model. Minutes displayed
for this layer are a recent-minutes proxy adjusted for news, not a separately
trained expected-minutes model. There is no calibrated season-rank probability.

The existing legal-squad optimizer runs on whichever forecast view is selected.
Both versions can be built together for lineups, transfers and wildcards. Rival
comparisons remain available within either version, including their point limits.
Each column has its own assumptions, so its higher displayed points alone do not
establish that it is a better team.

## Learning and preventing hindsight

`data/qualitative_archive/<season>/gwXX.json.gz` stores the latest genuine paired
forecast before each deadline. Both versions share the player code, baseline,
data/model cutoff, forecast timestamp and evidence capture. The archive includes
source-category scores, dates, hashes and extractor/model fingerprints; it excludes
raw text, creator/user names, source URLs, FPL entry IDs and private squad inputs.
Cached/offline runs and after-deadline captures cannot create prospective evidence.

Finalized actuals are matched by season, ID and stable player code. Missing outcomes
are not zero-filled. Repeated refreshes do not multiply observations. Point errors
are shown for all players and the affected subset; adjusted-minutes errors are
reported separately. Historical version 3 test metrics do not validate this layer.

Learning needs at least three finalized GWs and 30 outcomes affected by soft changes.
For each later GW, gains 0, 0.25, 0.5 and 1 are compared using only earlier GWs.
The selected gain is tested on the later GW. Automatic soft influence is enabled
only when chronological RMSE improves by at least 0.5% without increasing MAE,
and the full earlier record also supports the chosen next gain. Otherwise it stays
zero. Changing the extraction semantics after release should increment `VERSION`
so old and new experiments are not silently pooled.

Today’s discussions are not backfilled into the eight-season quantitative training
history. Useful historical qualitative training data would require genuinely dated,
verifiable archives. The first release has no finalized outcomes of this layer,
so no demonstrated accuracy improvement is claimed.

## Refresh and storage

The regular data refresh includes evidence; **Refresh evidence** skips quantitative
training. While the launcher stays open, its existing 12-hour refresh remains and a
deadline check runs every minute, requesting an extra refresh within four hours of
the next deadline. GitHub keeps twice-daily statistical refreshes and adds hourly
lightweight deadline checks; near a deadline those trigger statistical refreshes.
Public GitHub runs use `--skip-qualitative`; text evidence, caches and paired
qualitative archives are collected and retained locally. GitHub schedules can
be delayed. Use the manual refresh before confirming a transfer or captain.

Network reads have size/time limits and three source workers. The first 20 enabled
sources are read per refresh; additional configured sources are explicitly marked
as partial coverage. Source failures preserve the statistical forecast and show
coverage warnings. Raw transcript/document caches remain ignored under `data/`;
the local cache avoids repeated caption fetches. Imports and source settings
remain in ignored `user/`. Keep credentials in environment variables or GitHub
Secrets, never in code or source settings.

For an upgrade, preserve `user/`, `data/forecast_archive/` and
`data/qualitative_archive/`. Do not commit `user/`, raw caches, qualitative archives
or private report exports. The code release includes no generated qualitative
reports, scraped text or qualitative archives. The tracked statistical snapshot is
kept separate from the ignored local qualitative report.

## Primary implementation references

- [Transcript reader documentation](https://github.com/jdepoix/youtube-transcript-api)
- [YouTube caption-download API permissions](https://developers.google.com/youtube/v3/docs/captions/download)
- [Reddit Data API documentation](https://www.reddit.com/dev/api/)
