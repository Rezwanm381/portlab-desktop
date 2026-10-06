from __future__ import annotations
import sys
import argparse
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description='PortLab local desktop port planning')
    parser.add_argument('--smoke',action='store_true',help='Run built-in integration and visual checks, save diagnostics, then exit')
    parser.add_argument('--output',default='verification',help='Smoke-test output directory')
    parser.add_argument('--project',help='Open a saved project')
    args=parser.parse_args()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from .ui import MainWindow
    app=QApplication(sys.argv[:1]);app.setApplicationName('PortLab');app.setOrganizationName('PortLab')
    window=MainWindow();window.show()
    if args.project:window.load_project_path(Path(args.project))
    if args.smoke:
        def smoke():
            try:
                from .engine import run_simulation,compare_candidates
                output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
                result=run_simulation(window.selected_calls(),window.read_config())
                window.result=result;window.result_dataset=window.dataset;window.result_forecasts=window.forecasts
                window.show_result(result);window.tabs.setCurrentIndex(2)
                window.receive_recommendations(compare_candidates(window.selected_calls(),window.read_config()))
                # Pick an authoritative handling-event time, rather than an arbitrary blank scene.
                event=next((e for e in result.events if e['type']=='crane_start'),None)
                capture_hour=(float(event.get('start_time',event['time']))+.01) if event else 1.0
                window.seek_replay(round(capture_hour / max(result.kpis['simulated_hours'],.001) * 10000))
                window.progress_bar.setValue(1000)
                window.update_enabled()
                def capture():
                    window.update_resources();window.grab().save(str(output/'desktop_simulation.png'))
                    window.simulation_views.setCurrentIndex(1);window.grab().save(str(output/'desktop_operations.png'))
                    window.simulation_views.setCurrentIndex(0)
                    window.tabs.setCurrentIndex(3);window.grab().save(str(output/'desktop_forecasts.png'))
                    window.forecast_mode.setCurrentIndex(1);window.grab().save(str(output/'desktop_prediction_errors.png'))
                    window.tabs.setCurrentIndex(6);window.grab().save(str(output/'desktop_model_checks.png'))
                    window.tabs.setCurrentIndex(1);window.grab().save(str(output/'desktop_settings.png'))
                    report_dir=window.export_to(output)
                    from . import __version__
                    diagnostics={'version':__version__,'checks':result.checks,'kpis':result.kpis,'recommendation_status':window.recommendations.get('status'),
                        'candidates':len(window.recommendations.get('candidates',[])),'peak_process_mb':window._peak_memory,'visual':window.viewport.diagnostics(),
                        'report_dir':str(report_dir),'source':'actual desktop integration smoke test'}
                    (output/'smoke_diagnostics.json').write_text(json.dumps(diagnostics,indent=2,default=str),encoding='utf-8')
                    print(json.dumps({'checks':result.checks,'peak_process_mb':window._peak_memory,
                        'recommendation_status':diagnostics['recommendation_status'],'visual':diagnostics['visual'],
                        'report_dir':str(report_dir)},default=str),flush=True)
                    window.close();app.quit()
                QTimer.singleShot(1400,capture)
            except Exception:
                import traceback
                traceback.print_exc();window.close();app.exit(1)
        QTimer.singleShot(500,smoke)
    return app.exec()

if __name__=='__main__':
    raise SystemExit(main())
