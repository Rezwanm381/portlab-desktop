# Upload contracts

Use CSV, TSV or XLSX. In a workbook put the matching header row within the first 40 rows of a worksheet. Unrelated sheets are ignored. Never upload both the same table and its copied template: duplicate identifiers and annual years are rejected.

## Vessel calls

`vessel_calls.csv` uses real elapsed hours from a common time zero. `vessel_calls_utc.csv` uses ISO dates/times; hour zero becomes the earliest arrival for that port in all selected files. A timestamp in an explicitly UTC column without an offset is interpreted as UTC. Do not mix these time bases within a port.

Required: `vessel_id`, one of `arrival_hour` / `arrival_utc`, `import_boxes`, `export_boxes`. Blank counts are missing, not zero. Counts are whole boxes, not TEU. A repeated vessel visit needs a distinct call ID. IDs are case-insensitive within a port. Optional dimensions default to 180 m and 8 m; missing `box40_share` defaults to 0.5 as a sensitivity assumption. Fractions must be between 0 and 1. Dimensions are in metres.

The original AnyLogic names `VesselID`, `ETA_RealHours`, `ImportContainers`, `ExportContainers`, `ExpectedDepartureRealHour`, `PlannedPortStayRealHours` are accepted. The selected GUI port is used for files without a port column, and that assumption is reported. `Priority`, `VesselClass`, and `DelayCostUSDPerHour` are not used by the prototype service/financial model.

Use `evidence_type=observed` only for actual measured calls, with `evidence_reference` identifying the log/export. `synthetic` marks examples. With no provenance, imports remain `unverified`. The app does not independently audit declarations.

Optional measured times use `observed_berth_start_hour` / `observed_departure_hour` for elapsed-hour arrivals, or `berth_start_utc` / `departure_utc` for UTC arrivals. The hour columns also accept `observed_berth_start_real_hour` / `observed_departure_real_hour`. Use the same origin and time basis as that port's arrivals. Times must be at or after arrival, with departure at or after berth start when both are supplied. Measured fields require `evidence_type=observed` and an evidence reference. They survive saved projects and produce simulated-minus-observed errors and coverage in a run; they do not automatically calibrate or independently validate the prototype engine.

## Annual history

Required: explicit `port`, `fiscal_year`, `metric`, `value` and a clear boxes or TEU unit (metric itself may supply unit). `period_start` and `period_end` are optional together; when present they must describe an annual period. Monthly/quarterly rows and synthetic/forecast annual rows are excluded, not summed into annual observations. Reported annual rows must include `source_id`; rows without provenance remain unverified. Duplicate/revised annual values must be reconciled before import.

`record_status` supports `official_port_report`, `official_ACFR`, `reported_unaudited`, `observed`, `actual`, `unverified`, `synthetic`, and `forecast`/`estimate`. Fiscal-year labels must match the end year. Guam ends September 30; Conley ends June 30. Forecast errors are tested through nested one-year rolling holdouts. Error envelopes are historical errors, not confidence intervals. Annual totals never become a vessel arrival schedule.

## Settings

Copy `data/guam_settings.json` or `data/conley_settings.json`, edit the values, and import through Settings. Only fields supported by `TerminalConfig` are accepted. Settings load automatically when opening a saved project; all service/resource inputs are recorded in reports.

The prototype compares operational scenarios after the observation and uninterrupted-pressure gate. No incremental cost data or defensible vessel delay values are provided, so it does not calculate investment ROI.
