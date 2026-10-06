# Validation status

Version 0.2.0 was checked on Windows on 4 October and rechecked on 5 October 2026. Numerical consistency,
historical forecast error and empirical operating prediction are separate kinds
of evidence. **Real vessel waiting/turnaround prediction is not independently
validated.** The shipped operational examples are synthetic.

## Verified software behavior

| Check | Recorded result | Scope |
| --- | --- | --- |
| Calculation/data/replay suite | 69 automated tests passed | Contracts, forecast selection, inventories, sharing, horizons, recommendations and replay |
| Desktop workflow | 132 checks passed | Real Qt workers, cancellation, stale state, sidecars, project restore and report exports |
| Additional evidence UI | Latest curated-dataset check passed 248 assertions | Three forecast series/modes, independent error arithmetic, CSV/XLSX equivalence, declared measured timing errors and Model checks |
| Frozen Windows executable | Startup, simulation, five-candidate comparison, rendering and exports passed | All 11 required physical/accounting checks passed |
| Replay | 11 tests plus Guam/Conley rendered inspection | Cancellation cutoff, future staging, resource/cargo activity, backward seeks and incomplete traces |

The additional Qt assertion count includes checks repeated while waiting for
workers and can change with scheduling. Build verification also compared all
nine packaged application modules, including nested functions, with the source;
filename-only metadata differences were excluded.

Independent analytical cases cover ten horizon cutoffs and more than 670,000
aggregate boxes, finite yard/apron reservations, resource overlap, future export
staging and settled cutoff snapshots. Forced accounting failures and increased
unfinished cargo prevent candidate acceptance. Passing these cases establishes
consistency within assumptions; it does not establish actual port capacity or
causal investment benefit.

## Prediction evidence

The 26 unchanged reported annual observations give the following guarded-policy
outer holdout errors. All three policies match their last-year baseline.

| Series | Targets | Policy / baseline WAPE | MAE |
| --- | ---: | ---: | ---: |
| Guam boxes, FY2024–2025 | 2 | 1.216% | 1,026.5 boxes |
| Conley boxes, FY2020–2025 | 6 | 21.501% | 28,210.2 boxes |
| Conley TEU, FY2020–2025 | 6 | 21.267% | 49,541.3 TEU |

These are retrospective development results on small samples. WAPE is an error
ratio, not a correctness probability. The historical envelope is not a
confidence interval; prior-only hits were 0/1 for Guam and 3/5 for each Conley
series. Estimates follow the latest supplied FY2025 value and therefore target
FY2026, without supplied actual FY2026 totals. Full rows, method comparisons and
source controls are in the [forecast audit](model_review/README.md).

The UI's declared-observation timing fixture is hand constructed for software
checks. Independent expected berth-start MAE is 0.1 hour, departure MAE 0.35
hour and departure RMSE 0.353553 hour. Displayed/exported diagnostics agree.
This is not collected port data. Real observed uploads can produce same-run
hindcast errors and paired/censored coverage; calibration and separate-period
validation remain further work.

## Measured resource use

The reference laptop had a Ryzen 7 8845HS, 16 GB RAM and an RTX 4050 Laptop GPU.
Small-scenario CPU runs took approximately 0.14–0.24 seconds and six-replication
comparisons approximately 6–9 seconds. The desktop workflow peaked at 247.5 MiB
process RAM; the latest additional evidence UI run at 201.9 MiB; the packaged smoke run
at 218.29 MiB. The standalone folder was approximately 143 MiB before compression.

The final Guam render check measured 9.94 ms mean and 23.86 ms maximum frame
render time, with 171.5 MiB process RAM. Frame rendering time is not guaranteed
end-to-end playback speed. Playback is capped at 30 frames/s and 120 cargo
visuals; both limits are editable. These measurements describe small test
scenarios, not all datasets or hardware. Simulation uses the CPU and requires
no GPU model training.

## Reproduce

Install the recorded environment using Python 3.12 on Windows and
`requirements.lock.txt`, then run from the repository folder:

```text
python -m unittest discover -s tests -v
python docs/model_review/reproduce_forecast_audit.py
python examples/synthetic_import_test/check_example.py
python tools/desktop_check.py --output verification/desktop
python tools/phase2_check.py --output verification/phase2
python tools/visual_smoke.py
python -m portlab --smoke --output verification/smoke
```

Qt and rendering checks open their own application instance. Local diagnostics
and screenshots may contain machine paths and belong in ignored `verification`
or output folders; they are not needed for the public annual audit. The included
example results are dated snapshots and may change after model revisions.

For an empirical prediction claim, obtain actual call/service/resource records,
calibrate on one period, freeze the model and evaluate a separate later period.
Annual forecasts need subsequent reported observations and a frozen selection
protocol. See [data and model](DATA_AND_MODEL.md) for the boundaries of current
claims.
