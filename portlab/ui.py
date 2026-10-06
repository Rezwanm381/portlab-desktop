from __future__ import annotations

import copy
import json
import math
import time
import threading
from dataclasses import asdict
from pathlib import Path

import psutil
from PySide6.QtCore import Qt, QThread, QObject, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QFileDialog, QMessageBox, QTabWidget, QFormLayout,
    QGroupBox, QScrollArea, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QSlider, QSplitter, QProgressBar, QAbstractItemView)

from .contracts import TerminalConfig, VesselCall, DatasetBundle, SimulationResult
from .paths import data_path, user_root
from .charts import ForecastPlot, OperationsPlot


class Work(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, stop):
        super().__init__()
        self.operation = operation
        self.stop = stop

    def run(self):
        try:
            self.finished.emit(self.operation(self.progress.emit, self.stop))
        except Exception as exc:
            import traceback
            self.failed.emit(f'{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}')


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setAlternatingRowColors(True)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.verticalHeader().setVisible(False)
    return widget


def fill_table(widget, rows):
    widget.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            item = QTableWidgetItem('—' if value is None else str(value))
            item.setToolTip(item.text())
            widget.setItem(i, j, item)


def display_number(value, digits=2):
    return f'{value:,.{digits}f}' if isinstance(value,(int,float)) and math.isfinite(value) else '—'


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from .visual import PortViewport
        from . import __version__
        self.setWindowTitle(f'PortLab {__version__} · Desktop port planning')
        self.resize(1360, 850)
        self.config = TerminalConfig()
        self.dataset = DatasetBundle()
        self.result = None
        self.recommendations = None
        self.forecasts = []
        self.result_dataset = None
        self.result_forecasts = []
        self.stale = False
        self._loading = False
        self._thread = None
        self._worker = None
        self._stop = threading.Event()
        self._process = psutil.Process()
        self._peak_memory = 0
        self._clock_hour = 0.0
        self._last_tick = time.perf_counter()
        self._playing = False
        self._project_path = None
        self._last_reports = []

        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(18, 12, 18, 10)
        heading = QHBoxLayout()
        title = QLabel('PortLab'); title.setObjectName('brand')
        subtitle = QLabel('Local forecasts, scenario simulation and 3D replay')
        subtitle.setObjectName('muted')
        heading.addWidget(title); heading.addWidget(subtitle); heading.addStretch()
        self.port_combo = QComboBox(); self.port_combo.addItems(['Guam', 'Conley'])
        self.port_combo.currentTextChanged.connect(self.change_port)
        heading.addWidget(QLabel('Terminal')); heading.addWidget(self.port_combo)
        outer.addLayout(heading)
        controls = QHBoxLayout()
        self.import_button = self.button('Import data', self.import_data)
        self.open_button = self.button('Open project', self.open_project)
        self.save_button = self.button('Save project', self.save_project)
        self.run_button = self.button('Run simulation', self.run_simulation); self.run_button.setObjectName('primary')
        self.compare_button = self.button('Compare actions', self.compare_actions)
        self.stop_button = self.button('Cancel run', self.cancel_run); self.stop_button.setEnabled(False)
        for b in (self.import_button, self.open_button, self.save_button): controls.addWidget(b)
        controls.addStretch()
        for b in (self.run_button, self.compare_button, self.stop_button): controls.addWidget(b)
        outer.addLayout(controls)
        self.banner = QLabel(); self.banner.setWordWrap(True); self.banner.setObjectName('banner')
        outer.addWidget(self.banner)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)

        project = QWidget(); layout = QVBoxLayout(project)
        row = QHBoxLayout()
        row.addWidget(QLabel('Dataset overview')); row.addStretch()
        self.demo_button = self.button('Load bundled demo', self.load_demo)
        row.addWidget(self.demo_button)
        row.addWidget(self.button('Open input templates', self.open_templates))
        layout.addLayout(row)
        self.data_summary = QLabel(); self.data_summary.setWordWrap(True); layout.addWidget(self.data_summary)
        self.call_table = table(['Vessel', 'Arrival hour', 'Import boxes', 'Export boxes', 'Length m', 'Draft m', 'Evidence'])
        layout.addWidget(self.call_table, 2)
        self.evidence = QPlainTextEdit(); self.evidence.setReadOnly(True); self.evidence.setMaximumHeight(160)
        layout.addWidget(QLabel('Data notes and assumptions')); layout.addWidget(self.evidence)
        self.tabs.addTab(project, 'Project & data')

        settings = QScrollArea(); settings.setWidgetResizable(True)
        content = QWidget(); settings.setWidget(content); form_outer = QVBoxLayout(content)
        actions = QHBoxLayout()
        actions.addWidget(self.button('Import settings', self.import_settings))
        actions.addWidget(self.button('Export settings', self.export_settings))
        actions.addWidget(self.button('Restore port preset', self.restore_preset)); actions.addStretch()
        form_outer.addLayout(actions)
        self.fields = {}
        groups = [
            ('Run and workload', [('horizon_hours','Run horizon (hours)',1,8760,True),('demand_multiplier','Workload multiplier',0.05,20,True),('seed','Random seed',0,2147483647,False),('replications','Paired comparison replications',2,30,False),('service_variability','Service variability (CV)',0,1,True)]),
            ('Berths and handling resources', [('berths','Available berths',1,8,False),('cranes','Available quay cranes',1,32,False),('cranes_per_vessel','Cranes assigned per vessel',1,8,False),('crane_moves_per_hour','Moves per working crane-hour',0.1,150,True),('tractors','Available terminal tractors',1,128,False),('tractor_cycle_minutes','Tractor cycle (minutes)',0.1,120,True)]),
            ('Yard and vessel compatibility', [('yard_capacity_boxes','Yard capacity (physical boxes)',1,1000000,False),('initial_yard_boxes','Initial yard inventory (boxes)',0,1000000,False),('import_dwell_hours','Import yard dwell (hours)',0,720,True),('export_lead_hours','Export staging lead time (hours)',0,720,True),('max_vessel_length_m','Maximum vessel length (metres)',1,500,True),('max_vessel_draft_m','Maximum operating draft (metres)',1,25,True)]),
            ('Recommendation rules', [('observation_hours','Minimum observation (hours)',0,8760,True),('persistence_hours','Uninterrupted pressure (hours)',0,8760,True),('pressure_queue_calls','Pressure threshold (waiting vessels)',1,1000,False)]),
            ('Visual resource budget', [('view_fps','Playback frame-rate cap',5,30,False),('max_visual_containers','Maximum container visuals',1,120,False)])]
        grid = QHBoxLayout(); columns = [QVBoxLayout(), QVBoxLayout()]
        for index, (name, specs) in enumerate(groups):
            group = QGroupBox(name); form = QFormLayout(group)
            for key, label, low, high, decimal in specs:
                spin = QDoubleSpinBox() if decimal else QSpinBox()
                spin.setRange(low, high)
                if decimal: spin.setDecimals(6); spin.setSingleStep(.1 if high <= 1 else 1)
                spin.setValue(getattr(self.config, key)); spin.setMinimumWidth(130)
                spin.valueChanged.connect(self.settings_changed)
                self.fields[key] = spin; form.addRow(label, spin)
            columns[index % 2].addWidget(group)
        for column in columns: column.addStretch(); grid.addLayout(column, 1)
        form_outer.addLayout(grid)
        self.settings_notes = QPlainTextEdit(); self.settings_notes.setMaximumHeight(80)
        self.settings_notes.textChanged.connect(self.settings_changed)
        form_outer.addWidget(QLabel('Parameter provenance / assumptions')); form_outer.addWidget(self.settings_notes)
        rules_note = QLabel('168-hour observation and 24-hour continuous pressure are the default project rules. Edited values are sensitivity cases. Changing a threshold does not establish empirical validity. Cranes and transport rates require dated operational evidence.')
        rules_note.setWordWrap(True); rules_note.setObjectName('muted'); form_outer.addWidget(rules_note)
        self.tabs.addTab(settings, 'Settings')

        simulation = QWidget(); layout = QVBoxLayout(simulation)
        self.kpi_label = QLabel('Run a scenario to populate operational KPIs.'); self.kpi_label.setWordWrap(True)
        self.kpi_label.setObjectName('kpis'); layout.addWidget(self.kpi_label)
        self.viewport = PortViewport()
        self.simulation_views = QTabWidget()
        scene = QWidget(); scene_layout = QVBoxLayout(scene); scene_layout.setContentsMargins(0,0,0,0)
        scene_layout.addWidget(self.viewport,1)
        self.replay_status = QLabel('No replay loaded'); scene_layout.addWidget(self.replay_status)
        self.simulation_views.addTab(scene,'3D replay')
        operating = QWidget(); operating_layout = QVBoxLayout(operating)
        self.operations_plot = OperationsPlot(); operating_layout.addWidget(self.operations_plot,1)
        self.operating_note = QLabel('Run a scenario to view vessel outcomes and any supplied observed timings.')
        self.operating_note.setWordWrap(True); operating_layout.addWidget(self.operating_note)
        self.vessel_table = table(['Call','State','Arrival h','Predicted berth h','Predicted departure h','Wait through cutoff h','Berth error h','Departure error h'])
        self.vessel_table.itemSelectionChanged.connect(self.focus_selected_vessel)
        operating_layout.addWidget(self.vessel_table,1)
        self.simulation_views.addTab(operating,'Queues, yard & vessel outcomes')
        layout.addWidget(self.simulation_views,1)
        replay = QHBoxLayout()
        self.play_button = self.button('Play', self.toggle_play); replay.addWidget(self.play_button)
        replay.addWidget(self.button('Restart', self.restart_replay))
        self.speed = QComboBox()
        for label, value in [('0.05 h/s',.05),('0.25 h/s',.25),('1 h/s',1),('4 h/s',4),('12 h/s',12)]: self.speed.addItem(label, value)
        self.speed.setCurrentIndex(1); replay.addWidget(self.speed)
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setRange(0,10000)
        self.slider.valueChanged.connect(self.seek_replay); replay.addWidget(self.slider, 1)
        self.clock_label = QLabel('0.00 h'); replay.addWidget(self.clock_label)
        self.camera_combo = QComboBox()
        for label,mode in [('Operations','operations'),('Overview','overview'),('Top view','top'),('Orbit view','orbit')]: self.camera_combo.addItem(label,mode)
        self.camera_combo.currentIndexChanged.connect(lambda _:self.camera(self.camera_combo.currentData()))
        replay.addWidget(self.camera_combo)
        self.berth_combo = QComboBox(); self.berth_combo.addItem('All berths',None)
        self.berth_combo.currentIndexChanged.connect(self.focus_berth)
        replay.addWidget(self.berth_combo)
        replay.addWidget(self.button('Reset camera', self.reset_camera))
        layout.addLayout(replay)
        visual_note = QLabel('Schematic geometry. Playback uses completed simulation events; animation speed does not change the analytical results. Displayed containers are capped for performance.')
        visual_note.setWordWrap(True); visual_note.setObjectName('muted'); layout.addWidget(visual_note)
        self.tabs.addTab(simulation, 'Simulation & replay')

        forecasts = QWidget(); layout = QVBoxLayout(forecasts)
        self.forecast_combo = QComboBox(); self.forecast_combo.currentIndexChanged.connect(self.choose_forecast)
        forecast_controls = QHBoxLayout();forecast_controls.addWidget(self.forecast_combo,1)
        self.forecast_mode = QComboBox();self.forecast_mode.addItems(['History & following-year estimate','Held-out predictions'])
        self.forecast_mode.currentIndexChanged.connect(self.set_forecast_mode);forecast_controls.addWidget(self.forecast_mode)
        layout.addLayout(forecast_controls)
        self.forecast_summary = QLabel(); self.forecast_summary.setWordWrap(True);self.forecast_summary.setObjectName('kpis');layout.addWidget(self.forecast_summary)
        self.plot = ForecastPlot(); layout.addWidget(self.plot,1)
        detail_tabs = QTabWidget(); detail_tabs.setMinimumHeight(150);detail_tabs.setMaximumHeight(210)
        self.backtest_table = table(['Held-out FY','Actual','Predicted','Error','Absolute error %','Method used','Prior selection folds'])
        detail_tabs.addTab(self.backtest_table,'Prediction versus actual')
        self.method_table = table(['Fixed benchmark','WAPE','MAE','RMSE','Bias','Following-year estimate'])
        detail_tabs.addTab(self.method_table,'Benchmark comparisons')
        self.forecast_table = table(['Port / unit','Annual values','Latest method','Following data FY','Holdouts','Policy WAPE','Baseline WAPE'])
        detail_tabs.addTab(self.forecast_table,'All annual series')
        layout.addWidget(detail_tabs)
        self.forecast_note = QLabel(); self.forecast_note.setWordWrap(True); layout.addWidget(self.forecast_note)
        self.tabs.addTab(forecasts, 'Forecasts')

        recommendation = QWidget(); layout = QVBoxLayout(recommendation)
        self.rec_status = QLabel('Run a baseline, then compare actions.'); self.rec_status.setWordWrap(True)
        layout.addWidget(self.rec_status)
        self.rec_table = table(['Candidate','Mean wait reduction h','Lower bound h','Served change','Backlog change','Decision'])
        layout.addWidget(self.rec_table, 1)
        self.rec_detail = QPlainTextEdit(); self.rec_detail.setReadOnly(True); layout.addWidget(self.rec_detail)
        self.tabs.addTab(recommendation, 'Recommendations')

        reports = QWidget(); layout = QVBoxLayout(reports)
        intro = QLabel('Export the run settings, source notes, vessel outcomes, interval metrics, events, forecasts, and scenario comparisons. The report preserves observed / unverified / synthetic labels.')
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout(); self.export_button = self.button('Export run report', self.export_results)
        row.addWidget(self.export_button); row.addWidget(self.button('Open last report', self.open_last_report)); row.addStretch()
        layout.addLayout(row); self.report_text = QPlainTextEdit(); self.report_text.setReadOnly(True)
        layout.addWidget(self.report_text, 1); self.tabs.addTab(reports, 'Reports')

        validation = QWidget(); layout = QVBoxLayout(validation)
        self.validation_summary = QLabel();self.validation_summary.setWordWrap(True);self.validation_summary.setObjectName('kpis');layout.addWidget(self.validation_summary)
        self.validation_table = table(['Component','Evidence / coverage','Result','Interpretation']);layout.addWidget(self.validation_table,1)
        self.validation_detail = QPlainTextEdit();self.validation_detail.setReadOnly(True);layout.addWidget(self.validation_detail,1)
        self.tabs.addTab(validation,'Model checks')

        bottom = QHBoxLayout(); self.progress_bar = QProgressBar(); self.progress_bar.setRange(0,1000)
        self.progress_bar.setMaximumWidth(260); self.progress_bar.setValue(0)
        bottom.addWidget(self.progress_bar); self.status = QLabel('Ready'); bottom.addWidget(self.status, 1)
        self.resources = QLabel(); bottom.addWidget(self.resources); outer.addLayout(bottom)
        self.setStyleSheet('''
            QMainWindow,QWidget { background:#f5f7fa; color:#172b40; font-family:"Segoe UI"; font-size:12px; }
            QLabel#brand { font-size:27px; font-weight:700; color:#124e65; }
            QLabel#muted { color:#5d6e80; }
            QLabel#banner { background:#fff2d7; border:1px solid #ecd49b; border-radius:5px; padding:9px; }
            QLabel#kpis { background:#e7f2f6; border-radius:5px; padding:10px; font-size:13px; }
            QPushButton { background:#fff; border:1px solid #b8c6d3; border-radius:5px; padding:8px 13px; }
            QPushButton:hover { background:#e7f2f6; border-color:#167d9a; }
            QPushButton#primary { background:#167d9a; color:white; border-color:#167d9a; font-weight:600; }
            QPushButton:disabled { color:#8a98a6; background:#ebeff3; }
            QTabWidget::pane { background:white; border:1px solid #d1dbe5; border-radius:4px; }
            QTabBar::tab { padding:10px 16px; background:#e8edf3; margin-right:3px; }
            QTabBar::tab:selected { background:white; color:#116e88; border-top:3px solid #167d9a; }
            QGroupBox { border:1px solid #d1dbe5; border-radius:5px; margin-top:14px; padding-top:12px; }
            QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 5px; font-weight:600; }
            QSpinBox,QDoubleSpinBox,QComboBox,QPlainTextEdit { background:white; border:1px solid #c2cfdc; border-radius:4px; padding:5px; }
            QTableWidget { background:white; alternate-background-color:#f1f6fa; gridline-color:#e4ebf1; border:1px solid #d1dbe5; }
            QHeaderView::section { background:#e7edf4; padding:8px; border:none; font-weight:600; }
            QProgressBar { background:#e3eaf0; border:none; border-radius:4px; text-align:center; }
            QProgressBar::chunk { background:#167d9a; border-radius:4px; }
        ''')
        self.replay_timer = QTimer(self); self.replay_timer.timeout.connect(self.replay_tick); self.replay_timer.start(33)
        self.resource_timer = QTimer(self); self.resource_timer.timeout.connect(self.update_resources); self.resource_timer.start(1000)
        self.load_demo()
        self.update_resources()

    @staticmethod
    def button(text, callback):
        b = QPushButton(text); b.clicked.connect(callback); return b

    def error(self, message):
        QMessageBox.warning(self, 'PortLab', str(message))

    def read_config(self):
        values = self.config.to_dict()
        values.update({key: field.value() for key, field in self.fields.items()})
        values['port'] = self.port_combo.currentText()
        values['notes'] = self.settings_notes.toPlainText()
        return TerminalConfig.from_dict(values)

    def validate_desktop_config(self, config):
        config.validate()
        for key, field in self.fields.items():
            if not field.minimum() <= getattr(config, key) <= field.maximum():
                raise ValueError(f'{key} must be within the desktop range {field.minimum():g}–{field.maximum():g}; settings were not loaded')

    def apply_config(self, config):
        self.validate_desktop_config(config)
        self._loading = True
        self.config = config
        if self.port_combo.findText(config.port) < 0: self.port_combo.addItem(config.port)
        self.port_combo.setCurrentText(config.port)
        for key, field in self.fields.items(): field.setValue(getattr(config,key))
        self.settings_notes.setPlainText(config.notes)
        self._loading = False
        if self.result is None: self.viewport.set_port(config.port)
        self.viewport.set_fps(config.view_fps)
        self.settings_changed()

    def restore_preset(self):
        port = self.port_combo.currentText()
        preset_name = {'guam': 'guam_settings.json', 'conley': 'conley_settings.json'}.get(port.casefold())
        config = (TerminalConfig.from_dict(json.loads((data_path() / preset_name).read_text(encoding='utf-8')))
                  if preset_name else TerminalConfig(port=port))
        self.apply_config(config)

    def change_port(self, port):
        if self._loading: return
        self.restore_preset(); self.refresh_data()

    def settings_changed(self, *args):
        if self._loading: return
        if self.result:
            self.stale = True
        self.update_banner(); self.update_enabled(); self.refresh_validation()

    def selected_calls(self):
        return [c for c in self.dataset.calls if c.port == self.port_combo.currentText()]

    def update_banner(self):
        calls = self.selected_calls()
        kinds = sorted(set(c.evidence_type for c in calls))
        kind_label = ', '.join(kinds) if kinds else 'no vessel schedule'
        self.banner.setText(f"Schedule: {kind_label} · {len(calls):,} selected calls. Parameter and scenario results are conditional. " +
                            ('Settings or data changed; run again to refresh results.' if self.stale else ''))

    def update_enabled(self):
        busy = self._thread is not None
        self.run_button.setEnabled(not busy and bool(self.selected_calls()))
        self.compare_button.setEnabled(not busy and self.result is not None and not self.stale
                                       and not self.result.checks.get('cancelled'))
        self.stop_button.setEnabled(busy)
        self.export_button.setEnabled(not busy and self.result is not None)
        for widget in (self.import_button, self.open_button, self.port_combo, self.save_button, self.demo_button): widget.setEnabled(not busy)
        self.tabs.widget(1).setEnabled(not busy)

    def load_demo(self):
        from .data import load_demo
        try:
            self.dataset = load_demo(data_path())
            self.clear_results()
            self.restore_preset(); self.refresh_data()
            self.status.setText('Bundled observations and illustrative operational schedule loaded')
        except Exception as exc: self.error(exc)

    def clear_results(self):
        self.result = None; self.recommendations = None; self.stale = False
        self.result_dataset = None; self.result_forecasts = []
        self._playing = False; self._clock_hour = 0; self.play_button.setText('Play')
        self.slider.setValue(0); self.clock_label.setText('0.00 h')
        self.viewport.clear_result()
        self.operations_plot.result = None;self.operations_plot.update()
        self.replay_status.setText('No replay loaded');fill_table(self.vessel_table,[])
        self.kpi_label.setText('Run a scenario to populate operational KPIs.')
        self.rec_status.setText('Run a scenario before comparing actions.')
        fill_table(self.rec_table, []); self.rec_detail.clear(); self.report_text.clear()
        self.refresh_validation()

    def refresh_data(self):
        from .data import forecast_series
        calls = self.selected_calls()
        self.data_summary.setText(f"{self.port_combo.currentText()}: {len(calls):,} vessel calls · {len(self.dataset.annual_series)} annual metric series across the project. Boxes and TEU remain separate. Importing a new vessel file replaces the current schedule.")
        fill_table(self.call_table, [[c.vessel_id,f'{c.arrival_hour:.2f}',c.import_boxes,c.export_boxes,c.length_m,c.draft_m,c.evidence_type] for c in calls[:300]])
        self.evidence.setPlainText('\n'.join(self.dataset.evidence_notes + ['Sources:'] + self.dataset.sources))
        try: self.forecasts = forecast_series(self.dataset.annual_series)
        except Exception as exc: self.forecasts = []; self.error(exc)
        self.forecast_combo.blockSignals(True); self.forecast_combo.clear()
        for rec in self.forecasts: self.forecast_combo.addItem(f"{rec['port']} · {rec.get('unit',rec['metric'])}")
        self.forecast_combo.blockSignals(False)
        fill_table(self.forecast_table, [[f"{r['port']} / {r.get('unit',r['metric'])}",r.get('years'),r.get('method'),r.get('forecast_year'),r.get('holdouts'),display_number(r.get('wape_percent'))+'%',display_number(r.get('baseline_wape_percent'))+'%'] for r in self.forecasts])
        self.choose_forecast(0)
        if self.result: self.stale = True
        self.update_banner(); self.update_enabled()
        self.refresh_validation()

    def choose_forecast(self, index):
        if not self.forecasts or index < 0 or index >= len(self.forecasts):
            self.plot.record = None; self.forecast_note.setText('No annual series loaded.')
            self.forecast_summary.setText('Annual forecasts require a contiguous history with enough observations.')
            fill_table(self.backtest_table,[]);fill_table(self.method_table,[])
        else:
            rec = self.forecasts[index]; self.plot.record = rec
            fold_count = rec.get('holdouts',0)
            selection = rec.get('selection_details',{})
            forecast = display_number(rec.get('forecast'),0)
            self.forecast_summary.setText(f"FY{rec.get('forecast_year','—')} estimate: {forecast} {rec.get('unit',rec['metric'])} · history ends FY{rec.get('history_end_year',rec.get('history', [{}])[-1].get('year','—'))}\nHeld-out WAPE {display_number(rec.get('wape_percent'))}% · last-year baseline {display_number(rec.get('baseline_wape_percent'))}% · {fold_count} test years · latest method {rec.get('method') or 'unavailable'}")
            note = str(rec.get('uncertainty_note',''))+'\n'+str(selection.get('reason',''))
            note += '\n'+'; '.join(rec.get('reasons',[]))
            note += '\nSmall annual samples support exploratory estimates. Retrospective checks are not independent validation of the revised policy.'
            self.forecast_note.setText(note)
            fill_table(self.backtest_table,[[r.get('year'),display_number(r.get('actual'),0),display_number(r.get('forecast'),0),display_number(r.get('error'),0),display_number(r.get('absolute_percentage_error'))+'%',r.get('method'),r.get('inner_holdouts')] for r in rec.get('backtests',[])])
            fill_table(self.method_table,[[r.get('method'),display_number(r.get('wape_percent'))+'%',display_number(r.get('mae'),0),display_number(r.get('rmse'),0),display_number(r.get('mean_error'),0),display_number(r.get('next_forecast'),0)] for r in rec.get('method_comparison',[])])
        self.plot.update()

    def set_forecast_mode(self,index):
        self.plot.mode = 'backtests' if index==1 else 'forecast';self.plot.update()

    def refresh_validation(self):
        selected = self.selected_calls()
        counts = {kind:sum(c.evidence_type==kind for c in selected) for kind in ('observed','synthetic','unverified')}
        rows = [['Vessel schedule',f"{counts['observed']} observed; {counts['synthetic']} synthetic; {counts['unverified']} unverified",'Observed declarations' if counts['observed'] else 'Scenario inputs','Uploaded provenance is declared by the file; parameters still require calibration.']]
        for record in self.forecasts:
            rows.append([f"Annual prediction: {record['port']}/{record['metric']}",f"{record.get('years',0)} annual values; {record.get('holdouts',0)} test years",f"WAPE {display_number(record.get('wape_percent'))}%",'Exploratory; compare against the last-year baseline.'])
        result = self.result
        if result:
            from .engine import REQUIRED_PHYSICAL_CHECKS
            failures=[name for name in REQUIRED_PHYSICAL_CHECKS if result.checks.get(name) is not True]
            rows.append(['Cargo and resources',f'{len(REQUIRED_PHYSICAL_CHECKS)} required checks','Failed: '+', '.join(failures) if failures else 'Pass','Numerical consistency within configured assumptions, rather than observed operational validity.'])
            diagnostic=result.kpis.get('operational_prediction_diagnostics',{})
            berth=diagnostic.get('berth_start',{});departure=diagnostic.get('departure',{})
            rows.append(['Operational prediction',f"Berth pairs {berth.get('paired_calls',0)}; departure pairs {departure.get('paired_calls',0)}",diagnostic.get('status','not_validated'),'Paired measured timings permit hindcast errors; independent validation needs separate records.'])
            self.validation_detail.setPlainText(json.dumps({'physical_checks':result.checks,'operational_prediction':diagnostic,'model_notes':result.evidence_notes},indent=2,default=str))
        else:
            rows.append(['Operational prediction','No completed run','Not validated','Import observed arrival, berth start and departure records with evidence references to compare timings.'])
            self.validation_detail.setPlainText('Physical model checks will appear after a run.\n\nAnnual prediction checks use only earlier years at each held-out target. The estimate year follows the last supplied observation and can already have ended in calendar time.\n\nSystem-dynamics stocks and flows are recorded from the discrete-event simulation. An empirically calibrated endogenous feedback model is not present.\n\nGeometry, handling policy, delivery lead times and costs require further real operating evidence.')
        fill_table(self.validation_table,rows)
        self.validation_summary.setText('Model check results'+(' · partial cancelled run' if result and result.checks.get('cancelled') else '')+(' · displayed run is stale; rerun after changing inputs' if self.stale else '')+'\nSoftware consistency and empirical prediction accuracy are different checks. Annual prediction evidence and measured timing coverage are shown separately.')

    def import_data(self):
        paths, _ = QFileDialog.getOpenFileNames(self,'Import vessel schedule or annual history',str(user_root()),'Datasets (*.csv *.xlsx)')
        if not paths: return
        try: self.import_paths(paths)
        except Exception as exc: self.error(exc)

    def import_paths(self, paths):
        from .data import load_datasets
        incoming = load_datasets(paths, default_port=self.port_combo.currentText())
        presets = []
        for path in map(Path, paths):
            sidecar = path.with_suffix('.settings.json')
            if sidecar.exists():
                preset = TerminalConfig.from_dict(json.loads(sidecar.read_text(encoding='utf-8')))
                self.validate_desktop_config(preset); presets.append(preset)
        # Commit only after both the datasets and automatic settings pass validation.
        updated = copy.deepcopy(self.dataset)
        if incoming.calls: updated.calls = incoming.calls
        updated.annual_series.update(incoming.annual_series)
        updated.evidence_notes = list(dict.fromkeys(updated.evidence_notes + incoming.evidence_notes))
        updated.sources = list(dict.fromkeys(updated.sources + incoming.sources))
        ports = sorted(set(c.port for c in incoming.calls))
        selected = ports[0] if len(ports) == 1 else self.port_combo.currentText()
        if self.port_combo.findText(selected) < 0: self.port_combo.addItem(selected)
        self.port_combo.setCurrentText(selected)
        for preset in presets:
            if preset.port == selected: self.apply_config(preset)
        self.dataset = updated
        self.refresh_data(); self.status.setText('Imported and checked '+str(len(paths))+' dataset(s)')

    def import_settings(self):
        path,_=QFileDialog.getOpenFileName(self,'Import settings preset',str(user_root()),'Settings (*.json)')
        if not path:return
        try:
            self.apply_config(TerminalConfig.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))); self.refresh_data()
        except Exception as exc:self.error(exc)

    def export_settings(self):
        path,_=QFileDialog.getSaveFileName(self,'Export settings preset',str(user_root()/'port.settings.json'),'Settings (*.json)')
        if not path:return
        try:Path(path).write_text(json.dumps(self.read_config().to_dict(),indent=2),encoding='utf-8')
        except Exception as exc:self.error(exc)

    def save_project(self):
        path,_=QFileDialog.getSaveFileName(self,'Save project',str(self._project_path or user_root()/'project.portlab.json'),'PortLab project (*.json)')
        if not path:return
        try:
            self.save_project_path(path)
        except Exception as exc:self.error(exc)

    def save_project_path(self, path):
        content={'schema_version':1,'config':self.read_config().to_dict(),'calls':[asdict(c) for c in self.dataset.calls],
            'annual_series':[{'port':p,'metric':m,'history':rows} for (p,m),rows in self.dataset.annual_series.items()],
            'evidence_notes':self.dataset.evidence_notes,'sources':self.dataset.sources}
        Path(path).write_text(json.dumps(content,indent=2),encoding='utf-8')
        self._project_path=Path(path);self.status.setText('Project saved with data and settings')

    def open_project(self):
        path,_=QFileDialog.getOpenFileName(self,'Open project',str(user_root()),'PortLab project (*.json)')
        if not path:return
        try:self.load_project_path(Path(path))
        except Exception as exc:self.error(exc)

    def load_project_path(self,path):
        from .data import dataset_from_project
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        bundle=dataset_from_project(value)
        config=TerminalConfig.from_dict(value['config'])
        self.validate_desktop_config(config)
        self.clear_results()
        self.apply_config(config)
        self.dataset=bundle;self._project_path=Path(path)
        self.refresh_data();self.status.setText('Project restored; rerun to create results')

    def start_work(self, operation, callback):
        if self._thread:return
        self._stop=threading.Event();self._thread=QThread();self._worker=Work(operation,self._stop)
        self._worker.moveToThread(self._thread);self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.work_progress)
        self._worker.finished.connect(callback);self._worker.finished.connect(self.finish_work)
        self._worker.failed.connect(self.work_failed)
        self._worker.finished.connect(self._worker.deleteLater);self._worker.failed.connect(self._worker.deleteLater)
        self._worker.finished.connect(self._thread.quit);self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self.cleanup_work)
        self.progress_bar.setValue(0);self.update_enabled();self._thread.start()

    def work_progress(self,fraction,message):
        self.progress_bar.setValue(round(max(0,min(1,fraction))*1000));self.status.setText(message)

    def finish_work(self, result):
        self.progress_bar.setValue(1000)

    def cleanup_work(self):
        self._thread.deleteLater();self._worker=None;self._thread=None;self.update_enabled()

    def work_failed(self,message):
        self.status.setText('Run failed — see details');self.error(message)

    def cancel_run(self):
        self._stop.set();self.status.setText('Cancelling safely at the next simulation event…')

    def run_simulation(self):
        from .engine import run_simulation
        try:config=self.read_config()
        except Exception as exc:self.error(exc);return
        calls=copy.deepcopy(self.selected_calls())
        if not calls:self.error('Import a vessel schedule for the selected terminal first.');return
        self._playing=False;self.play_button.setText('Play');self.config=config
        self._pending_dataset=copy.deepcopy(self.dataset);self._pending_forecasts=copy.deepcopy(self.forecasts)
        self.start_work(lambda progress,stop:run_simulation(calls,config,capture_events=True,progress=progress,cancel=stop),self.receive_result)
        self.tabs.setCurrentIndex(2)

    def receive_result(self,result):
        self.result=result;self.result_dataset=self._pending_dataset;self.result_forecasts=self._pending_forecasts
        self.recommendations=None;self.stale=False
        self.show_result(result);self.update_banner()

    def show_result(self,result):
        k=result.kpis
        number=lambda key:f"{k.get(key,0):,.2f}" if isinstance(k.get(key), (int,float)) else '—'
        censored_count=sum(bool(row.get('wait_censored')) for row in result.vessels)
        self.kpi_label.setText(f"{result.config.port} run totals · Served {k.get('served_calls',0)} / {k.get('requested_calls',0)} calls    |    Mean simulated wait {number('mean_wait_hours')} h    |    Throughput {k.get('throughput_boxes',0):,} boxes\nBacklog {k.get('end_backlog_calls',0)} calls · {censored_count} unfinished waits truncated at cutoff    |    Berthed-call wait mean {number('completed_mean_wait_hours')} h    |    {k.get('gate_status','')}    |    Compute {number('wall_seconds')} s")
        self.rec_status.setText(f"Baseline gate: {k.get('gate_status','unknown')}. First eligible hour: {k.get('first_eligible_hour',k.get('eligible_hour'))}. Comparisons are conditional full-horizon counterfactuals; no automatic investment purchase.")
        fill_table(self.rec_table,[]);self.rec_detail.clear()
        self.viewport.load_result(result);self.viewport.set_fps(result.config.view_fps)
        self.camera(self.camera_combo.currentData())
        self.berth_combo.blockSignals(True);self.berth_combo.clear();self.berth_combo.addItem('All berths',None)
        for berth in range(result.config.berths):self.berth_combo.addItem(f'Berth {berth+1}',berth)
        self.berth_combo.blockSignals(False)
        self.operations_plot.result=result;self.operations_plot.hour=0;self.operations_plot.update()
        self.vessel_table.blockSignals(True)
        fill_table(self.vessel_table,[[r.get('vessel_id'),r.get('status'),display_number(r.get('arrival_hour')),display_number(r.get('berth_start_hour')),display_number(r.get('departure_hour')),display_number(r.get('wait_hours')),display_number(r.get('berth_start_error_hours')),display_number(r.get('departure_error_hours'))] for r in result.vessels])
        self.vessel_table.blockSignals(False)
        diagnostic=k.get('operational_prediction_diagnostics',{})
        self.operating_note.setText(f"Timings are simulated from this run. Reported wait includes unfinished waits at the cutoff. Select a berthed call to focus its berth. Operational timing validation: {diagnostic.get('status','not_validated')}. Prediction error = simulated minus supplied observation.")
        self._clock_hour=0;self.slider.setValue(0);self.viewport.set_simulation_time(0)
        self.clock_label.setText('0.00 h')
        self.report_text.setPlainText('Run checks:\n'+json.dumps(result.checks,indent=2,default=str)+'\n\nEvidence:\n'+'\n'.join(result.evidence_notes))
        self.status.setText('Simulation complete' if not result.checks.get('cancelled') else 'Cancelled run; partial outcomes retained')
        self.update_enabled()
        self.refresh_replay_status();self.refresh_validation()

    def compare_actions(self):
        from .engine import compare_candidates
        if not self.result or self.stale or self.result.checks.get('cancelled'):return
        calls=copy.deepcopy([c for c in self.result_dataset.calls if c.port==self.result.config.port])
        config=copy.deepcopy(self.result.config)
        self.start_work(lambda progress,stop:compare_candidates(calls,config,progress=progress,cancel=stop),self.receive_recommendations)
        self.tabs.setCurrentIndex(4)

    def receive_recommendations(self, recommendation):
        self.recommendations=recommendation
        self.rec_status.setText(str(recommendation.get('status',''))+' · '+str(recommendation.get('reason','')))
        rows=[]
        for r in recommendation.get('candidates',[]):
            fmt=lambda key:('—' if r.get(key) is None else f"{r[key]:.3f}")
            rows.append([r.get('name'),fmt('mean_wait_reduction_hours'),fmt('lower_bound_hours'),r.get('served_delta'),r.get('backlog_delta'),'SUPPORTED IN SCENARIO' if r.get('accepted') else 'NOT SUPPORTED'])
        fill_table(self.rec_table,rows);self.rec_detail.setPlainText(json.dumps(recommendation,indent=2,default=str));self.status.setText('Scenario comparison complete')

    def toggle_play(self):
        if not self.result:return
        self._playing=not self._playing;self._last_tick=time.perf_counter();self.play_button.setText('Pause' if self._playing else 'Play')

    def restart_replay(self):
        self.slider.setValue(0);self.seek_replay(0)

    def replay_tick(self):
        now=time.perf_counter();dt=min(now-self._last_tick,.2);self._last_tick=now
        if self.result and self._playing:
            horizon=float(self.result.kpis.get('simulated_hours',self.result.config.horizon_hours))
            self._clock_hour=min(horizon,self._clock_hour+dt*float(self.speed.currentData()))
            self.slider.blockSignals(True);self.slider.setValue(round(self._clock_hour/max(horizon,.001)*10000));self.slider.blockSignals(False)
            self.viewport.set_simulation_time(self._clock_hour);self.clock_label.setText(f'{self._clock_hour:.2f} h')
            self.operations_plot.hour=self._clock_hour;self.operations_plot.update();self.refresh_replay_status()
            if self._clock_hour>=horizon:self._playing=False;self.play_button.setText('Play')

    def seek_replay(self,value):
        if not self.result:return
        horizon=float(self.result.kpis.get('simulated_hours',self.result.config.horizon_hours))
        self._clock_hour=value/10000*horizon;self.viewport.set_simulation_time(self._clock_hour);self.clock_label.setText(f'{self._clock_hour:.2f} h')
        self.operations_plot.hour=self._clock_hour;self.operations_plot.update();self.refresh_replay_status()

    def refresh_replay_status(self):
        if not self.result or not hasattr(self.viewport,'replay_status'):return
        values=self.viewport.replay_status()
        yard='unavailable outside captured trace' if values.get('yard_boxes') is None else f"{values['yard_boxes']:,} boxes"
        self.replay_status.setText(f"At {values.get('hour',self._clock_hour):.2f} h · waiting {values.get('waiting_calls',0)} · working {values.get('working_calls',0)} · yard {yard} · active cranes {values.get('busy_cranes',0)} · tractors {values.get('busy_tractors',0)} · trace {values.get('trace_state','unknown')}")

    def focus_berth(self,index):
        if hasattr(self.viewport,'focus_berth'):self.viewport.focus_berth(self.berth_combo.itemData(index))
        self.refresh_replay_status()

    def focus_selected_vessel(self):
        if not self.result:return
        row=self.vessel_table.currentRow()
        if row<0 or row>=len(self.result.vessels):return
        berth=self.result.vessels[row].get('berth')
        if berth is None:return
        index=self.berth_combo.findData(berth)
        if index>=0:self.berth_combo.setCurrentIndex(index)
        self.simulation_views.setCurrentIndex(0)

    def camera(self,mode):
        if hasattr(self.viewport,'set_camera_mode'):self.viewport.set_camera_mode(mode)

    def reset_camera(self):
        if hasattr(self.viewport,'reset_camera'):self.viewport.reset_camera()

    def update_resources(self):
        rss=self._process.memory_info().rss/1024**2;self._peak_memory=max(self._peak_memory,rss)
        self.resources.setText(f'RAM {rss:.0f} MB · peak {self._peak_memory:.0f} MB · free {psutil.virtual_memory().available/1024**3:.1f} GB')

    def export_results(self):
        if not self.result:return
        path=QFileDialog.getExistingDirectory(self,'Choose report folder',str(user_root()))
        if not path:return
        try:self.export_to(Path(path))
        except Exception as exc:self.error(exc)

    def export_to(self,path):
        from .data import export_report
        path=Path(path)/time.strftime('run_%Y%m%d_%H%M%S');path.mkdir(parents=True,exist_ok=True)
        self._last_reports=export_report(path,self.result,self.result_forecasts,self.recommendations,self.result_dataset or self.dataset)
        from . import __version__
        (path/'desktop_diagnostics.json').write_text(json.dumps({'observed_peak_process_mb':self._peak_memory,'visual':self.viewport.diagnostics(),'app_version':__version__},indent=2,default=str),encoding='utf-8')
        self.report_text.setPlainText('Exported:\n'+'\n'.join(map(str,self._last_reports))+'\n\n'+json.dumps(self.result.checks,indent=2,default=str))
        self.status.setText('Reports saved to '+str(path));return path

    def open_last_report(self):
        if not self._last_reports:return
        path=next((p for p in self._last_reports if str(p).endswith('.html')),self._last_reports[0])
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_templates(self):
        from .paths import resource_root
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_root()/'templates')))

    def closeEvent(self,event):
        if self._thread and self._thread.isRunning():
            self._stop.set();event.ignore();self.status.setText('Cancelling run before closing…')
            QTimer.singleShot(300,self.close);return
        self.replay_timer.stop();self.resource_timer.stop();self.viewport.shutdown();event.accept()
