# Dashboard research

Reviewed on 12 October 2026 (Australia/Sydney). These are feature references,
not claims that this project reproduces commercial projection accuracy.

| Reference | Useful pattern | Implemented here |
|---|---|---|
| [LiveFPL](https://www.livefpl.net/) | Ownership, differentials, threats and league context | Optional rival list, squad ownership, captain context and shared-outcome scenarios |
| [FPL Review: Draft Management](https://docs.fplreview.com/the-model/planner-interface/draft-management/) | Compare alternative plans and projected points by GW | Standard / With rivals versions, per-GW plans, unchanged player projections and a visible point-sacrifice limit |
| [Fantasy Football Hub: Player comparison](https://www.fantasyfootballhub.co.uk/whats-new/player-comparison-and-opta-stats-now-in-the-app) | Compare two or three players, recent windows and player profiles | One-to-three player graphs, recent-GW filtering, xG/xA, minutes, points and per-90 summary |

The comparison module treats rivals as a secondary decision preference. Lower
ownership can increase relative variance without improving expected points.
The 1% default point limit, 2% ceiling and optional switch preserve the main
points objective. The scenario model is explicitly approximate, and cannot
predict unknown rival transfers or their future chip decisions.

SVG charts run locally without external chart scripts. The project uses official
FPL public statistics; paid Opta tools and commercial models are not copied.
