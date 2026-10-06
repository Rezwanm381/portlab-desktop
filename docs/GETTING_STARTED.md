# Getting started

PortLab runs locally. The Windows bundle includes Python and its dependencies;
no account, paid service or internet connection is required to use the app.

## Windows bundle

1. Extract the complete Windows ZIP from the repository's Releases page into a
   writable folder.
2. Start `PortLab.exe`. Keep its `_internal` folder beside it.
3. Choose **Guam** or **Conley** and click **Run simulation**. The bundled vessel
   calls are fictional scenario inputs; the annual history contains separately
   reported observations.
4. Open **Simulation & replay**. Use **Play**, the timeline and camera controls
   to inspect the run. **Queues, yard & vessel outcomes** shows numerical traces
   and individual call results.
5. Open **Forecasts** for annual estimates, actual-versus-predicted holdouts and
   benchmark tables. **Model checks** distinguishes numerical consistency from
   prediction evidence.
6. Use **Save project** to retain data and settings. Opening a project clears old
   results; rerun it before comparing actions.

The default project folder is beside the executable. Changing settings marks
the previous result stale. Switching terminals restores that terminal's preset,
so save a project or export edited settings before switching.

## Try a complete import

The [synthetic import example](../examples/synthetic_import_test/START_HERE.txt)
contains 32 fictional Guam calls, equivalent CSV/XLSX files and a settings file.

1. Keep all three `guam_synthetic_test` input files together.
2. Click **Import data** and select either the CSV or the XLSX. Selecting both
   duplicates the same call IDs and is rejected.
3. Confirm **Guam**, **32 synthetic calls** and the imported 336-hour settings.
   The matching `guam_synthetic_test.settings.json` loads automatically.
4. Run the simulation, then **Compare actions**. The example intentionally
   creates persistent congestion; unfinished calls are a valid scenario result.
5. In **Reports**, export into a folder you choose. Inspect `report.html`,
   `results.xlsx` or `complete_report.json`.

This upload replaces the vessel schedule. Existing annual histories remain
available; a synthetic operating file does not become annual forecast evidence.
Example outcomes and a reproducible check are included beside the inputs.

## Import your own records

Use the [input templates](../templates/README.md). Supply unique call IDs,
arrivals, import boxes and export boxes. Use elapsed hours or UTC timestamps
consistently within each port. Blank counts are missing values; explicitly enter
zero when there is no cargo in a direction.

Actual measured records need `evidence_type=observed` and an
`evidence_reference`. Optional observed berth/departure times enable same-run
timing diagnostics. The app does not independently verify declarations or fit
handling parameters automatically. See [data and model](DATA_AND_MODEL.md) for
accepted fields, units and interpretation.

For automatic settings, keep a same-stem JSON beside the dataset: `calls.csv`
or `calls.xlsx` uses `calls.settings.json`. The preset's port must match the
selected imported port. **Settings** also offers explicit import/export, and a
saved project includes settings. Invalid data/settings are rejected before
replacing the current dataset.

## Run the Python source

The tested build uses Python 3.12 on Windows. From the repository folder, use
PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m portlab
```

`requirements.txt` allows compatible dependency versions. For the recorded
Windows build/test environment, install `requirements.lock.txt` instead.
Build requirements, including PyInstaller, are included in that lock file.

Run calculation checks without opening the app:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe docs\model_review\reproduce_forecast_audit.py
.\.venv\Scripts\python.exe examples\synthetic_import_test\check_example.py
```

The last command reruns six paired replications per candidate and can take
several seconds. Desktop verification tools open their own Qt application and
save diagnostics locally; see [validation](VALIDATION.md). Distribution and
third-party rights are described in [LICENSE_STATUS.md](../LICENSE_STATUS.md).
