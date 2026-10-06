"""Independent analytical regressions, separate from implementation unit tests."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from portlab.contracts import SimulationResult, TerminalConfig, VesselCall
from portlab.engine import REQUIRED_PHYSICAL_CHECKS, compare_candidates, run_simulation


CHECKS = REQUIRED_PHYSICAL_CHECKS


class AnalyticalCrossChecks(unittest.TestCase):
    def assert_accounting(self, result):
        for name in CHECKS:
            self.assertTrue(result.checks[name], f'{name} at {result.kpis["simulated_hours"]}h')
        self.assertTrue(all(value >= 0 for value in result.checks['inventory'].values()))
        inventory = result.checks['inventory']
        self.assertEqual(inventory['import_on_vessel'] + inventory['import_apron'] +
                         inventory['import_on_tractor'] + inventory['import_yard'] + inventory['import_released'],
                         result.checks['requested_import_boxes'])
        self.assertEqual(inventory['export_not_staged'] + inventory['export_yard'] +
                         inventory['export_on_tractor'] + inventory['export_apron'] + inventory['export_loaded'],
                         result.checks['requested_export_boxes'])
        for resource in ('berth', 'crane', 'tractor'):
            for identifier in range(getattr(result.config, resource + 's')):
                intervals = sorted((interval for interval in result.intervals
                                    if interval['resource'] == resource and interval['resource_id'] == identifier),
                                   key=lambda interval: interval['start_time'])
                for interval in intervals:
                    self.assertGreaterEqual(interval['start_time'], 0)
                    self.assertGreaterEqual(interval['end_time'], interval['start_time'])
                    self.assertLessEqual(interval['end_time'], result.kpis['simulated_hours'])
                for first, second in zip(intervals, intervals[1:]):
                    self.assertLessEqual(first['end_time'], second['start_time'] + 1e-9)

    def test_multi_cutoff_conservation_all_motion_phases(self):
        calls = [VesselCall('A', 0, 17, 13), VesselCall('B', .3, 9, 11),
                 VesselCall('C', .75, 20, 7), VesselCall('TOO_BIG', .1, 6, 5, length_m=450)]
        base = TerminalConfig(berths=2, cranes=2, cranes_per_vessel=2, tractors=2,
                              yard_capacity_boxes=15, initial_yard_boxes=4,
                              import_dwell_hours=.4, export_lead_hours=.2,
                              crane_moves_per_hour=12, tractor_cycle_minutes=4,
                              service_variability=.35)
        for cutoff in (.02, .1, .3, .5, .75, 1, 2, 5, 12, 50):
            with self.subTest(cutoff=cutoff):
                result = run_simulation(calls, replace(base, horizon_hours=cutoff))
                self.assert_accounting(result)
                expected = [call for call in calls if call.arrival_hour <= cutoff]
                self.assertEqual(result.kpis['requested_calls'], len(expected))
                self.assertEqual(result.kpis['requested_calls'], result.kpis['served_calls'] + result.kpis['unserved_calls'])
                self.assertEqual(result.kpis['unserved_calls'], result.kpis['incompatible_calls'] + result.kpis['end_backlog_calls'])
                for row in result.vessels:
                    if row['berth_start_hour'] is None and row['status'] == 'waiting':
                        self.assertEqual(row['wait_hours'], cutoff - row['arrival_hour'])
                        self.assertTrue(row['wait_censored'])
                    if row['status'] == 'served':
                        self.assertFalse(row['turnaround_censored'])

    def test_large_aggregate_counts_exact_at_partial_cutoffs(self):
        calls = [VesselCall('BIG', 0, 500001, 150003), VesselCall('LATER', 1, 20011, 7031)]
        base = TerminalConfig(berths=2, cranes=4, cranes_per_vessel=2, tractors=6,
                              yard_capacity_boxes=10000, initial_yard_boxes=100,
                              import_dwell_hours=8, export_lead_hours=.5,
                              service_variability=.25)
        for cutoff in (.01, 1, 4, 40):
            with self.subTest(cutoff=cutoff):
                result = run_simulation(calls, replace(base, horizon_hours=cutoff), capture_events=False)
                self.assertGreater(result.checks['aggregation_batch_boxes'], 1)
                self.assert_accounting(result)
                self.assertEqual(result.events, [])

    def test_per_vessel_crane_limit_and_shared_fleet(self):
        calls = [VesselCall('A', 0, 73, 47), VesselCall('B', 0, 62, 53)]
        for cranes_per_vessel in (1, 2, 3):
            config = TerminalConfig(horizon_hours=20, berths=2, cranes=3,
                                    cranes_per_vessel=cranes_per_vessel, tractors=8,
                                    yard_capacity_boxes=1000, import_dwell_hours=1,
                                    service_variability=0)
            result = run_simulation(calls, config)
            self.assert_accounting(result)
            crane_intervals = [interval for interval in result.intervals if interval['resource'] == 'crane']
            changes = []
            for interval in crane_intervals:
                changes.append((interval['start_time'], 1, interval['vessel_id']))
                changes.append((interval['end_time'], -1, interval['vessel_id']))
            active = {'A': 0, 'B': 0}
            for _, delta, vessel_id in sorted(changes, key=lambda change: (change[0], change[1])):
                active[vessel_id] += delta
                self.assertLessEqual(active[vessel_id], cranes_per_vessel)
                self.assertLessEqual(sum(active.values()), config.cranes)

    def test_extending_horizon_preserves_earlier_trajectory(self):
        calls = [VesselCall('EARLY', 0, 100, 0), VesselCall('FUTURE', 100, 100000, 0)]
        config = TerminalConfig(horizon_hours=20, cranes=1, cranes_per_vessel=1,
                                berths=1, tractors=1, yard_capacity_boxes=2000,
                                import_dwell_hours=1, service_variability=0)
        short = run_simulation(calls, config)
        long = run_simulation(calls, replace(config, horizon_hours=200))
        early_short = next(row for row in short.vessels if row['vessel_id'] == 'EARLY')
        early_long = next(row for row in long.vessels if row['vessel_id'] == 'EARLY')
        self.assertEqual(short.checks['aggregation_batch_boxes'], long.checks['aggregation_batch_boxes'])
        self.assertEqual(early_short, early_long)
        prefix_short = [event for event in short.events if event.get('vessel_id') == 'EARLY']
        prefix_long = [event for event in long.events if event.get('vessel_id') == 'EARLY' and event['time'] <= 20]
        self.assertEqual(prefix_short, prefix_long)

    def fake_result(self, config, bad_candidate=False, bad_baseline=False):
        checks = {name: True for name in CHECKS}
        checks.update(cancelled=False, inventory_nonnegative=True)
        if bad_candidate or bad_baseline:
            checks['export_conserved'] = False
        kpis = {'gate_status': 'eligible', 'mean_wait_hours': 5 if bad_candidate else 10,
                'served_calls': 3, 'end_backlog_calls': 2, 'unserved_calls': 2,
                'incompatible_calls': 0, 'service_backlog_boxes': 0}
        return SimulationResult(config, [], [], [], kpis, checks)

    def test_failed_candidate_accounting_forbids_acceptance(self):
        config = TerminalConfig(replications=3)
        def fake(calls, candidate_config, *args, **kwargs):
            return self.fake_result(candidate_config,
                                    bad_candidate=candidate_config.tractor_cycle_minutes < config.tractor_cycle_minutes)
        with patch('portlab.engine.run_simulation', side_effect=fake):
            result = compare_candidates([], config)
        broken = next(candidate for candidate in result['candidates'] if candidate['name'] == 'Faster tractor dispatch')
        self.assertFalse(broken['accepted'])
        self.assertTrue(broken.get('physical_check_failed_replications'))

    def test_failed_baseline_accounting_forbids_comparison(self):
        with patch('portlab.engine.run_simulation', side_effect=lambda calls, config, *args, **kwargs: self.fake_result(config, bad_baseline=True)):
            result = compare_candidates([], TerminalConfig(replications=3))
        self.assertEqual(result['status'], 'invalid_baseline')
        self.assertEqual(result['candidates'], [])


class PresetValidationCrossChecks(unittest.TestCase):
    def test_fractional_and_boolean_count_presets_rejected(self):
        for changes in ({'initial_yard_boxes': .5}, {'pressure_queue_calls': 1.5},
                        {'cranes': True}, {'seed': .5}, {'view_fps': 0},
                        {'max_visual_containers': -1}, {'port': ' '}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    TerminalConfig.from_dict(changes)


if __name__ == '__main__':
    unittest.main()
