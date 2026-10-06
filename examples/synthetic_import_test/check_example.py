"""Reproduce the curated fictional operating example without private files."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from portlab import __version__
from portlab.contracts import TerminalConfig
from portlab.data import load_datasets
from portlab.engine import REQUIRED_PHYSICAL_CHECKS, compare_candidates, run_simulation


def main():
    csv_path = HERE / 'guam_synthetic_test.csv'
    xlsx_path = HERE / 'guam_synthetic_test.xlsx'
    settings_path = HERE / 'guam_synthetic_test.settings.json'
    csv_data = load_datasets([csv_path])
    xlsx_data = load_datasets([xlsx_path])
    if [asdict(call) for call in csv_data.calls] != [asdict(call) for call in xlsx_data.calls]:
        raise ValueError('CSV and XLSX call records differ.')
    if len(csv_data.calls) != 32 or not all(call.evidence_type == 'synthetic' for call in csv_data.calls):
        raise ValueError('Expected 32 explicitly synthetic calls.')
    if csv_data.annual_series or xlsx_data.annual_series:
        raise ValueError('This operating example must not contain annual forecasting targets.')
    config = TerminalConfig.from_dict(json.loads(settings_path.read_text(encoding='utf-8')))
    if config.port != 'Guam' or not all(call.port == config.port for call in csv_data.calls):
        raise ValueError('Schedule and settings ports must match.')
    result = run_simulation(csv_data.calls, config, capture_events=False)
    failed = [name for name in REQUIRED_PHYSICAL_CHECKS if result.checks.get(name) is not True]
    if failed:
        raise ValueError('Required physical checks failed: ' + ', '.join(failed))
    timing = result.kpis['operational_prediction_diagnostics']
    if timing['status'] != 'not_validated':
        raise ValueError('Synthetic calls cannot validate observed operating predictions.')
    comparison = compare_candidates(csv_data.calls, config)
    report = {'app_version': __version__, 'review_date': '2026-10-04',
              'verified_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'csv_and_xlsx_equivalent': True, 'matching_settings_valid': True,
              'all_calls_synthetic': True, 'annual_forecasting_targets_used': 0,
              'calls': len(csv_data.calls), 'horizon_hours': config.horizon_hours,
              'last_arrival_hour': max(call.arrival_hour for call in csv_data.calls),
              'import_boxes': sum(call.import_boxes for call in csv_data.calls),
              'export_boxes': sum(call.export_boxes for call in csv_data.calls), 'seed': config.seed,
              'served_calls': result.kpis['served_calls'], 'end_backlog_calls': result.kpis['end_backlog_calls'],
              'service_backlog_boxes': result.kpis['service_backlog_boxes'],
              'restricted_mean_wait_hours': result.kpis['restricted_mean_wait_hours'],
              'completed_mean_wait_hours': result.kpis['completed_mean_wait_hours'],
              'wait_population_calls': result.kpis['wait_population_calls'],
              'completed_wait_calls': result.kpis['completed_wait_calls'],
              'gate_status': result.kpis['gate_status'], 'first_eligible_hour': result.kpis['first_eligible_hour'],
              'physical_checks_passed': True, 'required_physical_checks': list(REQUIRED_PHYSICAL_CHECKS),
              'operational_prediction_status': timing['status'], 'comparison_status': comparison['status'],
              'comparison_candidates': len(comparison['candidates']),
              'preferred_conditional_candidate': comparison.get('preferred_candidate'),
              'candidate_summary': [{key: row[key] for key in ('name', 'category', 'changes',
                  'mean_wait_reduction_hours', 'lower_bound_hours', 'served_delta', 'backlog_delta',
                  'service_backlog_boxes_delta', 'accepted', 'reason')} for row in comparison['candidates']],
              'input_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in (csv_path, xlsx_path, settings_path)},
              'model_source_sha256': {str(path.relative_to(ROOT)).replace('\\', '/'):
                                      hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in (ROOT / 'portlab' / 'contracts.py',
                                                   ROOT / 'portlab' / 'data.py', ROOT / 'portlab' / 'engine.py')},
              'reproduction_scope': 'Dataset loaders, matching settings contract, engine baseline and paired comparisons. Qt automatic settings behavior is checked separately by tools/desktop_check.py and tools/phase2_check.py.',
              'disclaimer': 'Fixed-seed fictional operating scenario, not actual port measurements, independent prediction validation, causal investment benefit or annual forecasting evidence.'}
    (HERE / 'IMPORT_CHECK_RESULTS.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
