# Synthetic import example

This is a **fictional 32-call Guam operating scenario**, with a 336-hour horizon
and fixed seed 20261004. It is designed to exercise imports, resource pressure,
unfinished calls, replay and conditional comparisons. It is not actual Guam
demand, measured service performance or annual forecast evidence.

Use either `guam_synthetic_test.csv` or `guam_synthetic_test.xlsx`, with
`guam_synthetic_test.settings.json` in the same folder. Importing both formats
together duplicates the same call IDs. Follow [START_HERE.txt](START_HERE.txt)
or the [testing guide](TESTING_GUIDE.md) for exact import steps and expected
results. The [getting-started guide](../../docs/GETTING_STARTED.md) covers setup.

| v0.2.0 fixed-seed baseline | Result |
| --- | ---: |
| Calls | 32 |
| Import / export physical boxes | 13,802 / 7,845 |
| Last arrival | 194.38 hours |
| Departed / unfinished calls at 336h | 11 / 21 |
| Unfinished service cargo | 13,847 boxes |
| Restricted mean wait, all 32 calls | 171.777 hours |
| Mean wait for 13 calls that reached a berth | 129.432 hours |
| First recommendation eligibility | 168 hours |
| Required physical/accounting checks | All 11 pass |
| Operational prediction status | `not_validated` |

An unfinished wait is truncated at the horizon. The 13 completed waiting
observations include calls still working at cutoff; they are not 13 departed
calls. The pressure gate is eligible, allowing five paired comparisons. In this
fictional scenario, **Reassign existing cranes** is the preferred supported
operational candidate. This does not establish feasible real-world improvement,
equipment investment returns or a purchase decision.

[IMPORT_CHECK_RESULTS.json](IMPORT_CHECK_RESULTS.json) records current-version
results, verification time, per-candidate changes/constraints and SHA-256 hashes
of the inputs and tested model source.
The input CSV, XLSX and settings are preserved byte-for-byte from the shipped
example. To reproduce the calculation after installing dependencies:

```text
python examples/synthetic_import_test/check_example.py
```

The script runs from any working directory, validates CSV/XLSX equality and
matching settings, reruns the baseline and six paired replications per
candidate, then overwrites the JSON snapshot. Qt automatic sidecar loading is
verified separately by the desktop tools. Outcomes can change after model
revisions. See [validation](../../docs/VALIDATION.md) for claim boundaries.
