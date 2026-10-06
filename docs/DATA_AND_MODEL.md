# Data and model

PortLab combines three distinct layers: exploratory annual volume forecasting,
a discrete-event operating scenario, and replay of that scenario's recorded
events. Reported annual volumes do not generate vessel arrivals or calibrate
the operational model.

## Evidence and units

| Input | Bundled evidence | Supported interpretation |
| --- | --- | --- |
| Guam annual boxes | Six reported totals, FY2020–2025 | Exploratory one-year box-volume estimates |
| Conley annual boxes | Ten reported totals, FY2016–2025 | Exploratory one-year physical-container estimates |
| Conley annual TEU | Ten reported totals, FY2016–2025 | A separate TEU series |
| Vessel calls and operational settings | Explicitly synthetic/illustrative | Conditional scenario behavior and software checks |
| Supplied measured service times | Optional, declared observed | Same-run timing errors and paired coverage |

There are **26 reported annual observations**. No Guam TEU series is inferred.
Boxes are physical containers; TEU measures equivalent 20-foot capacity. The
app never silently converts TEU into boxes. `box40_share` is a scenario fraction,
not an inferred measured mix; its 0.5 default is a sensitivity assumption.

The source register and unchanged workbook are in [data](../data/). Conley uses
[Massport's FY2025 ACFR](https://www.massport.com/sites/default/files/2025-12/Massport-2025-ACFR-Final-Report.pdf),
S-15, for a consistent published series excluding over-the-road volumes. Older
reports differ for FY2018/FY2020 TEU; that discrepancy remains unresolved.
Guam FY2020–2024 use the
[Guam Economic Development Authority seaport table](https://www.investguam.com/economic-indicators/),
and FY2025 uses the
[Port Authority board report](https://portofguam.com/sites/default/files/112125_pag_board_meeting_materials.pdf).
FY2025's 83,574 Guam boxes remain labelled board-reported unaudited.

`observed`, `synthetic` and `unverified` are data declarations retained in the
project/report evidence. An observed declaration does not establish an
independent source audit or empirical validity of all model parameters.

## Import contracts

CSV, TSV and XLSX are accepted. In a workbook, place the header within the first
40 rows of a worksheet. Unrelated worksheets are ignored. Old `.xls` files need
to be saved as `.xlsx` first. No spreadsheet code or macros are executed.

| Vessel field | Requirement / meaning |
| --- | --- |
| `vessel_id` | Required unique call ID; case-insensitive within a port |
| `arrival_hour` or `arrival_utc` | Required elapsed hours or ISO UTC timestamp |
| `import_boxes`, `export_boxes` | Required finite whole nonnegative physical-box counts |
| `port` | Optional; the selected desktop port is assumed and noted if absent |
| `length_m`, `draft_m` | Optional positive dimensions in metres; defaults 180 m / 8 m are assumptions |
| `box40_share` | Optional fraction from 0 to 1; default 0.5 is an assumption |
| `evidence_type`, `evidence_reference` | Use observed plus a reference for measured calls; examples use synthetic |
| `observed_berth_start_hour`, `observed_departure_hour` | Optional measured elapsed times using the arrival origin |
| `berth_start_utc`, `departure_utc` | Optional measured timestamps for UTC arrivals |

For UTC files, hour zero is the earliest selected arrival for each port across
all files imported together. Offset-free timestamps in explicitly UTC columns
are interpreted as UTC. Do not mix relative and absolute time bases within a
port. Measured times require observed provenance and a reference, cannot precede
arrival, and departure cannot precede a supplied berth start.

Original AnyLogic aliases include `VesselID`, `ETA_RealHours`,
`ImportContainers`, `ExportContainers`, `ExpectedDepartureRealHour` and
`PlannedPortStayRealHours`. Planned/expected timestamps do not become measured
service outcomes. Priority, vessel class and delay-cost fields do not implement
a priority or financial model. See the [full template rules](../templates/README.md).

Annual rows need an explicit port, whole fiscal year, value and clear boxes/TEU
metric/unit. Zero is a valid observation. Reported rows need a `source_id`;
unlabelled rows remain unverified. Revisions or duplicate years must be
reconciled before import. Monthly, quarterly, synthetic and forecast rows are
excluded instead of being aggregated into observed annual totals. Optional
period dates must describe the fiscal year: Guam ends September 30, Conley
ends June 30.

## Annual forecasts

The candidates are last year's value, a trailing three-year mean, and a custom
trend equal to the last value plus half the recent OLS slope. The trend is not
fitted Holt damped exponential smoothing.

Each outer target is withheld from fitting and selection. Inner rolling
one-year targets start after three observations. A challenger replaces the
last-year benchmark only with at least three inner targets, at least 5% lower
MAE and lower error in both latest inner targets. These controls are transparent
prototype heuristics, not statistical or port-industry standards. Fixed methods
are compared on the same outer years; those rankings do not select the current
production method.

| Series | Outer target years | Targets | Policy WAPE | MAE in series unit |
| --- | --- | ---: | ---: | ---: |
| Guam boxes | FY2024–2025 | 2 | 1.216% | 1,026.5 boxes |
| Conley boxes | FY2020–2025 | 6 | 21.501% | 28,210.2 boxes |
| Conley TEU | FY2020–2025 | 6 | 21.267% | 49,541.3 TEU |

WAPE is 100 × total absolute error / total actual volume in the target years.
It is not a probability of correctness. The selected policy matches the
last-year baseline for all three supplied histories. Guam's two targets provide
little validation coverage; Conley's large demand changes produce large misses.
The selector revision was developed against this history, so replayed results
remain retrospective development evidence.

Latest estimates refer to **FY2026**, the year following the supplied FY2025
actuals: 83,574 Guam boxes, 137,632 Conley boxes and 247,405 Conley TEU. These
fiscal periods had already ended at the October 2026 review; their actual totals
were not supplied. They are not FY2027 forecasts or observed FY2026 totals.

The displayed envelope adds/subtracts the largest historical outer error,
clipped at zero. Earlier-only envelope checks hit 0/1 Guam targets and 3/5 for
each Conley series. This is **not a confidence interval** or calibrated future
coverage. Missing fiscal years stop forecasting. Fewer than three annual values
produce no estimate; short histories can provide a baseline without an outer
accuracy score. All-zero target volumes have undefined WAPE while MAE/RMSE
remain defined. [Reproducible audit](model_review/README.md).

## Operating scenarios and recommendations

The CPU-based discrete-event engine shares finite berths, cranes and tractors;
checks vessel compatibility; stages exports; moves imports through apron/yard;
and releases yard cargo after configured dwell. Import discharge is preferred
when joint yard/apron reservation is feasible; ready exports can load when it is
not. Later appointments may occupy yard space before the cutoff, with separate
future-export accounting.

Counts are conserved, but larger workloads may use batches with approximate
within-batch timing/parallelism. Runs above 30,000 handling jobs or 10,000
in-horizon calls are rejected before allocation. The export prestaging policy
uses a conservative half-yard quota. These are schematic prototype policies,
not vessel hatch/stow, stability, truck-gate or equipment-failure models.

Default recommendation rules require 168 hours of observation and 24 hours of
uninterrupted raw queue pressure. Pressure may start earlier; cleared pressure
resets persistence. Paired full-horizon counterfactuals use matching seeds.
Candidate support requires physical checks, a conservative family-adjusted
improvement bound and no increase in unfinished cargo, unserved calls or backlog.
The comparison is conditional on uploaded workloads and assumed parameters.
Acquisition delays, investment costs and ROI are absent.

Waiting values for unfinished calls are truncated at the cutoff and therefore
lower bounds. Completed/berthed-call summaries and population counts are
reported separately. Supplied observed berth/departure times produce signed
errors (simulated minus observed), MAE, RMSE, bias and missing/censored coverage.
They do not fit parameters or establish independent predictive validity.

The 3D layer replays recorded events; playback speed does not change results.
Geometry and routes are schematic, and displayed cargo/equipment are capped
samples. Missing/capped cargo traces explicitly show unavailable yard totals
outside retained coverage. Stock/flow monitoring records the DES; an empirically
calibrated endogenous system-dynamics feedback model is not included.
