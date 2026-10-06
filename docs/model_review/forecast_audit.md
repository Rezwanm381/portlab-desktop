# Reported annual forecast audit

This audit uses 26 reported annual observations through FY2025. No synthetic annual targets, inferred Guam TEU, or vessel-level operating predictions enter the scores.

## Heldout errors

| Series | Target FYs | Targets | Guarded-policy WAPE | Last-year WAPE | MAE | Historical v0.1 policy WAPE |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Guam boxes | 2024–2025 | 2 | 1.216% | 1.216% | 1,026.5 | 2.945% |
| Conley boxes | 2020–2025 | 6 | 21.501% | 21.501% | 28,210.2 | 25.780% |
| Conley TEU | 2020–2025 | 6 | 21.267% | 21.267% | 49,541.3 | 25.938% |

WAPE = 100 × total absolute error / total actual volume in the target years. It is an error ratio, not a probability of correctness. Signed error is prediction minus actual; MAE/RMSE are in boxes or TEU. Guam has only two outer targets. Conley misses the sharp FY2022 drop and FY2023 recovery.

The reviewed policy matches the last-year benchmark on these observations. The former selector could promote a challenger from one earlier validation error and performed worse on every supplied series. The updated three-inner-target, 5%-MAE-margin and two-recent-win controls are heuristic prototype guards. They were developed against this history, so replay is retrospective development evidence, even though each fold hides its own target.

## Following-data-year estimates

| Series | Latest actual FY | Estimate FY | Latest method | Point | Historical error envelope | Prior-only hits |
| --- | ---: | ---: | --- | ---: | --- | ---: |
| Guam boxes | 2025 | 2026 | last_year | 83,574 | 81,890–85,258 | 0/1 |
| Conley boxes | 2025 | 2026 | last_year | 137,632 | 75,977–199,287 | 3/5 |
| Conley TEU | 2025 | 2026 | last_year | 247,405 | 139,519–355,291 | 3/5 |

FY2026 follows the latest supplied FY2025 observation. Its fiscal periods had already ended at the October 2026 review, without supplied actual FY2026 totals. These are historical following-period estimates, not observed FY2026 totals or FY2027 forecasts.

The envelope uses the largest earlier outer absolute error, with a zero floor. Each heldout envelope is built only from still-earlier errors; the first target has no envelope. The small hit counts demonstrate that this is not a confidence interval, calibrated coverage or a guarantee. The latest band describes policy errors rather than independent uncertainty for a newly selected method.

## Protocol and source boundaries

Outer targets begin after four observed years. All fitting and selection use strictly earlier observations. Fixed-method rows use identical outer years, but their retrospective rankings do not choose the current method. The custom damped_trend adds half a recent OLS slope and is not fitted Holt damped exponential smoothing.

Zero observations are retained; all-zero targets give undefined WAPE while MAE/RMSE remain available. Gaps stop forecasting. Fewer than three years produce no estimate; short samples can estimate a baseline without outer accuracy evidence. No annual totals become arrival schedules, within-year seasonality, cargo per call or observed box-size mixes.

Guam FY2025 remains the board-reported unaudited 83,574 boxes. Conley FY2018/FY2020 TEU use FY2025 ACFR S-15 (281,978/283,061); older-report differences remain unresolved. Primary source references are in the data source register.

The unchanged reviewed workbook SHA-256 is `d77005bc4224bcf13f439baa6379a41fc15cda616b8c9b46df689da2dc236138`. The public reproduction needs only the repository workbook and code, with no private archive or original-copy dependency.

Files: actual_vs_predicted.csv contains all target, training, error, method and prior-envelope rows; method_comparison.csv contains fixed benchmarks; latest_forecasts.csv contains current estimates and summary errors; forecast_audit.json retains all diagnostics.

Run `python docs/model_review/reproduce_forecast_audit.py` from the repository folder. Independent annual arithmetic and leakage/promotion/zero/gap cases are also tested in tests/test_data.py.

References: [rolling-origin evaluation](https://otexts.com/fpp3/tscv.html), [simple benchmarks](https://otexts.com/fpp3/simple-methods.html), [forecast accuracy](https://otexts.com/fpp3/accuracy.html), and [WAPE limitations](https://robjhyndman.com/hyndsight/wape.html).
