"""Exercise real local GPU rendering, cameras, trace honesty and bounded pools."""
from pathlib import Path
from dataclasses import replace
import argparse
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from portlab.contracts import TerminalConfig, VesselCall
from portlab.engine import run_simulation
from portlab.visual import PortViewport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='output/visual_smoke_v2')
    parser.add_argument('--port', choices=('Guam', 'Conley'), default='Guam')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = TerminalConfig(port=args.port, horizon_hours=48, initial_yard_boxes=220,
                            cranes=4, cranes_per_vessel=2, tractors=12)
    calls = [VesselCall(f'{args.port.upper()}-{index + 1:03d}', index * .15,
                        80 + index * 7, 32 + index * 3, length_m=180 + (index % 3) * 25,
                        port=args.port) for index in range(12)]
    result = run_simulation(calls, config)
    export = next(row for row in result.intervals if row['resource'] == 'crane' and row['flow'] == 'export')
    export_hour = export['start_time'] + (export['end_time'] - export['start_time']) * .4
    truncated = replace(result, events=result.events[:180], checks={**result.checks,
                        'trace_captured': True, 'trace_complete': False})
    stop = [False]
    def cancellation_progress(fraction, message):
        stop[0] = fraction >= .1
    cancelled = run_simulation(calls, config, progress=cancellation_progress,
                               cancel=lambda: stop[0])
    future = run_simulation([VesselCall('FUTURE-APPOINTMENT', 12, 0, 150, port=args.port)],
                            replace(config, horizon_hours=10, export_lead_hours=5))
    app = QApplication.instance() or QApplication(sys.argv)
    view = PortViewport()
    view.resize(1200, 700)
    view.show()
    view.load_result(result)
    steps = [0]
    started = time.perf_counter()
    phases = [('operations.png', 'operations', None, 4.5, result),
              ('berth_detail.png', 'operations', 0, 4.5, result),
              ('overview.png', 'overview', None, 4.5, result),
              ('top.png', 'top', None, 4.5, result),
              ('export_detail.png', 'operations', 0, export_hour, result),
              ('partial_trace.png', 'operations', None, 4.5, truncated),
              ('cancelled.png', 'operations', 0, config.horizon_hours, cancelled),
              ('future_staging.png', 'operations', None, 10, future)]
    snapshots = []
    benchmark_seconds = [None]

    def capture_next():
        if not phases:
            diagnostics = view.diagnostics()
            diagnostics.update(elapsed_seconds=round(time.perf_counter() - started, 3),
                               benchmark_seconds=benchmark_seconds[0], snapshots=snapshots,
                               simulation_checks=result.checks)
            try:
                import psutil
                diagnostics['process_rss_mib'] = round(psutil.Process().memory_info().rss / 2**20, 1)
            except ImportError:
                pass
            (output / 'diagnostics.json').write_text(json.dumps(diagnostics, indent=2), encoding='utf-8')
            print(json.dumps({key: diagnostics[key] for key in ('error', 'driver_renderer', 'frames_rendered',
                             'mean_render_ms', 'max_render_ms', 'benchmark_seconds', 'process_rss_mib')}, indent=2))
            view.shutdown()
            app.quit()
            return
        filename, mode, berth, hour, replay_result = phases.pop(0)
        view.load_result(replay_result)
        view.set_camera_mode(mode)
        view.focus_berth(berth)
        view.set_simulation_time(hour)
        if filename == 'berth_detail.png':
            view._selected_resource = ('crane', 0)
            view._update_scene()

        def capture():
            if not view.save_screenshot(output / filename):
                raise RuntimeError(f'Unable to save {filename}')
            snapshots.append({'file': filename, 'status': view.replay_status()})
            QTimer.singleShot(80, capture_next)
        QTimer.singleShot(100, capture)

    def advance():
        steps[0] += 1
        view.set_simulation_time(4 + steps[0] / 180)
        if steps[0] >= 90:
            timer.stop()
            benchmark_seconds[0] = round(time.perf_counter() - started, 3)
            capture_next()

    timer = QTimer()
    timer.timeout.connect(advance)
    timer.start(34)
    app.exec()
    return 1 if view.diagnostics()['error'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
