import json
import unittest
from pathlib import Path

from portlab.contracts import SimulationResult, TerminalConfig, VesselCall
from portlab.visual import EventReplay, PortViewport


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.result = SimulationResult(
            config=TerminalConfig(initial_yard_boxes=2), vessels=[], kpis={}, checks={},
            intervals=[{'resource': 'crane', 'resource_id': 0, 'start_time': 1, 'end_time': 3,
                        'box_id': 'batch_a', 'count': 4},
                       {'resource': 'crane', 'resource_id': 0, 'start_time': 3, 'end_time': 5,
                        'box_id': 'batch_b', 'count': 2}],
            events=[{'time': 1, 'type': 'yard_in', 'count': 4},
                    {'time': 2, 'type': 'yard_out', 'count': 3},
                    {'time': 2, 'type': 'yard_in', 'count': 1},
                    {'time': 3, 'type': 'berth_start', 'vessel_id': 'SHIP-A', 'berth': 1}])
        self.replay = EventReplay(self.result)

    def test_yard_uses_counted_events_and_seek_can_move_backward(self):
        self.assertEqual(self.replay.yard_count(2), 4)
        self.assertEqual(self.replay.yard_count(0), 2)
        self.assertEqual(self.replay.yard_count(1.5), 6)
        self.assertEqual(self.replay.yard_count(100), 4)

    def test_resource_switches_at_exact_endpoint_without_double_booking(self):
        self.assertIsNone(self.replay.active('crane', 0, .9))
        self.assertEqual(self.replay.active('crane', 0, 1)['box_id'], 'batch_a')
        self.assertEqual(self.replay.active('crane', 0, 3)['box_id'], 'batch_b')
        self.assertIsNone(self.replay.active('crane', 0, 5))
        self.assertIsNone(self.replay.active('crane', 1, 3))

    def test_berth_mapping_and_latest_event(self):
        self.assertEqual(self.replay.berths['SHIP-A'], 1)
        self.assertIsNone(self.replay.latest_event(.5))
        self.assertEqual(self.replay.latest_event(3)['type'], 'berth_start')

    def test_partial_trace_does_not_present_stale_yard_as_current(self):
        self.result.checks.update(trace_captured=True, trace_complete=False)
        replay = EventReplay(self.result)
        self.assertEqual(replay.yard_count(2), 4)
        self.assertIsNone(replay.yard_count(3))
        self.assertIsNone(replay.yard_count(10))
        self.assertIsNone(replay.latest_event(10))
        self.assertEqual(replay.status(4)['trace_state'], 'partial')
        self.assertEqual(replay.status(4)['busy_cranes'], 1)

    def test_uncaptured_trace_never_invents_yard_total(self):
        self.result.checks.update(trace_captured=False, trace_complete=False)
        self.result.events = []
        replay = EventReplay(self.result)
        self.assertIsNone(replay.yard_count(0))
        self.assertIsNone(replay.yard_count(2))
        self.assertEqual(replay.status(2)['trace_state'], 'uncaptured')
        self.assertIsNotNone(replay.active('crane', 0, 2))

    def test_cancelled_replay_preserves_nominal_duration_and_cutoff(self):
        self.result.kpis['simulated_hours'] = 2
        self.result.intervals = [{'resource': 'tractor', 'resource_id': 18,
                                  'start_time': 1, 'end_time': 2, 'planned_end_time': 5,
                                  'censored': True, 'flow': 'import', 'count': 3}]
        replay = EventReplay(self.result)
        self.assertAlmostEqual(replay.progress(replay.active('tractor', 18, 2), 2), .25)
        self.assertIsNone(replay.active('tractor', 18, 2.1))
        self.assertEqual(replay.status(100)['hour'], 2)
        # Live counters include resources beyond the bounded visual pool.
        self.assertEqual(replay.status(2)['busy_tractors'], 1)

    def test_vessel_status_has_no_future_calls_and_respects_departure(self):
        self.result.vessels = [
            {'vessel_id': 'served', 'arrival_hour': 0, 'berth_start_hour': .5, 'departure_hour': 1},
            {'vessel_id': 'working', 'arrival_hour': 0, 'berth_start_hour': 1, 'departure_hour': None},
            {'vessel_id': 'waiting', 'arrival_hour': 1, 'berth_start_hour': None, 'departure_hour': None},
            {'vessel_id': 'future', 'arrival_hour': 5, 'berth_start_hour': None, 'departure_hour': None},
            {'vessel_id': 'rejected', 'arrival_hour': 0, 'status': 'incompatible'}]
        state = EventReplay(self.result).status(2)
        self.assertEqual((state['waiting_calls'], state['working_calls'], state['served_calls']), (1, 1, 1))

    def test_route_has_constant_path_speed_and_correct_endpoints(self):
        route = [(0, 0), (0, 10), (30, 10)]
        self.assertEqual(PortViewport.route_position(route, 0)[:2], (0, 0))
        self.assertEqual(PortViewport.route_position(route, .5)[:2], (10, 10))
        self.assertEqual(PortViewport.route_position(route, 1)[:2], (30, 10))

    def test_future_vessel_export_staging_is_in_yard_without_future_vessel(self):
        from portlab.engine import run_simulation
        config = TerminalConfig(horizon_hours=10, export_lead_hours=5,
                                initial_yard_boxes=3, import_dwell_hours=100)
        result = run_simulation([VesselCall('FUTURE', 12, 0, 8)], config)
        replay = EventReplay(result)
        self.assertEqual(replay.yard_count(6), 3)
        self.assertEqual(replay.yard_count(10), 11)
        self.assertEqual(replay.status(10)['waiting_calls'], 0)
        self.assertEqual(replay.status(10)['working_calls'], 0)
        self.assertTrue(result.checks['future_export_conserved'])

    def test_real_cancellation_freezes_in_progress_equipment_at_actual_cutoff(self):
        from portlab.engine import run_simulation
        config = TerminalConfig(horizon_hours=100, tractor_cycle_minutes=600)
        stop = [False]
        def progress(fraction, message):
            stop[0] = fraction >= .02
        result = run_simulation([VesselCall('STOPPED', 0, 400, 0)], config,
                                progress=progress, cancel=lambda: stop[0])
        replay = EventReplay(result)
        self.assertTrue(result.checks['cancelled'])
        self.assertLess(replay.cutoff_hour, config.horizon_hours)
        state = replay.status(config.horizon_hours)
        self.assertEqual(state['hour'], replay.cutoff_hour)
        self.assertGreater(state['busy_tractors'], 0)
        for batch in state['active_batches']:
            self.assertGreaterEqual(batch['progress'], 0)
            self.assertLess(batch['progress'], 1)
        self.assertEqual(replay.yard_count(replay.cutoff_hour), result.checks['inventory']['import_yard'])

    def test_real_assets_retain_dimensions_and_compile_to_readable_geometry(self):
        from panda3d.core import NodePath
        assets = Path(__file__).resolve().parents[1] / 'assets'
        manifest = json.loads((assets / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['models']), 16)
        for model in manifest['models']:
            with self.subTest(asset=model['asset']):
                node = NodePath.decodeFromBamStream((assets / 'models' / (model['asset'] + '.bam')).read_bytes())
                self.assertFalse(node.isEmpty())
                lower, upper = node.getTightBounds()
                for actual, expected in zip((*lower, *upper), (*model['min_m'], *model['max_m'])):
                    self.assertAlmostEqual(actual, expected, places=3)
                self.assertGreater(model['triangles'], 0)


if __name__ == '__main__':
    unittest.main()
