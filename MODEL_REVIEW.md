# PortLab 0.2.0 model review

The second phase improves operational logic, prediction transparency and the visual replay. This release remains a local scenario laboratory. The available annual data support exploratory volume estimates; the supplied synthetic vessel schedules establish import and software behavior, rather than real operating accuracy.

## Annual prediction findings

| Series | Held-out fiscal years | Target years | Revised WAPE | Mean absolute error |
| --- | --- | ---: | ---: | ---: |
| Guam boxes | 2024–2025 | 2 | 1.216% | 1,026.5 boxes |
| Conley boxes | 2020–2025 | 6 | 21.501% | 28,210.2 boxes |
| Conley TEU | 2020–2025 | 6 | 21.267% | 49,541.3 TEU |

The previous selector promoted trend or mean methods too readily. Its errors were 2.945%, 25.780% and 25.938%, respectively. The revised selector keeps the last-year benchmark unless a challenger has at least three earlier selection targets, reduces their MAE by at least 5%, and wins on both latest targets. These thresholds are prototype heuristics. On the supplied observations the revised policy matches the last-year benchmark; it does not demonstrate a superior learned model.

WAPE is total absolute error divided by total actual volume in the test years. It is not a probability of correctness. Guam has only two targets. Conley misses large demand changes and needs better explanatory data. Policy changes were developed using this history, so the rerun remains retrospective development evidence. Freeze the policy and check later actuals before claiming independent accuracy.

FY2026 estimates follow the last supplied FY2025 actuals: 83,574 Guam boxes, 137,632 Conley boxes and 247,405 Conley TEU. Those fiscal periods have ended without supplied FY2026 actuals. The historical error envelope is not a confidence interval: earlier-only coverage was 0/1 for Guam and 3/5 for each Conley series. No synthetic annual values or inferred Guam TEU were added.

The Forecasts view and exported reports now expose actual-versus-predicted rows, MAE, RMSE, bias, benchmark results and selection reasons. Detailed reproducible calculations are included in `docs/model_review/` in both the desktop and source deliveries; the desktop also keeps a convenient `model_review/` copy.

## Operational logic findings

- Crane service now prefers feasible import discharge. It reserves yard and apron space together; ready exports can load when imports lack space. This corrects the earlier all-exports-first sequence and avoids holding one scarce space while waiting indefinitely for another.
- Later appointments can stage exports before the cutoff. Separate future-appointment accounting keeps the same uploaded scenario's earlier cargo path consistent when its horizon changes.
- Waiting and turnaround statistics distinguish completed observations from unfinished values truncated at the cutoff. Empty populations show unavailable values. The final stock-flow sample reflects settled events at the cutoff.
- Candidates must pass conservation/resource checks and avoid increasing unfinished cargo as well as unserved calls and call backlog. Improvement bounds adjust conservatively for comparing multiple candidates; physical pass results alone do not establish real causal benefit.
- Optional observed berth/departure times survive CSV/XLSX import and saved projects. A run reports paired timing errors, MAE, RMSE, bias and missing/censored coverage. These are same-run comparisons, not automatic calibration. The included schedules have no actual service-time observations, so operational prediction remains **not validated**.

The default 168-hour observation and 24-hour uninterrupted-pressure rule is retained. False pressure resets persistence. Scenarios rerun changed settings for the entire horizon; equipment delivery dates and investment costs are absent. There is no ROI estimate or automatic purchase.

## Visualization findings

The replay adds Operations, Overview, Top and Orbit cameras, berth focus, projected equipment labels, activity inspection, clearer tractor routes, color-coded handling and yard stacks representing a capped inventory sample. A live panel shows waiting/working calls, resource activity, trace coverage and yard counts. The queues/yard plot and vessel outcome table provide quantitative views beside the animation.

Replay ends at the actual simulated cutoff after cancellation. Cargo stays assigned throughout its recorded service interval. Missing or capped cargo traces show yard totals as unavailable outside retained coverage; the renderer does not silently carry the last known inventory forward. Resource intervals can still show retained equipment activity independently of a capped cargo trace.

The geometry remains schematic. A shared crane can be reassigned without an explicit travel-time model. Sampled 3D objects are not one object per physical box or machine. The supplied assets are retained, with bounded visual pools and configurable frame limits.

## Next improvements with the greatest value

1. Supply actual call arrivals, berth starts, departures, handled boxes, resource shifts and downtime. Calibrate handling, transport and dwell on one period, then test a separate later period.
2. Add dated annual or monthly actuals and explanatory demand variables. Keep independent box/TEU histories and test against simple benchmarks before adopting more complex prediction models.
3. Supply a berth/yard layout with dimensions and route lengths. These can improve visual placement and support explicit crane/tractor travel, truck-gate and yard-block behavior.
4. Add hatch/stow constraints and export readiness records where those affect sequencing. Cost and acquisition-delay data would be required for investment comparisons.

Method references: [rolling-origin forecast evaluation](https://otexts.com/fpp3/tscv.html), [simple benchmark methods](https://otexts.com/fpp3/simple-methods.html), [forecast accuracy](https://otexts.com/fpp3/accuracy.html), [WAPE limitations](https://robjhyndman.com/hyndsight/wape.html), [NIST Student-t critical values](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3672.htm) and [Bonferroni adjustment](https://www.itl.nist.gov/div898/handbook/prc/section4/prc463.htm).
