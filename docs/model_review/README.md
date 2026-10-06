# Annual forecast evidence

This folder contains a public, reproducible audit of the **reported annual
observations** in the repository. It does not evaluate synthetic vessel calls or
establish independent operational prediction accuracy.

- [Narrative audit](forecast_audit.md): source boundaries, measured errors,
  heuristic selection rules and historical error envelopes.
- [Actual versus predicted](actual_vs_predicted.csv): all heldout target values,
  forecasts, errors, training endpoints, selection reasons and prior-only bands.
- [Fixed benchmarks](method_comparison.csv): comparable outer-year results for
  last-year, three-year mean and custom trend methods.
- [Following-data-year estimates](latest_forecasts.csv): latest estimates, error
  scores and exploratory status.
- [Complete audit](forecast_audit.json): machine-readable diagnostics and source
  notes.

From the repository folder, after installing dependencies:

```text
python docs/model_review/reproduce_forecast_audit.py
```

The script also works from another working directory using its script path. It
checks the reviewed workbook hash, recalculates the summary WAPE from heldout
rows and overwrites these public artifacts. `--output` selects another output
folder. No private conversation, original ZIP or machine-specific location is
required. The fixed hash intentionally rejects changed source data until a new
source review is performed.

The review is retrospective development evidence. Every outer target was hidden
from its own fit/selection, but the revised controls were developed against this
history. Freeze the protocol and evaluate subsequent reported observations
before claiming independent predictive validation. See
[data and model](../DATA_AND_MODEL.md) and [validation](../VALIDATION.md).
