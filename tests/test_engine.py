from dataclasses import replace
import math
import unittest
from unittest.mock import patch

from portlab.contracts import SimulationResult, TerminalConfig, VesselCall
from portlab.engine import (REQUIRED_PHYSICAL_CHECKS, QueuePressureGate, _factor, _confidence_details, _lower_bound,
                            compare_candidates, run_simulation)


class PressureGateTests(unittest.TestCase):
    def test_pressure_before_observation_matures_at_168(self):
        gate = QueuePressureGate()
        gate.update(120, 1)
        self.assertFalse(gate.eligible(167.999))
        self.assertTrue(gate.eligible(168))
        gate.advance(168)
        self.assertEqual(gate.first_eligible_hour, 168)

    def test_late_pressure_matures_at_184(self):
        gate = QueuePressureGate()
        gate.update(160, 1)
        self.assertFalse(gate.eligible(183.999))
        self.assertTrue(gate.eligible(184))

    def test_false_signal_resets_without_averaging(self):
        gate = QueuePressureGate()
        gate.update(120, 1)
        gate.update(143.9, 0)
        gate.update(165, 1)
        self.assertFalse(gate.eligible(188.999))
        self.assertTrue(gate.eligible(189))
        gate.update(190, 0)
        self.assertEqual(gate.status(200), "pressure_cleared")
        self.assertFalse(gate.eligible(200))
        self.assertEqual(gate.first_eligible_hour, 189)
        self.assertIsNone(gate.episodes[0]["eligible_hour"])


class EngineTests(unittest.TestCase):
    def config(self, **changes):
        return replace(TerminalConfig(), horizon_hours=60, berths=1, cranes=1,
                       tractors=1, crane_moves_per_hour=10, tractor_cycle_minutes=6,
                       yard_capacity_boxes=10, import_dwell_hours=1,
                       export_lead_hours=1, service_variability=0, **changes)

    def assert_conserved(self, result):
        for name in ("import_conserved", "export_conserved", "initial_inventory_conserved",
                     "yard_inventory_consistent", "yard_capacity_respected", "yard_slot_balance",
                     "apron_slot_balance", "apron_capacity_respected",
                     "resource_bounds_respected", "inventory_nonnegative"):
            self.assertTrue(result.checks[name], name)

    def test_export_import_finite_yard_and_all_counts_conserved(self):
        calls = [VesselCall("A", 0, 35, 24), VesselCall("B", 1, 12, 18)]
        result = run_simulation(calls, self.config())
        self.assertEqual(result.kpis["served_calls"], 2)
        self.assertEqual(result.kpis["throughput_boxes"], 89)
        self.assertEqual(result.kpis["ending_yard_boxes"], 0)
        self.assert_conserved(result)
        starts = [event for event in result.events if event["type"] == "tractor_start"]
        self.assertEqual(sum(event["count"] for event in starts), 89)
        self.assertTrue(all(event["end_time"] > event["start_time"] for event in starts))
        self.assertTrue(all(event["tractor_id"] == 0 for event in starts))

    def test_full_initial_yard_releases_and_exports_do_not_disappear(self):
        cfg = self.config(initial_yard_boxes=10)
        result = run_simulation([VesselCall("A", 0, 5, 20)], cfg)
        self.assertEqual(result.kpis["served_calls"], 1)
        self.assertEqual(result.checks["inventory"]["export_loaded"], 20)
        self.assertEqual(result.checks["inventory"]["initial_released"], 10)
        self.assert_conserved(result)

    def test_one_box_yard_multiple_calls_cannot_be_blocked_by_future_exports(self):
        cfg = replace(self.config(), yard_capacity_boxes=1)
        calls = [VesselCall("A", 0, 3, 4), VesselCall("B", 0, 3, 4)]
        result = run_simulation(calls, cfg)
        self.assertEqual(result.kpis["served_calls"], 2)
        self.assert_conserved(result)

    def test_import_preferred_interleaving_starts_discharge_when_space_available(self):
        result = run_simulation([VesselCall("A", 0, 5, 5)], self.config())
        starts = [event for event in result.events if event["type"] == "crane_start"]
        self.assertEqual(starts[0]["flow"], "import")
        self.assertTrue(any(event["flow"] == "export" for event in starts))
        self.assertEqual(result.kpis["served_calls"], 1)
        self.assert_conserved(result)

    def test_interleaving_multiple_berths_initial_full_yard_releases_progress(self):
        cfg = replace(self.config(), berths=2, cranes=3, cranes_per_vessel=2,
                      tractors=2, yard_capacity_boxes=4, initial_yard_boxes=4)
        calls = [VesselCall("A", 0, 21, 19), VesselCall("B", 0, 17, 23),
                 VesselCall("C", .1, 15, 25)]
        result = run_simulation(calls, cfg)
        self.assertEqual(result.kpis["served_calls"], 3)
        self.assertEqual(result.kpis["service_backlog_boxes"], 0)
        self.assert_conserved(result)

    def test_restricted_wait_reports_censored_population_without_completed_bias(self):
        cfg = replace(self.config(), horizon_hours=2, yard_capacity_boxes=200)
        calls = [VesselCall("A", 0, 100, 0), VesselCall("B", 0, 0, 0),
                 VesselCall("C", 1, 0, 0)]
        result = run_simulation(calls, cfg)
        self.assertEqual(result.kpis["wait_population_calls"], 3)
        self.assertEqual(result.kpis["completed_wait_calls"], 1)
        self.assertEqual(result.kpis["waits_censored"], 2)
        self.assertEqual(result.kpis["completed_mean_wait_hours"], 0)
        self.assertEqual(result.kpis["restricted_mean_wait_hours"], 1)
        self.assertEqual(result.kpis["total_observed_wait_hours"], 3)
        self.assertIsNone(result.kpis["mean_turnaround_hours"])
        extended = run_simulation(calls, replace(cfg, horizon_hours=20))
        self.assertGreater(extended.kpis["mean_wait_hours"], result.kpis["mean_wait_hours"])

    def test_hindcast_errors_match_analytical_timing_and_report_coverage(self):
        calls = [VesselCall("A", 0, 10, 0, evidence_type="observed",
                            observed_berth_start_hour=.1, observed_departure_hour=1.5),
                 VesselCall("B", .2, 0, 0, evidence_type="observed",
                            observed_berth_start_hour=1.2, observed_departure_hour=1.4)]
        result = run_simulation(calls, self.config())
        diagnostics = result.kpis["operational_prediction_diagnostics"]
        self.assertEqual(diagnostics["status"], "observations_compared_not_independently_validated")
        self.assertAlmostEqual(diagnostics["berth_start"]["mae_hours"], .1)
        self.assertAlmostEqual(diagnostics["berth_start"]["bias_hours"], -.1)
        self.assertAlmostEqual(diagnostics["departure"]["mae_hours"], .35)
        self.assertAlmostEqual(diagnostics["departure"]["rmse_hours"], math.sqrt(.125))
        self.assertEqual(diagnostics["departure"]["paired_calls"], 2)
        self.assertEqual(diagnostics["departure"]["missing_prediction_or_censored_calls"], 0)
        short = run_simulation(calls, replace(self.config(), horizon_hours=.5))
        self.assertEqual(short.kpis["operational_prediction_diagnostics"]["departure"]["paired_calls"], 0)
        self.assertEqual(short.kpis["operational_prediction_diagnostics"]["departure"]["missing_prediction_or_censored_calls"], 2)
        self.assertIsNone(short.kpis["operational_prediction_diagnostics"]["departure"]["mae_hours"])

    def test_synthetic_times_cannot_validate_operational_predictions(self):
        result = run_simulation([VesselCall("A", 0, 1, 0, observed_berth_start_hour=.1,
                                           observed_departure_hour=.5)], self.config())
        diagnostics = result.kpis["operational_prediction_diagnostics"]
        self.assertEqual(diagnostics["status"], "not_validated")
        self.assertEqual(diagnostics["nonobserved_calls_with_supplied_times_ignored"], 1)
        self.assertIsNone(result.vessels[0]["departure_error_hours"])

    def test_family_guard_matches_nist_and_conservatively_adjusts_selection(self):
        details = _confidence_details(5, 5)
        self.assertAlmostEqual(details["critical_value"], 3.366)
        self.assertEqual(details["requested_one_sided_alpha"], .01)
        for size in (3, 4, 5):
            self.assertLessEqual(_confidence_details(size, 5)["used_one_sided_alpha"], .05 / size)
        values = [0, 1, 2, 3, 4, 5]
        self.assertLess(_lower_bound(values, family_size=5), _lower_bound(values, family_size=1))

    def test_wait_gain_cannot_accept_more_unfinished_cargo_even_if_call_counts_match(self):
        config = self.config()
        def fake(calls, candidate_config, *args, **kwargs):
            faster = candidate_config.tractor_cycle_minutes < config.tractor_cycle_minutes
            kpis = {"gate_status": "eligible", "mean_wait_hours": 5 if faster else 10,
                    "served_calls": 3, "end_backlog_calls": 2, "unserved_calls": 2,
                    "incompatible_calls": 0, "service_backlog_boxes": 101 if faster else 100}
            checks = {name: True for name in REQUIRED_PHYSICAL_CHECKS}
            checks["cancelled"] = False
            return SimulationResult(candidate_config, [], [], [], kpis, checks)
        with patch("portlab.engine.run_simulation", side_effect=fake):
            result = compare_candidates([], config)
        candidate = next(row for row in result["candidates"] if row["name"] == "Faster tractor dispatch")
        self.assertGreater(candidate["lower_bound_hours"], 0)
        self.assertEqual(candidate["backlog_delta"], 0)
        self.assertEqual(candidate["service_backlog_boxes_delta"], 1)
        self.assertFalse(candidate["accepted"])
        self.assertIn("unfinished cargo", candidate["reason"])

    def test_cancel_returns_consistent_partial_inventory(self):
        boundaries = [0]
        def cancel():
            boundaries[0] += 1
            return boundaries[0] > 8
        result = run_simulation([VesselCall("A", 0, 40, 30)], self.config(), cancel=cancel)
        self.assertTrue(result.checks["cancelled"])
        self.assertLess(result.kpis["simulated_hours"], 60)
        self.assert_conserved(result)

    def test_compatibility_censoring_and_wait_lower_bound(self):
        calls = [VesselCall("LARGE", 0, 1, 1, length_m=500),
                 VesselCall("A", 0, 100, 0), VesselCall("B", 0, 10, 0)]
        cfg = replace(self.config(), horizon_hours=2)
        result = run_simulation(calls, cfg)
        rows = {row["vessel_id"]: row for row in result.vessels}
        self.assertEqual(rows["LARGE"]["status"], "incompatible")
        self.assertEqual(rows["A"]["status"], "in_service")
        self.assertEqual(rows["B"]["status"], "waiting")
        self.assertTrue(rows["B"]["wait_censored"])
        self.assertEqual(rows["B"]["wait_hours"], 2)
        self.assertEqual(result.kpis["unserved_calls"], 3)
        self.assertEqual(result.kpis["end_backlog_calls"], 2)
        self.assert_conserved(result)

    def test_exact_horizon_events_are_processed(self):
        cfg = replace(self.config(), horizon_hours=1, tractor_cycle_minutes=30,
                      crane_moves_per_hour=2)
        result = run_simulation([VesselCall("A", 0, 1, 0)], cfg)
        self.assertEqual(result.vessels[0]["departure_hour"], 1)
        self.assertEqual(result.kpis["served_calls"], 1)
        self.assertEqual(result.kpis["stock_flow_samples"][-1]["served_calls"], 1)
        self.assertEqual(result.kpis["stock_flow_samples"][-1]["working_calls"], 0)
        self.assert_conserved(result)

    def test_shared_resource_intervals_do_not_overlap(self):
        cfg = replace(self.config(), berths=2, cranes=2, cranes_per_vessel=2,
                      tractors=2, yard_capacity_boxes=30)
        calls = [VesselCall("A", 0, 15, 15), VesselCall("B", 0, 15, 15)]
        result = run_simulation(calls, cfg)
        for resource in ("crane", "tractor", "berth"):
            for resource_id in range(getattr(cfg, resource + "s")):
                intervals = sorted((row for row in result.intervals
                                    if row["resource"] == resource and row["resource_id"] == resource_id),
                                   key=lambda row: row["start_time"])
                for first, second in zip(intervals, intervals[1:]):
                    self.assertLessEqual(first["end_time"], second["start_time"] + 1e-9)
        self.assert_conserved(result)

    def test_random_draws_stable_by_entity_stage(self):
        expected = _factor(4, "A", "import", 10, "crane", .2)
        _factor(4, "other", "export", 2, "tractor", .2)
        self.assertEqual(expected, _factor(4, "A", "import", 10, "crane", .2))
        self.assertNotEqual(expected, _factor(5, "A", "import", 10, "crane", .2))
        cfg = replace(self.config(), service_variability=.2)
        calls = [VesselCall("A", 0, 20, 10), VesselCall("B", 1, 10, 5)]
        first = run_simulation(calls, cfg)
        second = run_simulation(calls, cfg)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.vessels, second.vessels)

    def test_recommendations_require_current_gate(self):
        result = compare_candidates([VesselCall("A", 0, 1, 0)], self.config())
        self.assertEqual(result["status"], "gate_not_met")
        self.assertEqual(result["candidates"], [])

    def test_paired_comparison_reports_constraints_and_scope(self):
        cfg = replace(self.config(), horizon_hours=10, observation_hours=2,
                      persistence_hours=1, replications=3, yard_capacity_boxes=1000)
        calls = [VesselCall(str(i), 0, 100, 0) for i in range(4)]
        result = compare_candidates(calls, cfg)
        self.assertEqual(result["status"], "evaluated")
        self.assertIn("Retrospective", result["comparison_scope"])
        categories = [row["category"] for row in result["candidates"]]
        self.assertEqual(categories[:2], ["operational", "operational"])
        for candidate in result["candidates"]:
            self.assertEqual(candidate["paired_replications"], 3)
            self.assertEqual([row["seed"] for row in candidate["paired_results"]], [42, 1051, 2060])
            if candidate["accepted"]:
                self.assertGreater(candidate["lower_bound_hours"], 0)
                self.assertTrue(all(pair[key] <= 0 for pair in candidate["paired_results"]
                                    for key in ("backlog_delta", "unserved_delta", "incompatible_delta")))

    def test_large_workloads_report_aggregation_and_keep_counts(self):
        cfg = replace(self.config(), horizon_hours=1, yard_capacity_boxes=1000)
        result = run_simulation([VesselCall("A", 0, 9000, 5000)], cfg, capture_events=False)
        self.assertGreater(result.checks["aggregation_batch_boxes"], 1)
        self.assertEqual(result.events, [])
        self.assert_conserved(result)

    def test_horizon_extension_keeps_prefix_jobs_and_departure_unchanged(self):
        cfg = replace(self.config(), horizon_hours=20, yard_capacity_boxes=2000)
        calls = [VesselCall("EARLY", 0, 100, 0), VesselCall("FUTURE", 100, 100000, 0)]
        short = run_simulation(calls, cfg)
        long = run_simulation(calls, replace(cfg, horizon_hours=200))
        self.assertEqual(short.checks["aggregation_batch_boxes"], long.checks["aggregation_batch_boxes"])
        self.assertEqual(short.vessels[0]["departure_hour"], long.vessels[0]["departure_hour"])
        short_handling = [event for event in short.events if event.get("vessel_id") == "EARLY"]
        long_handling = [event for event in long.events if event.get("vessel_id") == "EARLY" and event["time"] <= 20]
        self.assertEqual(short_handling, long_handling)

    def test_future_export_appointments_affect_yard_before_arrival_without_counting_call(self):
        cfg = replace(self.config(), horizon_hours=10, yard_capacity_boxes=10,
                      import_dwell_hours=4, export_lead_hours=12)
        calls = [VesselCall("CURRENT", 0, 50, 0), VesselCall("FUTURE", 13, 0, 100)]
        short = run_simulation(calls, cfg)
        long = run_simulation(calls, replace(cfg, horizon_hours=30))
        short_prefix = [event for event in short.events if event.get("vessel_id") == "CURRENT"]
        long_prefix = [event for event in long.events if event.get("vessel_id") == "CURRENT" and event["time"] <= 10]
        self.assertEqual(short_prefix, long_prefix)
        self.assertEqual(short.kpis["requested_calls"], 1)
        self.assertEqual(len(short.vessels), 1)
        self.assertGreater(short.kpis["future_export_staged_boxes"], 0)
        self.assertTrue(short.checks["future_export_conserved"])
        self.assert_conserved(short)

    def test_huge_workload_rejected_before_allocating_jobs(self):
        cfg = replace(self.config(), yard_capacity_boxes=1)
        with self.assertRaisesRegex(ValueError, "handling jobs"):
            run_simulation([VesselCall("HUGE", 0, 40000, 0)], cfg)

    def test_bad_input_rejected_and_future_arrivals_excluded(self):
        cfg = self.config()
        with self.assertRaises(ValueError):
            run_simulation([VesselCall("A", 0, 1, 0), VesselCall("A", 1, 1, 0)], cfg)
        with self.assertRaises(ValueError):
            run_simulation([VesselCall("A", -1, 1, 0)], cfg)
        result = run_simulation([VesselCall("future", 61, 1, 0)], cfg)
        self.assertEqual(result.kpis["requested_calls"], 0)
        self.assert_conserved(result)


if __name__ == "__main__":
    unittest.main()
