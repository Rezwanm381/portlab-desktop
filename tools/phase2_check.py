"""Check the phase-two evidence interface in a separate, real Qt instance.

This uses QTest on this process's widgets, never the user's running executable.
The operational 'observed' fixture is hand constructed solely to exercise the
declared-observation import path; it is not collected port operating evidence.
Annual accuracy checks below use the unchanged reported workbook observations.
"""
from __future__ import annotations

import argparse
import copy
import csv
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl
import psutil
from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from portlab.contracts import TerminalConfig
from portlab.engine import REQUIRED_PHYSICAL_CHECKS
from portlab.ui import MainWindow


# Independent fixed values from the supplied annual workbook. No synthetic
# targets or forecast implementation helpers participate in these calculations.
ACTUAL_HISTORY = {
    ('Guam', 'boxes'): list(zip(range(2020, 2026), [85143, 86794, 89052, 85627, 85258, 83574])),
    ('Conley', 'boxes'): list(zip(range(2016, 2026), [140967, 145540, 161130, 174849, 161171, 140750, 79095, 123460, 145117, 137632])),
    ('Conley', 'TEU'): list(zip(range(2016, 2026), [250439, 254747, 281978, 307331, 283061, 247845, 139959, 220810, 258620, 247405])),
}
METHODS = ('last_year', 'mean3', 'damped_trend')


def independent_prediction(values, method):
    if method == 'last_year':
        return float(values[-1])
    if method == 'mean3':
        return sum(values[-3:]) / len(values[-3:])
    recent = values[-5:]
    n = len(recent)
    # Independent closed-form OLS calculation (x = 0, ..., n-1).
    xsum = n * (n - 1) / 2
    x2sum = n * (n - 1) * (2 * n - 1) / 6
    slope = (n * sum(i * y for i, y in enumerate(recent)) - xsum * sum(recent)) / (n * x2sum - xsum * xsum)
    return max(0.0, values[-1] + slope / 2)


def independent_selection(values):
    errors = {method: [abs(independent_prediction(values[:i], method) - values[i])
                       for i in range(3, len(values))] for method in METHODS}
    if len(values) - 3 < 3:
        return 'last_year'
    means = {method: sum(items) / len(items) for method, items in errors.items()}
    best = min(METHODS, key=lambda method: (means[method], METHODS.index(method)))
    if (best == 'last_year' or means['last_year'] == 0 or
            means[best] > means['last_year'] * .95 or
            not all(a < b for a, b in zip(errors[best][-2:], errors['last_year'][-2:]))):
        return 'last_year'
    return best


def independent_scores(actual, predicted):
    errors = [p - a for a, p in zip(actual, predicted)]
    return {'wape_percent': 100 * sum(abs(e) for e in errors) / sum(actual),
            'mae': sum(abs(e) for e in errors) / len(errors),
            'rmse': math.sqrt(sum(e * e for e in errors) / len(errors)),
            'mean_error': sum(errors) / len(errors)}


def number(value, digits=2):
    return '—' if value is None else format(value, f',.{digits}f')


class CheckedWindow(MainWindow):
    def __init__(self):
        self.check_errors = []
        super().__init__()

    def error(self, message):
        self.check_errors.append(str(message))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'verification' / 'phase2')
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    diagnostics = {'passed': False, 'checks': [], 'qt_messages': [], 'python_exceptions': [],
                   'screenshots': [], 'independent_annual_accuracy': [],
                   'operational_fixture_scope': 'Controlled hand-constructed declared-observation fixture; not empirical port validation.'}
    started = time.perf_counter()
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    old_hook = sys.excepthook
    old_handler = qInstallMessageHandler(lambda kind, context, message:
        diagnostics['qt_messages'].append({'kind': str(kind), 'message': message}))
    sys.excepthook = lambda kind, value, tb: diagnostics['python_exceptions'].append(
        ''.join(traceback.format_exception(kind, value, tb)))
    app = QApplication.instance() or QApplication(['PortLab phase-two verification'])
    app.setQuitOnLastWindowClosed(False)
    window = None

    def check(condition, description):
        if not condition:
            raise AssertionError(description)
        diagnostics['checks'].append(description)

    def near(actual, expected, description):
        check(actual is not None and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-9), description)

    def cells(widget):
        return [[widget.item(i, j).text() for j in range(widget.columnCount())]
                for i in range(widget.rowCount())]

    def validation_row(component):
        return next(row for row in cells(window.validation_table) if row[0] == component)

    def pump(ms=100):
        nonlocal peak_rss
        QTest.qWait(ms)
        app.processEvents()
        peak_rss = max(peak_rss, process.memory_info().rss)
        check(not window.check_errors, 'No application error dialog during Qt interaction')
        check(not diagnostics['python_exceptions'], 'No unhandled Python callback exception')

    def screenshot(name, widget=None):
        pump(80)
        path = output / f'{name}.png'
        pixmap = (widget or window).grab()
        check(pixmap.save(str(path)), f'Screenshot saved: {name}')
        diagnostics['screenshots'].append(str(path))
        return pixmap.toImage()

    def count_color(image, rgb):
        target = int(rgb.removeprefix('#'), 16)
        return sum((image.pixel(x, y) & 0xFFFFFF) == target
                   for y in range(0, image.height(), 2) for x in range(0, image.width(), 2))

    def run(label):
        check(window.run_button.isEnabled(), f'{label}: run control available')
        QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        check(window._thread is not None, f'{label}: actual Qt worker started')
        until = time.perf_counter() + 60
        while window._thread is not None:
            pump(20)
            if time.perf_counter() > until:
                raise TimeoutError(f'{label}: simulation worker timeout')
        check(window.result is not None and not window.result.checks['cancelled'], f'{label}: complete run received')
        check(all(window.result.checks.get(name) is True for name in REQUIRED_PHYSICAL_CHECKS),
              f'{label}: every required conservation/resource check passes')
        check(validation_row('Cargo and resources')[2] == 'Pass', f'{label}: model-check UI reports physical pass')

    try:
        window = CheckedWindow()
        window.resize(1360, 900)
        window.show()
        pump(200)
        check(window.dataset.annual_series == ACTUAL_HISTORY, 'GUI imports all 26 unchanged reported annual values with separate boxes/TEU units')
        check(len(window.forecasts) == 3, 'Exactly three supported annual series are displayed; no inferred Guam TEU')
        check(validation_row('Operational prediction')[2] == 'Not validated', 'Startup model checks do not claim operational validation')
        window.tabs.setCurrentIndex(3)
        for index, record in enumerate(window.forecasts):
            key = (record['port'], record['metric'])
            series = ACTUAL_HISTORY[key]
            values = [value for year, value in series]
            window.forecast_combo.setCurrentIndex(index)
            pump(30)
            methods = [independent_selection(values[:i]) for i in range(4, len(values))]
            predicted = [independent_prediction(values[:i], method)
                         for i, method in zip(range(4, len(values)), methods)]
            actual = values[4:]
            scores = independent_scores(actual, predicted)
            expected_rows = [[str(series[i][0]), number(values[i], 0), number(p, 0), number(p-values[i], 0),
                              number(abs(p-values[i])/values[i]*100)+'%', method, str(i-3)]
                             for i, p, method in zip(range(4, len(values)), predicted, methods)]
            check(cells(window.backtest_table) == expected_rows, f'{key}: every heldout FY, actual, prediction, signed error, percentage and inner-fold count matches independent arithmetic')
            for field, expected in scores.items():
                near(record[field], expected, f'{key}: independently calculated policy {field}')
            expected_benchmarks = []
            for method in METHODS:
                fixed = [independent_prediction(values[:i], method) for i in range(4, len(values))]
                metrics = independent_scores(actual, fixed)
                expected_benchmarks.append([method, number(metrics['wape_percent'])+'%', number(metrics['mae'], 0),
                    number(metrics['rmse'], 0), number(metrics['mean_error'], 0), number(independent_prediction(values, method), 0)])
            check(cells(window.method_table) == expected_benchmarks, f'{key}: fixed benchmark table matches independent MAE, RMSE, WAPE, bias and future estimates')
            latest_method = independent_selection(values)
            near(record['forecast'], independent_prediction(values, latest_method), f'{key}: following-data-year estimate uses current guarded selection')
            check(record['forecast_year'] == 2026 and record['history_end_year'] == 2025 and
                  'history ends FY2025' in window.forecast_summary.text(), f'{key}: period follows supplied history and is labelled explicitly')
            check('not a confidence interval' in window.forecast_note.text().lower() and
                  'not independent validation' in window.forecast_note.text().lower(), f'{key}: uncertainty and retrospective-development limits are visible')
            stem = f"forecast_{record['port'].lower()}_{record['metric'].lower()}"
            window.forecast_mode.setCurrentIndex(0)
            check(window.plot.mode == 'forecast' and window.plot.record is record, f'{key}: history chart uses selected evidence record')
            forecast_image = screenshot(stem+'_history', window.plot)
            check(count_color(forecast_image, '#c0790d') >= 10, f'{key}: estimate/error-envelope drawing is visibly rendered')
            screenshot(stem+'_desktop')
            window.forecast_mode.setCurrentIndex(1)
            check(window.plot.mode == 'backtests', f'{key}: heldout mode changes chart state')
            backtest_image = screenshot(stem+'_heldouts', window.plot)
            check(count_color(backtest_image, '#8154b8') >= 10 and backtest_image != forecast_image,
                  f'{key}: heldout predictions render as a distinct chart')
            diagnostics['independent_annual_accuracy'].append({'port': key[0], 'metric': key[1], 'targets': len(actual), **scores})

        # Exercise equivalent CSV and XLSX uploads and automatic matching-stem
        # settings. These are explicitly synthetic scenario inputs.
        headers = ['vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes', 'length_m', 'draft_m',
                   'box40_share', 'port', 'evidence_type', 'evidence_reference']
        rows = [[f'SYN-{i+1}', arrival, 18+i, 8+i, 180, 8, .5, 'Guam', 'synthetic',
                 'Controlled UI test; illustrative assumptions'] for i, arrival in enumerate([0, .25, .5, 1, 1.5, 2])]
        csv_path = output / 'synthetic_calls.csv'
        xlsx_path = output / 'synthetic_calls.xlsx'
        with csv_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream); writer.writerow(headers); writer.writerows(rows)
        workbook = openpyxl.Workbook()
        sheet = workbook.active; sheet.title = 'Synthetic calls'
        sheet.append(['Controlled synthetic UI fixture, not measured port operations'])
        sheet.append(headers)
        for row in rows: sheet.append(row)
        workbook.save(xlsx_path); workbook.close()
        synthetic_config = replace(TerminalConfig(), horizon_hours=12, berths=1, cranes=1, tractors=1,
            crane_moves_per_hour=10, tractor_cycle_minutes=8.123456, yard_capacity_boxes=20,
            import_dwell_hours=1, export_lead_hours=1, observation_hours=1, persistence_hours=1,
            view_fps=10, max_visual_containers=30, notes='Controlled synthetic UI fixture settings; no real calibration.')
        # CSV and XLSX share a sidecar by stem, as supported by the desktop.
        csv_path.with_suffix('.settings.json').write_text(json.dumps(synthetic_config.to_dict(), indent=2), encoding='utf-8')
        window.import_paths([str(csv_path)])
        csv_calls = [asdict(call) for call in window.dataset.calls]
        check(window.read_config().to_dict() == synthetic_config.to_dict(), 'CSV upload applies automatic settings with six-decimal precision')
        run('Synthetic CSV')
        csv_result = copy.deepcopy(window.result)
        check(window.result.kpis['operational_prediction_diagnostics']['status'] == 'not_validated' and
              validation_row('Operational prediction')[2] == 'not_validated', 'Synthetic simulation remains unvalidated in model-check table and engine diagnostics')
        check(window.vessel_table.rowCount() == 6 and all(row[6:] == ['—', '—'] for row in cells(window.vessel_table)),
              'Synthetic vessel table has all calls and no fabricated observed timing errors')
        check('unfinished waits truncated at cutoff' in window.kpi_label.text(), 'Run totals explicitly explain finite-horizon wait censoring')
        window.simulation_views.setCurrentIndex(1)
        window.slider.setValue(5000)
        near(window.operations_plot.hour, 6, 'Operation-chart cursor follows the authoritative replay slider')
        check(window.operations_plot.result is window.result and window.result.kpis['stock_flow_samples'], 'Operating chart receives actual recorded model stock-flow samples')
        operation_image = screenshot('synthetic_operations_chart', window.operations_plot)
        check(count_color(operation_image, '#167d9a') >= 10 and count_color(operation_image, '#c0790d') >= 10,
              'Both recorded queue and yard-fill traces visibly render')
        screenshot('synthetic_operations_desktop')
        window.tabs.setCurrentIndex(6); screenshot('synthetic_model_checks')
        window.fields['tractor_cycle_minutes'].setValue(9)
        window.fields['crane_moves_per_hour'].setValue(11)
        window.import_paths([str(xlsx_path)])
        check([asdict(call) for call in window.dataset.calls] == csv_calls and window.read_config().to_dict() == synthetic_config.to_dict(),
              'XLSX with introductory row imports identical calls and automatic settings as CSV')
        run('Synthetic XLSX')
        check(window.result.vessels == csv_result.vessels and window.result.checks == csv_result.checks and
              {k:v for k,v in window.result.kpis.items() if k != 'wall_seconds'} ==
              {k:v for k,v in csv_result.kpis.items() if k != 'wall_seconds'}, 'CSV/XLSX runs produce identical analytical outcomes, checks and KPI traces')

        # Known deterministic timing: ten crane moves at 10/h with a 0.1h
        # tractor pipeline complete A at 1.1h; B then starts/departs at 1.1h.
        observed_path = output / 'declared_observed_fixture.csv'
        observed_headers = ['vessel_id', 'arrival_utc', 'import_boxes', 'export_boxes', 'berth_start_utc',
                            'departure_utc', 'port', 'evidence_type', 'evidence_reference']
        observed_rows = [
            ['A', '2025-01-01T00:00:00Z', 10, 0, '2025-01-01T00:06:00Z', '2025-01-01T01:30:00Z', 'Guam', 'observed', 'Controlled analytical fixture; not collected port evidence'],
            ['B', '2025-01-01T00:12:00Z', 0, 0, '2025-01-01T01:12:00Z', '2025-01-01T01:24:00Z', 'Guam', 'observed', 'Controlled analytical fixture; not collected port evidence'],
        ]
        with observed_path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream); writer.writerow(observed_headers); writer.writerows(observed_rows)
        observed_config = replace(synthetic_config, horizon_hours=6, tractor_cycle_minutes=6, yard_capacity_boxes=10,
                                  service_variability=0, notes='Controlled declared-observation timing fixture for software checks; not empirical validation.')
        observed_path.with_suffix('.settings.json').write_text(json.dumps(observed_config.to_dict()), encoding='utf-8')
        window.import_paths([str(observed_path)])
        check([(c.arrival_hour, c.observed_berth_start_hour, c.observed_departure_hour) for c in window.dataset.calls]
              == [(0, .1, 1.5), (.2, 1.2, 1.4)], 'UTC arrivals and declared measured times share the same normalized port origin')
        run('Declared observed analytical fixture')
        expected_vessels = [['A', 'served', '0.00', '0.00', '1.10', '0.00', '-0.10', '-0.40'],
                            ['B', 'served', '0.20', '1.10', '1.10', '0.90', '-0.10', '-0.30']]
        check(cells(window.vessel_table) == expected_vessels, 'Every observed-fixture outcome and signed timing error matches independent analytical timing')
        diagnostic = window.result.kpis['operational_prediction_diagnostics']
        near(diagnostic['berth_start']['mae_hours'], .1, 'Observed-fixture berth MAE is 0.1h')
        near(diagnostic['departure']['mae_hours'], .35, 'Observed-fixture departure MAE is 0.35h')
        near(diagnostic['departure']['rmse_hours'], math.sqrt(.125), 'Observed-fixture departure RMSE uses both paired errors')
        near(diagnostic['departure']['bias_hours'], -.35, 'Observed-fixture bias is predicted minus declared observed time')
        check(validation_row('Operational prediction')[1:] == ['Berth pairs 2; departure pairs 2',
              'observations_compared_not_independently_validated', 'Paired measured timings permit hindcast errors; independent validation needs separate records.'],
              'Model-check table exposes both timing-pair coverage and same-run hindcast limitation')
        detail = json.loads(window.validation_detail.toPlainText())
        check(detail['operational_prediction'] == diagnostic, 'Detailed model-check JSON preserves all timing errors, coverage and interpretation')
        window.simulation_views.setCurrentIndex(1); screenshot('declared_observed_operations')
        window.vessel_table.selectRow(1)
        check(window.berth_combo.currentData() == 0 and window.simulation_views.currentIndex() == 0,
              'Selecting a berthed vessel focuses its actual berth and replay view')
        window.tabs.setCurrentIndex(6); screenshot('declared_observed_model_checks')
        original_result = window.result
        invalid_result = copy.deepcopy(original_result); invalid_result.checks['export_conserved'] = False
        window.result = invalid_result; window.show_result(invalid_result)
        check(validation_row('Cargo and resources')[2] == 'Failed: export_conserved', 'Model-check UI reports an injected accounting failure instead of a pass')
        window.result = original_result; window.show_result(original_result)

        project = output / 'observed_roundtrip.portlab.json'
        expected_calls = [asdict(c) for c in window.dataset.calls]
        expected_history = copy.deepcopy(window.dataset.annual_series)
        expected_config = window.read_config().to_dict()
        window.save_project_path(project)
        window.fields['crane_moves_per_hour'].setValue(11)
        check('stale' in window.validation_summary.text(), 'Settings edits visibly mark model checks stale')
        window.load_project_path(project)
        check([asdict(c) for c in window.dataset.calls] == expected_calls and window.dataset.annual_series == expected_history and
              window.read_config().to_dict() == expected_config, 'Project roundtrip preserves UTC-normalized measured fields, reported histories, units and precise settings')
        check(window.result is None and window.operations_plot.result is None and window.vessel_table.rowCount() == 0 and
              validation_row('Operational prediction')[2] == 'Not validated', 'Opening a project clears old results, operation plots, timing rows and validation status')
        malformed = json.loads(project.read_text(encoding='utf-8'))
        malformed['calls'][1]['vessel_id'] = 'a'
        bad_project = output / 'rejected_duplicate_project.portlab.json'
        bad_project.write_text(json.dumps(malformed), encoding='utf-8')
        try:
            window.load_project_path(bad_project)
        except ValueError as exc:
            diagnostics['malformed_project_error'] = str(exc)
        else:
            raise AssertionError('Case-insensitive duplicate-ID project was accepted by UI')
        check([asdict(c) for c in window.dataset.calls] == expected_calls and window.read_config().to_dict() == expected_config,
              'Rejected malformed project leaves active dataset/settings untouched')
        run('Restored observed project')
        check(cells(window.vessel_table) == expected_vessels and window.result.kpis['operational_prediction_diagnostics'] == diagnostic,
              'Restored project reproduces analytical timing errors and coverage')
        report_path = window.export_to(output / 'reports')
        report = json.loads((report_path / 'complete_report.json').read_text(encoding='utf-8'))
        check(report['simulation']['kpis']['operational_prediction_diagnostics'] == diagnostic and
              report['simulation']['vessels'] == window.result.vessels, 'Desktop export retains measured-versus-predicted timings and diagnostics')
        check(all(row['backtests'] == next(r for r in window.forecasts if (r['port'],r['metric']) == (row['port'],row['metric']))['backtests']
                  for row in report['forecasts']), 'Desktop export preserves audited observed annual holdout predictions')
        diagnostics['report_directory'] = str(report_path)
        diagnostics['operational_fixture_diagnostics'] = diagnostic
        diagnostics['required_physical_checks'] = list(REQUIRED_PHYSICAL_CHECKS)
        # This public fixture ships with a fresh clone and source ZIP. No
        # archived chat directory or previous local verification is required.
        curated = ROOT / 'examples' / 'synthetic_import_test' / 'guam_synthetic_test.csv'
        check(curated.is_file(), 'Curated synthetic import example is present in the repository')
        window.import_paths([str(curated)])
        example_calls = [asdict(call) for call in window.dataset.calls]
        example_config = window.read_config().to_dict()
        check(len(example_calls) == 32 and all(row['evidence_type'] == 'synthetic' for row in example_calls),
              'Public example imports all 32 explicitly synthetic calls')
        run('Curated public synthetic example')
        check(window.result.kpis['requested_calls'] == 32 and
              window.result.kpis['operational_prediction_diagnostics']['status'] == 'not_validated',
              'Public example has complete input coverage and does not claim operational validation')
        example_result = copy.deepcopy(window.result.vessels)
        window.import_paths([str(curated.with_suffix('.xlsx'))])
        check([asdict(call) for call in window.dataset.calls] == example_calls and
              window.read_config().to_dict() == example_config,
              'Curated public CSV/XLSX import the same schedule and automatic settings')
        run('Curated public synthetic XLSX')
        check(window.result.vessels == example_result,
              'Curated public CSV/XLSX produce identical deterministic vessel outcomes')
        diagnostics['curated_example'] = {'path': curated.relative_to(ROOT).as_posix(),
                                          'calls': len(example_calls),
                                          'status': window.result.kpis['operational_prediction_diagnostics']['status']}
        thread_fragments = ('QThread: Destroyed', 'different thread', 'Cannot queue arguments', 'QObject::setParent')
        check(not any(any(fragment in row['message'] for fragment in thread_fragments) for row in diagnostics['qt_messages']),
              'No Qt thread-affinity or lifecycle errors')
        diagnostics['passed'] = True
    except Exception as exc:
        diagnostics['failure'] = f'{type(exc).__name__}: {exc}'
        diagnostics['traceback'] = traceback.format_exc()
        print(diagnostics['failure'], flush=True)
        if window:
            window.grab().save(str(output / 'failure.png'))
    finally:
        if window is not None:
            if window._thread is not None:
                window._stop.set()
                until = time.perf_counter() + 20
                while window._thread is not None and time.perf_counter() < until:
                    app.processEvents(); QTest.qWait(20)
            diagnostics['application_errors'] = window.check_errors
            window.close(); app.processEvents()
        diagnostics['wall_seconds'] = round(time.perf_counter() - started, 3)
        diagnostics['peak_process_rss_mib'] = round(max(peak_rss, process.memory_info().rss) / 2**20, 1)
        (output / 'diagnostics.json').write_text(json.dumps(diagnostics, indent=2, allow_nan=False), encoding='utf-8')
        if diagnostics['passed']:
            (output / 'failure.png').unlink(missing_ok=True)
        qInstallMessageHandler(old_handler); sys.excepthook = old_hook
    print(json.dumps({'passed': diagnostics['passed'], 'checks': len(diagnostics['checks']),
                      'failure': diagnostics.get('failure'), 'wall_seconds': diagnostics['wall_seconds'],
                      'peak_process_rss_mib': diagnostics['peak_process_rss_mib'], 'output': str(output)}, indent=2))
    return 0 if diagnostics['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
