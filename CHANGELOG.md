# Changelog

## 0.2.0 — 2026-10-04

- Guarded annual forecast selection against the last-year benchmark; added actual-versus-predicted rows, MAE/RMSE/bias, fixed-method comparisons and historical envelope checks.
- Preserved optional observed berth/departure times and added paired timing diagnostics with explicit validation status.
- Replaced all-exports-first handling with feasible import-preferred interleaving and joint yard/apron reservations.
- Added future-appointment export staging, settled cutoff snapshots and completed/censored waiting statistics.
- Strengthened safeguards for unfinished cargo and multiple-candidate comparisons.
- Added berth focus, equipment details, cargo routes, occupancy-scaled yard samples and Operations/Overview cameras.
- Corrected cancelled and partial-trace replay coverage.
- Added operating traces, vessel outcomes, Model checks, a tested synthetic import dataset and public documentation.
- Verified standalone Windows packaging, source consistency and report export.

## 0.1.0 — 2026-10-04

- Initial local PySide6 workflow with CSV/XLSX import, settings, saved projects and background workers.
- Shared-resource SimPy simulation with finite inventory and pressure-gated alternatives.
- Panda3D replay using supplied procedural assets.
- Local HTML, Excel, CSV and JSON reports and portable Windows runtime.
