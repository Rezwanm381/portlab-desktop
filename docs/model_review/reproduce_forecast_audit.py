"""Reproduce the public annual audit without private archives or machine paths."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from portlab import __version__
from portlab.data import _METHODS, _predict, forecast_series, load_datasets

SOURCE_HASH = 'd77005bc4224bcf13f439baa6379a41fc15cda616b8c9b46df689da2dc236138'


def write_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def legacy_replay(series):
    """Historical v0.1 selector, retained only to reproduce the review finding."""
    values = [float(value) for _, value in series]
    errors = []
    for cut in range(4, len(values)):
        train = values[:cut]
        losses = {method: statistics.fmean(abs(_predict(train[:origin], method) - train[origin])
                                           for origin in range(3, len(train))) for method in _METHODS}
        method = min(_METHODS, key=lambda candidate: (losses[candidate], _METHODS.index(candidate)))
        errors.append(_predict(train, method) - values[cut])
    return 100 * sum(abs(error) for error in errors) / sum(values[4:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'data' / 'Port_Guam_Conley_History.xlsx'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_HASH:
        raise ValueError('The annual audit is frozen to the supplied workbook through FY2025. Its hash changed; review the new source before updating this audit.')
    bundle = load_datasets([source])
    if sum(map(len, bundle.annual_series.values())) != 26:
        raise ValueError('Expected the 26 reported annual observations from the reviewed workbook.')
    records = forecast_series(bundle.annual_series)
    # Recalculate scores from actual/predicted rows, independently of the
    # production accuracy helper. This also catches serialization discrepancies.
    for row in records:
        tests = row['backtests']
        errors = [test['forecast'] - test['actual'] for test in tests]
        wape = 100 * sum(abs(error) for error in errors) / sum(test['actual'] for test in tests)
        if abs(wape - row['wape_percent']) > 1e-10:
            raise ValueError('Forecast summary differs from heldout-row arithmetic.')
    legacy = [{'port': port, 'metric': metric, 'legacy_policy_wape_percent': legacy_replay(series)}
              for (port, metric), series in sorted(bundle.annual_series.items())]
    metadata = {'review_date': '2026-10-04', 'app_version': __version__,
                'source_workbook': source.name, 'source_sha256': digest,
                'observed_annual_records': 26, 'synthetic_annual_records_used': 0,
                'historical_actual_end_year': 2025,
                'evaluation_status': 'retrospective_development_replay_not_independent_prospective_validation',
                'source_notes': bundle.evidence_notes, 'legacy_policy': legacy, 'forecasts': records}
    (output / 'forecast_audit.json').write_text(json.dumps(metadata, indent=2, allow_nan=False), encoding='utf-8')
    write_csv(output / 'actual_vs_predicted.csv', [{'port': row['port'], 'metric': row['metric'], **test}
                                                for row in records for test in row['backtests']])
    write_csv(output / 'method_comparison.csv', [{'port': row['port'], 'metric': row['metric'], **method}
                                               for row in records for method in row['method_comparison']])
    fields = ('port', 'metric', 'history_end_year', 'forecast_year', 'forecast', 'method', 'wape_percent',
              'baseline_wape_percent', 'baseline_skill_percent', 'mae', 'rmse', 'mean_error', 'holdouts',
              'uncertainty_low', 'uncertainty_high', 'envelope_hits', 'envelope_evaluated_holdouts', 'status')
    write_csv(output / 'latest_forecasts.csv', [{key: row[key] for key in fields} for row in records])
    lookup = {(row['port'], row['metric']): row for row in records}
    legacy_lookup = {(row['port'], row['metric']): row for row in legacy}
    order = [('Guam', 'boxes'), ('Conley', 'boxes'), ('Conley', 'TEU')]
    lines = ['# Reported annual forecast audit', '',
        'This audit uses 26 reported annual observations through FY2025. No synthetic annual targets, inferred Guam TEU, or vessel-level operating predictions enter the scores.', '',
        '## Heldout errors', '',
        '| Series | Target FYs | Targets | Guarded-policy WAPE | Last-year WAPE | MAE | Historical v0.1 policy WAPE |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for key in order:
        row = lookup[key]
        lines.append(f'| {row["port"]} {row["metric"]} | {row["tested_first_year"]}–{row["tested_last_year"]} | {row["holdouts"]} | {row["wape_percent"]:.3f}% | {row["baseline_wape_percent"]:.3f}% | {row["mae"]:,.1f} | {legacy_lookup[key]["legacy_policy_wape_percent"]:.3f}% |')
    lines += ['',
        'WAPE = 100 × total absolute error / total actual volume in the target years. It is an error ratio, not a probability of correctness. Signed error is prediction minus actual; MAE/RMSE are in boxes or TEU. Guam has only two outer targets. Conley misses the sharp FY2022 drop and FY2023 recovery.', '',
        'The reviewed policy matches the last-year benchmark on these observations. The former selector could promote a challenger from one earlier validation error and performed worse on every supplied series. The updated three-inner-target, 5%-MAE-margin and two-recent-win controls are heuristic prototype guards. They were developed against this history, so replay is retrospective development evidence, even though each fold hides its own target.', '',
        '## Following-data-year estimates', '',
        '| Series | Latest actual FY | Estimate FY | Latest method | Point | Historical error envelope | Prior-only hits |',
        '| --- | ---: | ---: | --- | ---: | --- | ---: |']
    for key in order:
        row = lookup[key]
        lines.append(f'| {row["port"]} {row["metric"]} | {row["history_end_year"]} | {row["forecast_year"]} | {row["method"]} | {row["forecast"]:,.0f} | {row["uncertainty_low"]:,.0f}–{row["uncertainty_high"]:,.0f} | {row["envelope_hits"]}/{row["envelope_evaluated_holdouts"]} |')
    lines += ['',
        'FY2026 follows the latest supplied FY2025 observation. Its fiscal periods had already ended at the October 2026 review, without supplied actual FY2026 totals. These are historical following-period estimates, not observed FY2026 totals or FY2027 forecasts.', '',
        'The envelope uses the largest earlier outer absolute error, with a zero floor. Each heldout envelope is built only from still-earlier errors; the first target has no envelope. The small hit counts demonstrate that this is not a confidence interval, calibrated coverage or a guarantee. The latest band describes policy errors rather than independent uncertainty for a newly selected method.', '',
        '## Protocol and source boundaries', '',
        'Outer targets begin after four observed years. All fitting and selection use strictly earlier observations. Fixed-method rows use identical outer years, but their retrospective rankings do not choose the current method. The custom damped_trend adds half a recent OLS slope and is not fitted Holt damped exponential smoothing.', '',
        'Zero observations are retained; all-zero targets give undefined WAPE while MAE/RMSE remain available. Gaps stop forecasting. Fewer than three years produce no estimate; short samples can estimate a baseline without outer accuracy evidence. No annual totals become arrival schedules, within-year seasonality, cargo per call or observed box-size mixes.', '',
        'Guam FY2025 remains the board-reported unaudited 83,574 boxes. Conley FY2018/FY2020 TEU use FY2025 ACFR S-15 (281,978/283,061); older-report differences remain unresolved. Primary source references are in the data source register.', '',
        f'The unchanged reviewed workbook SHA-256 is `{digest}`. The public reproduction needs only the repository workbook and code, with no private archive or original-copy dependency.', '',
        'Files: actual_vs_predicted.csv contains all target, training, error, method and prior-envelope rows; method_comparison.csv contains fixed benchmarks; latest_forecasts.csv contains current estimates and summary errors; forecast_audit.json retains all diagnostics.', '',
        'Run `python docs/model_review/reproduce_forecast_audit.py` from the repository folder. Independent annual arithmetic and leakage/promotion/zero/gap cases are also tested in tests/test_data.py.', '',
        'References: [rolling-origin evaluation](https://otexts.com/fpp3/tscv.html), [simple benchmarks](https://otexts.com/fpp3/simple-methods.html), [forecast accuracy](https://otexts.com/fpp3/accuracy.html), and [WAPE limitations](https://robjhyndman.com/hyndsight/wape.html).', '']
    (output / 'forecast_audit.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'app_version': __version__, 'observed_annual_records': 26, 'source_sha256': digest,
                      'series': [{'port': row['port'], 'metric': row['metric'], 'wape_percent': row['wape_percent'],
                                  'holdouts': row['holdouts']} for row in records]}, indent=2))


if __name__ == '__main__':
    main()
