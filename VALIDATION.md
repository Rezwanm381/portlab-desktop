# Release verification — 5 October 2026

PortLab Desktop 0.2.0 was built and run on the user's Windows laptop with Ryzen 7 8845HS, approximately 16 GB installed RAM, and an RTX 4050 Laptop GPU. The new build is in a separate versioned folder; an earlier running application was preserved.

## Software checks

- **69 automated tests passed**, covering data contracts, old saved projects, forecast selection, conservation, resource sharing, horizon consistency, recommendations and replay.
- **132 desktop workflow checks passed**: real background runs/comparisons, cancellation, reruns, stale controls, settings sidecars, rejected-input rollback, project restoration, rendered playback and report export. Wall time was 51.225 seconds; peak process RAM was 247.5 MiB.
- **248 assertions passed in the latest additional Qt run**, including the curated 32-call dataset, all three annual prediction series, both chart modes, independently calculated errors, benchmark tables, equivalent CSV/XLSX inputs, restored sidecar settings, synthetic provenance, measured timing errors, Model checks, project restoration and exported diagnostics. Wall time was 5.473 seconds; peak RAM was 201.9 MiB. The count includes assertions repeated during worker waits and may vary with scheduling.
- The frozen executable passed startup, simulation, five-candidate comparison, OpenGL rendering and HTML/XLSX/CSV/JSON report export. The renderer reported no error and all 11 required physical/accounting checks passed. Packaged peak RAM was 218.29 MiB; the pictured demo baseline computed in about 0.23 seconds and served 14 of 24 synthetic calls.
- All nine application modules in both the build archive and executable's embedded archive match the current source code, including nested functions; only rewritten filename metadata is ignored. The source verifier accepts a filename-only change and rejects a changed nested function.
- All 70 bundled assets, data, templates and license files match the curated source. The fresh source ZIP imports, runs its CLI and executes the 48-call/three-series demo; its portable annual audit, synthetic example and source-only packaging also pass without private archives or earlier outputs.
- Eleven replay tests cover real cancellation cutoff, future export staging, resource intervals, cargo movement progress, backward seeking, and capped/uncaptured traces.
- Actual GPU replay screenshots were checked for Guam and Conley, including operations, berth detail, overview, top, export activity, capped traces, cancellation and future staging. The final Guam check measured 9.94 ms mean frame rendering, 23.86 ms maximum and 171.5 MiB process RAM. This measures rendering, rather than guaranteed end-to-end playback speed. Playback remains capped at 30 frames/s and 120 cargo visuals.

The standalone folder is approximately 143 MiB before ZIP compression and includes the Python runtime and dependencies. CPU simulation of the three small scenarios took approximately 0.14–0.24 seconds; six-replication comparisons took approximately 6–9 seconds. Resource figures are measured small-scenario results and can change with input size and other running applications.

## Prediction checks

The unchanged source workbook contains 26 reported annual observations. Independent error recalculation confirms revised held-out WAPE of 1.216% for Guam boxes (two targets), 21.501% for Conley boxes (six), and 21.267% for Conley TEU (six). The guarded policy matches the last-year benchmark on these observations. The review was developed against this history, so these results remain retrospective evidence. Historical error envelopes are not calibrated confidence intervals.

The declared-observation timing fixture is hand constructed solely for software verification. Its independent expected berth-start MAE is 0.1 hour and departure MAE 0.35 hour; the UI and exported diagnostics match those values. This is not measured port data. The bundled and downloadable synthetic schedules have no actual service-time measurements and correctly report operational prediction as **not validated**.

Independent analytical checks include ten horizon cutoffs, more than 670,000 aggregate boxes, finite-yard/apron reservations, shared-resource overlap, later-appointment export staging, settled cutoff snapshots, increased unfinished-cargo rejection and forced accounting failures in recommendations. These establish numerical consistency within the configured assumptions. They do not establish actual capacity, calibrated operational predictions, causal improvement or investment returns.

## Reproduce and inspect

Use the source ZIP, install `requirements.lock.txt` in Python 3.12 on Windows, then run:

```text
python -m unittest discover -s tests -v
python tools/desktop_check.py --output verification/desktop_v0.2.0
python tools/phase2_check.py
python tools/visual_smoke.py
python tools/build_release.py --smoke --output verification/packaged_v0.2.0
python tools/package_delivery.py
```

Workspace evidence: `verification/unit_v0.2.0.log`, `verification/desktop_v0.2.0/diagnostics.json`, `verification/phase2/diagnostics.json`, `verification/packaged_v0.2.0/smoke_diagnostics.json`, and `output/visual_smoke_v2*/diagnostics.json`. The desktop ZIP includes the forecast audit and synthetic import check results; the source ZIP includes the reproducible checking tools. The README and MODEL_REVIEW describe further data and modeling improvements.
