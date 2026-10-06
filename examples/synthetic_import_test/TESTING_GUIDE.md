# Test the 32-call example

This fixed-seed example was rerun successfully with **PortLab 0.2.0**. All calls
and operating parameters are fictional. The CSV, XLSX and settings inputs are
unchanged from the shipped example; their hashes and the tested model-source
hashes are recorded in [IMPORT_CHECK_RESULTS.json](IMPORT_CHECK_RESULTS.json).

## Import and run

1. Open the v0.2.0 app and choose **Guam** in the terminal selector.
2. Keep `guam_synthetic_test.csv`, `guam_synthetic_test.xlsx` and
   `guam_synthetic_test.settings.json` together in one folder.
3. Click **Import data**. Choose **either the CSV or the XLSX**, then open it.
   Both contain the same calls; selecting both duplicates the call IDs.
4. In **Project & data**, confirm **32** selected calls and **synthetic** evidence.
5. In **Settings**, confirm the automatically loaded preset: horizon **336 h**,
   seed **20261004**, **2** berths, **3** cranes, **1** crane per vessel, **8**
   tractors, **7.5-minute** tractor cycle and **6,000-box** yard capacity. Keep
   the remaining preset values unchanged for this test. Do not switch terminals
   after importing, because switching restores the other terminal's preset.
6. Click **Run simulation** and wait for **Simulation complete**.

## Expected baseline

| Check | Expected result |
| --- | --- |
| Requested calls / horizon | 32 / 336 hours |
| Served (departed) calls | 11 |
| End backlog, including unfinished working calls | 21 |
| Mean simulated wait through cutoff | 171.78 hours |
| Berthed-call wait mean | 129.43 hours, based on 13 calls that reached a berth |
| Unfinished service cargo | 13,847 physical boxes |
| Pressure gate | `eligible`; first eligible hour 168 |
| Model checks → Cargo and resources | `Pass`, all 11 required checks |
| Model checks → Operational prediction | `not_validated` |

The backlog is intentional congestion, not an import failure. The 13 calls
whose waiting ended include two still working at cutoff; only 11 departed.
Unfinished waits are truncated at the horizon and are lower bounds.

Open **Simulation & replay → Queues, yard & vessel outcomes** to inspect the
traces and all 32 call rows. Use **3D replay**, **Play**, the timeline and camera
controls to inspect animation. Displayed cargo is a capped visual sample.

Click **Compare actions**. Expect five evaluated candidates and the preferred
conditional operational candidate **Reassign existing cranes**. This changes
the per-vessel crane assignment limit within the same fleet. It is a result for
this fictional full-horizon scenario, not a real purchase or investment claim.

Use **Reports → Export run report** to save the HTML, XLSX, CSV and complete JSON
outputs. Save/open a project if desired, then rerun it to regenerate results.
To try the other input format, import it alone; it replaces the schedule rather
than appending a duplicate. With the same settings, outcomes should match.

## Source reproduction and evidence limits

After installing the repository dependencies, run from the repository folder:

```text
python examples/synthetic_import_test/check_example.py
```

This checks CSV/XLSX call equality, provenance, matching settings, all required
physical checks and current-version baseline/comparisons. It updates the JSON
snapshot, including verification time and SHA-256 hashes. Automatic desktop
sidecar behavior is tested separately by the Qt verification tools.

The example uses no annual targets and supplies no observed berth/departure
times. It cannot measure annual forecast accuracy or validate real operational
predictions. Existing reported annual histories remain available in Forecasts;
their separate retrospective errors are documented in
[the annual audit](../../docs/model_review/README.md).
