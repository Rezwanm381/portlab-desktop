# Contributing

Open an issue with a small reproducible example before making a substantial model change. Describe the intended behavior, units, assumptions, and evidence. Synthetic schedules can test software behavior; measured records and independent holdouts are needed for claims about real port prediction.

Use Python 3.12 on Windows to reproduce the tested build:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m portlab
```

The headless test workflow covers calculations, imports, conservation, recommendation constraints, forecasting, and replay indexing. For desktop changes, also run `python tools/desktop_check.py` and `python tools/phase2_check.py` in a local graphical session. Rendering checks need an OpenGL-capable computer.

Reproduce the public examples with `python docs/model_review/reproduce_forecast_audit.py --output verification/forecast_audit` and `python examples/synthetic_import_test/check_example.py`. These scripts read the curated repository inputs and work without the original archives or previous local reports.

Keep changes focused. Preserve cargo counts, units, evidence labels, censored outcomes, reproducible random draws, and the distinction between scenario outputs and observed data. Add a regression for a demonstrated calculation or import defect. Do not add real records to the repository unless they are publicly shareable and their provenance is documented.

For a Windows build, run `python tools/build_release.py --smoke`, then `python tools/check_frozen_sources.py` and `python tools/package_delivery.py`. The manual GitHub build workflow creates review artifacts; it does not publish a release or substitute for local GPU and desktop checks.

See [LICENSE_STATUS.md](LICENSE_STATUS.md) and [third-party notices](licenses/THIRD_PARTY_NOTICES.md) before reusing or redistributing source, data, or assets. Contributions do not change those terms automatically.
