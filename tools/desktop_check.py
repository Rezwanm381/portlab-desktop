"""Exercise the real Qt worker/project/replay workflow with fixture paths.

Run with the project virtualenv. No native desktop automation or network is
used; buttons and signals belong to this application instance.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import psutil

from portlab.engine import REQUIRED_PHYSICAL_CHECKS
from portlab.ui import MainWindow


class CheckedWindow(MainWindow):
    def __init__(self):
        self.check_errors = []
        super().__init__()

    def error(self, message):
        # A test must record/report an error instead of opening a modal dialog.
        self.check_errors.append(str(message))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'output' / 'desktop_check')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    diagnostics = {'passed': False, 'checks': [], 'worker_phases': [], 'qt_messages': [],
                   'python_exceptions': [], 'output_directory': str(output)}
    started = time.perf_counter()
    process = psutil.Process()
    peak_rss = process.memory_info().rss

    def qt_message(kind, context, text):
        diagnostics['qt_messages'].append({'kind': str(kind), 'text': text})

    old_handler = qInstallMessageHandler(qt_message)
    old_hook = sys.excepthook
    def exception_hook(kind, value, tb):
        import traceback
        diagnostics['python_exceptions'].append(''.join(traceback.format_exception(kind, value, tb)))
    sys.excepthook = exception_hook
    app = QApplication.instance() or QApplication(['PortLab desktop verification'])
    app.setQuitOnLastWindowClosed(False)
    window = None

    def check(condition, description):
        if not condition:
            raise AssertionError(description)
        diagnostics['checks'].append(description)

    def wait_for(predicate, label, timeout=60):
        nonlocal peak_rss
        until = time.perf_counter() + timeout
        while not predicate():
            app.processEvents()
            peak_rss = max(peak_rss, process.memory_info().rss)
            if window and window.check_errors:
                raise AssertionError(f'{label}: application errors: {window.check_errors}')
            if diagnostics['python_exceptions']:
                raise AssertionError(f'{label}: unhandled Python exception')
            if time.perf_counter() >= until:
                raise TimeoutError(f'{label} did not finish within {timeout} seconds')
            QTest.qWait(20)
        app.processEvents()

    def click(button):
        check(button.isEnabled(), f'Control available: {button.text()}')
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    def worker_phase(button, label, cancel=False):
        phase_start = time.perf_counter()
        print(f'Checking {label}...', flush=True)
        click(button)
        check(window._thread is not None, f'{label}: real QThread started')
        check(not window.run_button.isEnabled(), f'{label}: run locked while worker runs')
        check(not window.port_combo.isEnabled(), f'{label}: terminal locked while worker runs')
        check(not window.demo_button.isEnabled(), f'{label}: demo replacement locked while worker runs')
        check(window.stop_button.isEnabled(), f'{label}: cancellation available')
        if cancel:
            click(window.stop_button)
        wait_for(lambda: window._thread is None, label)
        check(window._worker is None, f'{label}: worker cleaned up')
        check(not window.stop_button.isEnabled(), f'{label}: cancellation disabled after cleanup')
        check(window.run_button.isEnabled(), f'{label}: next run available after cleanup')
        check(window.port_combo.isEnabled(), f'{label}: terminal available after cleanup')
        diagnostics['worker_phases'].append({'name': label,
                                             'wall_seconds': round(time.perf_counter() - phase_start, 3)})

    def full_result_checks(label):
        check(window.result is not None, f'{label}: simulation result received')
        check(not window.result.checks['cancelled'], f'{label}: complete horizon received')
        check(all(window.result.checks.get(name) is True for name in REQUIRED_PHYSICAL_CHECKS),
              f'{label}: all required physical/accounting checks passed')
        check(window.compare_button.isEnabled(), f'{label}: comparison available')
        check(window.export_button.isEnabled(), f'{label}: export available')

    try:
        window = CheckedWindow()
        window.resize(1360, 850)
        window.show()
        QTest.qWait(250)
        check(not window.check_errors, 'Bundled demo loaded without dialogs/errors')
        check(window.result is None, 'Startup has no fabricated completed result')
        check(not window.compare_button.isEnabled(), 'Startup comparison unavailable without a run')

        # Use the same importer as the file dialog, including the automatic
        # same-stem settings sidecar. Import failures must be transactional.
        initial_config = window.read_config()
        import_fixture = output / 'sidecar_calls.csv'
        fixture_calls = [asdict(call) for call in window.selected_calls()]
        with import_fixture.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(fixture_calls[0]))
            writer.writeheader()
            writer.writerows(fixture_calls)
        sidecar = import_fixture.with_suffix('.settings.json')
        sidecar_config = initial_config.to_dict()
        sidecar_config['tractor_cycle_minutes'] = 8.123456
        sidecar.write_text(json.dumps(sidecar_config), encoding='utf-8')
        window.import_paths([str(import_fixture)])
        check(window.read_config().tractor_cycle_minutes == 8.123456,
              'Dataset import automatically applies matching settings sidecar with six-decimal precision')
        calls_before_bad_import = [asdict(call) for call in window.dataset.calls]
        config_before_bad_import = window.read_config().to_dict()
        invalid = dict(sidecar_config, tractors=0)
        sidecar.write_text(json.dumps(invalid), encoding='utf-8')
        try:
            window.import_paths([str(import_fixture)])
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid automatic settings sidecar was accepted')
        check([asdict(call) for call in window.dataset.calls] == calls_before_bad_import and
              window.read_config().to_dict() == config_before_bad_import,
              'Invalid settings sidecar leaves dataset and settings untouched')
        window.load_demo()

        worker_phase(window.run_button, 'Initial simulation')
        full_result_checks('Initial simulation')
        check(window.result.kpis['gate_status'] == 'eligible', 'Demo raw queue gate is eligible')
        worker_phase(window.compare_button, 'Initial paired comparison')
        check(window.recommendations['status'] == 'evaluated', 'Real paired comparison evaluated')
        check(bool(window.recommendations['candidates']), 'Comparison populated candidate table')
        check(window.rec_table.rowCount() == len(window.recommendations['candidates']),
              'Candidate table matches analytical output')

        worker_phase(window.run_button, 'Cancelled simulation', cancel=True)
        check(window.result.checks['cancelled'], 'Cancel button returns partial simulation')
        check(all(window.result.checks.get(name) is True for name in REQUIRED_PHYSICAL_CHECKS),
              'Cancellation retains consistent physical/accounting state')
        check(not window.compare_button.isEnabled(), 'Partial cancelled result cannot trigger recommendations')
        worker_phase(window.run_button, 'Rerun after cancellation')
        full_result_checks('Rerun after cancellation')

        previous = window.result
        rate = window.fields['crane_moves_per_hour'].value()
        window.fields['crane_moves_per_hour'].setValue(rate + 1)
        check(window.stale, 'Editing an analytical setting marks result stale')
        check(window.result is previous, 'Stale marking preserves the previous result snapshot')
        check(not window.compare_button.isEnabled(), 'Stale result cannot trigger recommendations')
        window.fields['crane_moves_per_hour'].setValue(rate)
        worker_phase(window.run_button, 'Refresh stale result')
        full_result_checks('Refresh stale result')
        check(not window.stale, 'Completed rerun clears stale marker')

        fixture = output / 'desktop_roundtrip.portlab.json'
        expected_config = window.read_config().to_dict()
        expected_calls = [asdict(call) for call in window.dataset.calls]
        expected_history = dict(window.dataset.annual_series)
        window.save_project_path(fixture)
        check(fixture.is_file(), 'Project saved through actual project method')
        window.fields['tractor_cycle_minutes'].setValue(window.fields['tractor_cycle_minutes'].value() + 2)
        window.load_project_path(fixture)
        check(window.read_config().to_dict() == expected_config, 'Project settings survive save/load roundtrip')
        check([asdict(call) for call in window.dataset.calls] == expected_calls,
              'Project call records survive save/load roundtrip')
        check(window.dataset.annual_series == expected_history, 'Annual metric units/history survive project roundtrip')
        check(window.result is None and window.recommendations is None,
              'Opening project clears previous simulation/comparison')
        check(window.viewport.diagnostics()['trace_complete'] is None,
              'Opening project clears previous visual replay trace')
        check(not window.compare_button.isEnabled(), 'Restored project requires a fresh simulation')
        worker_phase(window.run_button, 'Restored project simulation')
        full_result_checks('Restored project simulation')
        worker_phase(window.compare_button, 'Restored project comparison')
        check(window.recommendations['status'] == 'evaluated', 'Restored project comparison completes')

        window.tabs.setCurrentIndex(2)
        window.slider.setValue(3000)
        hour_before = window._clock_hour
        frames_before = window.viewport.diagnostics()['frames_rendered']
        click(window.play_button)
        QTest.qWait(700)
        check(window._clock_hour > hour_before, 'Playback advances the authoritative result clock')
        click(window.play_button)
        check(not window._playing, 'Playback pause stops clock advancement')
        view = window.viewport.diagnostics()
        check(view['error'] is None, 'Local Panda3D renderer completes without error')
        check(view['frames_rendered'] > frames_before, 'Playback produces additional rendered frames')
        check(view['container_pool'] <= window.result.config.max_visual_containers,
              'Visual container pool respects configured resource limit')
        check(view['trace_complete'] is True, 'Demo animation has the complete authoritative event trace')
        window.update_resources()
        screenshot = output / 'desktop_simulation.png'
        check(window.grab().save(str(screenshot)), 'Full desktop screenshot saved')
        check(window.viewport.save_screenshot(output / 'port_replay.png'), 'Simulation viewport screenshot saved')
        report_path = window.export_to(output / 'reports')
        report = json.loads((report_path / 'complete_report.json').read_text(encoding='utf-8'))
        check(report['simulation']['kpis']['served_calls'] == window.result.kpis['served_calls'],
              'Exported report preserves run KPIs')
        check(report['recommendations']['status'] == 'evaluated', 'Exported report includes evaluated comparisons')
        check(report['simulation']['config'] == window.result.config.to_dict(),
              'Exported report uses run settings snapshot')
        check((report_path / 'results.xlsx').is_file() and (report_path / 'report.html').is_file(),
              'Excel and HTML reports generated by desktop export')
        check(not window.check_errors, 'No application/worker error dialogs across the workflow')
        thread_warning_fragments = ('QThread: Destroyed', 'different thread', 'Cannot queue arguments', 'QObject::setParent')
        check(not any(any(fragment in row['text'] for fragment in thread_warning_fragments)
                      for row in diagnostics['qt_messages']), 'No Qt thread-affinity/lifecycle errors')
        check(not diagnostics['python_exceptions'], 'No unhandled Python/Qt callback exceptions')
        diagnostics.update({'passed': True, 'visual': view,
                            'simulation_kpis': {key: value for key, value in window.result.kpis.items()
                                                if key != 'stock_flow_samples'},
                            'simulation_checks': window.result.checks,
                            'preferred_candidate': window.recommendations.get('preferred_candidate'),
                            'report_directory': str(report_path), 'screenshot': str(screenshot)})
    except Exception as exc:
        import traceback
        diagnostics['failure'] = f'{type(exc).__name__}: {exc}'
        diagnostics['traceback'] = traceback.format_exc()
    finally:
        if window is not None:
            if window._thread is not None:
                window._stop.set()
                try:
                    wait_for(lambda: window._thread is None, 'Cleanup', timeout=20)
                except Exception as exc:
                    diagnostics['cleanup_error'] = str(exc)
            diagnostics['application_errors'] = window.check_errors
            window.close()
            app.processEvents()
        diagnostics['wall_seconds'] = round(time.perf_counter() - started, 3)
        diagnostics['peak_process_rss_mib'] = round(max(peak_rss, process.memory_info().rss) / 2**20, 1)
        (output / 'diagnostics.json').write_text(json.dumps(diagnostics, indent=2, default=str), encoding='utf-8')
        qInstallMessageHandler(old_handler)
        sys.excepthook = old_hook
    print(json.dumps({'passed': diagnostics['passed'], 'checks_passed': len(diagnostics['checks']),
                      'wall_seconds': diagnostics['wall_seconds'],
                      'peak_process_rss_mib': diagnostics['peak_process_rss_mib'],
                      'failure': diagnostics.get('failure'),
                      'diagnostics': str(output / 'diagnostics.json')}, indent=2))
    return 0 if diagnostics['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
