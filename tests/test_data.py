from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from dataclasses import asdict
import csv
import json
import unittest

from portlab.contracts import DatasetBundle, SimulationResult, TerminalConfig
from portlab.data import dataset_from_project, export_report, forecast_series, load_datasets, load_demo


ROOT = Path(__file__).resolve().parent.parent


class DataTests(unittest.TestCase):
    def make_csv(self, folder, name, headers, rows):
        path = Path(folder) / name
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def test_observed_history_exact_and_no_guam_teu_inferred(self):
        dataset = load_demo(ROOT / 'data')
        self.assertEqual(dataset.annual_series[('Guam', 'boxes')],
                         [(2020, 85143), (2021, 86794), (2022, 89052),
                          (2023, 85627), (2024, 85258), (2025, 83574)])
        self.assertEqual(len(dataset.annual_series[('Conley', 'boxes')]), 10)
        self.assertEqual(dict(dataset.annual_series[('Conley', 'TEU')])[2018], 281978)
        self.assertEqual(dict(dataset.annual_series[('Conley', 'TEU')])[2020], 283061)
        self.assertNotIn(('Guam', 'TEU'), dataset.annual_series)
        self.assertEqual(len(dataset.calls), 48)
        self.assertTrue(all(call.evidence_type == 'synthetic' for call in dataset.calls))
        self.assertTrue(any('sensitivity' in note for note in dataset.evidence_notes))

    def test_original_anylogic_aliases_default_port(self):
        with TemporaryDirectory() as folder:
            path = self.make_csv(folder, 'old.csv', ['VesselID', 'ETA_RealHours', 'ImportContainers', 'ExportContainers'],
                                 [['old-1', 2.5, 12, 7]])
            dataset = load_datasets([path], default_port='Conley')
            call = dataset.calls[0]
            self.assertEqual((call.port, call.arrival_hour, call.import_boxes, call.export_boxes), ('Conley', 2.5, 12, 7))
            self.assertEqual(call.evidence_type, 'unverified')
            self.assertTrue(any('selected port Conley' in note for note in dataset.evidence_notes))

    def test_reject_duplicate_calls_across_files_and_ports_are_distinct(self):
        with TemporaryDirectory() as folder:
            headers = ['port', 'vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes']
            path1 = self.make_csv(folder, 'a.csv', headers, [['Guam', 'SHIP', 0, 1, 1], ['Conley', 'SHIP', 0, 1, 1]])
            path2 = self.make_csv(folder, 'b.csv', headers, [['Guam', 'ship', 4, 2, 0]])
            self.assertEqual(len(load_datasets([path1]).calls), 2)
            with self.assertRaisesRegex(ValueError, 'duplicate vessel'):
                load_datasets([path1, path2])

    def test_counts_missing_negative_fractional_and_nonfinite_rejected(self):
        with TemporaryDirectory() as folder:
            headers = ['vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes']
            for value in ('', -1, 1.5, 'nan', 'inf'):
                with self.subTest(value=value):
                    path = self.make_csv(folder, 'a.csv', headers, [['a', 0, value, 1]])
                    with self.assertRaises(ValueError):
                        load_datasets([path])

    def test_utc_origin_across_files_and_ordering(self):
        with TemporaryDirectory() as folder:
            headers = ['port', 'vessel_id', 'arrival_utc', 'import_boxes', 'export_boxes', 'evidence_type', 'evidence_reference']
            one = self.make_csv(folder, 'a.csv', headers, [['Guam', 'a', '2026-10-04T14:00:00+02:00', 2, 0, 'observed', 'log-1']])
            two = self.make_csv(folder, 'b.csv', headers, [['Guam', 'b', '2026-10-04T10:00:00Z', 3, 0, 'observed', 'log-1']])
            calls = load_datasets([one, two]).calls
            self.assertEqual([(call.vessel_id, call.arrival_hour) for call in calls], [('b', 0), ('a', 2)])
            self.assertTrue(all(call.evidence_type == 'observed' for call in calls))

    def test_mixed_time_basis_rejected(self):
        with TemporaryDirectory() as folder:
            one = self.make_csv(folder, 'a.csv', ['vessel_id', 'arrival_utc', 'import_boxes', 'export_boxes'], [['a', '2026-10-04T10:00:00Z', 1, 0]])
            two = self.make_csv(folder, 'b.csv', ['vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes'], [['b', 2, 1, 0]])
            with self.assertRaisesRegex(ValueError, 'time basis|relative hours'):
                load_datasets([one, two])

    def test_service_dates_and_observed_reference(self):
        with TemporaryDirectory() as folder:
            headers = ['vessel_id', 'arrival_utc', 'import_boxes', 'export_boxes', 'berth_start_utc', 'departure_utc', 'evidence_type', 'evidence_reference']
            path = self.make_csv(folder, 'a.csv', headers, [['a', '2026-10-04T10:00:00Z', 1, 0, '2026-10-04T12:00:00Z', '2026-10-04T11:00:00Z', 'observed', 'log']])
            with self.assertRaisesRegex(ValueError, 'departure precedes'):
                load_datasets([path])
            path = self.make_csv(folder, 'a.csv', ['vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes', 'evidence_type'], [['a', 0, 1, 0, 'observed']])
            with self.assertRaisesRegex(ValueError, 'need evidence_reference'):
                load_datasets([path])

    def test_observed_service_times_retained_relative_to_shared_utc_origin(self):
        with TemporaryDirectory() as folder:
            headers = ['vessel_id', 'arrival_utc', 'import_boxes', 'export_boxes', 'berth_start_utc', 'departure_utc', 'evidence_type', 'evidence_reference']
            one = self.make_csv(folder, 'a.csv', headers, [['A', '2026-10-04T12:00:00Z', 4, 0, '2026-10-04T14:00:00Z', '2026-10-04T18:00:00Z', 'observed', 'log-1']])
            two = self.make_csv(folder, 'b.csv', headers, [['B', '2026-10-04T10:00:00Z', 2, 1, '', '', 'observed', 'log-1']])
            call = next(call for call in load_datasets([one, two]).calls if call.vessel_id == 'A')
            self.assertEqual((call.arrival_hour, call.observed_berth_start_hour, call.observed_departure_hour), (2, 4, 8))

    def test_elapsed_observed_times_and_provenance_guard(self):
        with TemporaryDirectory() as folder:
            headers = ['vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes', 'observed_berth_start_hour', 'observed_departure_hour', 'evidence_type', 'evidence_reference']
            path = self.make_csv(folder, 'a.csv', headers, [['A', 2, 4, 0, 4, 8, 'observed', 'log-1']])
            call = load_datasets([path]).calls[0]
            self.assertEqual((call.observed_berth_start_hour, call.observed_departure_hour), (4, 8))
            path = self.make_csv(folder, 'a.csv', headers, [['A', 2, 4, 0, 4, 8, 'synthetic', 'example']])
            with self.assertRaisesRegex(ValueError, 'measured service times require'):
                load_datasets([path])

    def test_teu_workload_rejected(self):
        with TemporaryDirectory() as folder:
            path = self.make_csv(folder, 'a.csv', ['vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes', 'unit'], [['a', 0, 100, 50, 'TEU']])
            with self.assertRaisesRegex(ValueError, 'not TEU'):
                load_datasets([path])

    def test_monthly_and_forecast_rows_excluded(self):
        with TemporaryDirectory() as folder:
            headers = ['port', 'fiscal_year', 'metric', 'value', 'unit', 'record_status', 'source_id', 'period_start', 'period_end']
            path = self.make_csv(folder, 'a.csv', headers,
                                 [['Guam', 2025, 'boxes', 12, 'boxes', 'observed', 'LOG', '2025-01-01', '2025-01-31'],
                                  ['Guam', 2026, 'boxes', 100000, 'boxes', 'forecast', 'MODEL', '', ''],
                                  ['Guam', 2024, 'boxes', 85258, 'boxes', 'observed', 'G02', '2023-10-01', '2024-09-30']])
            dataset = load_datasets([path])
            self.assertEqual(dataset.annual_series, {('Guam', 'boxes'): [(2024, 85258)]})
            self.assertTrue(any('nonannual' in note for note in dataset.evidence_notes))

    def test_duplicate_annual_metric_and_ambiguous_units_rejected(self):
        with TemporaryDirectory() as folder:
            headers = ['port', 'fiscal_year', 'metric', 'value', 'unit']
            path = self.make_csv(folder, 'a.csv', headers, [['Guam', 2025, 'boxes', 1, 'boxes'], ['Guam', 2025, 'boxes', 2, 'boxes']])
            with self.assertRaisesRegex(ValueError, 'duplicate annual'):
                load_datasets([path])
            path = self.make_csv(folder, 'a.csv', headers, [['Guam', 2025, 'volume', 1, 'unknown']])
            with self.assertRaisesRegex(ValueError, 'explicit unit'):
                load_datasets([path])

    def test_nested_forecasts_no_future_information_and_gaps_rejected(self):
        first = [(2016 + index, value) for index, value in enumerate([100, 110, 105, 108, 103, 106])]
        extended = first + [(2022, 99999)]
        a = forecast_series({('Test', 'boxes'): first})[0]
        b = forecast_series({('Test', 'boxes'): extended})[0]
        self.assertEqual(a['backtests'], b['backtests'][:len(a['backtests'])])
        self.assertTrue(all(test['train_last_year'] < test['year'] for test in b['backtests']))
        self.assertEqual(a['status'], 'exploratory_not_validated')
        self.assertIn('not a confidence interval', a['uncertainty_note'])
        gap = forecast_series({('Test', 'boxes'): [(2020, 1), (2022, 2), (2023, 3)]})[0]
        self.assertIsNone(gap['forecast'])
        self.assertEqual(gap['status'], 'insufficient_data')

    def test_export_complete_roundtrip_and_html_escaped(self):
        dataset = DatasetBundle(evidence_notes=['<script>bad</script>'])
        result = SimulationResult(TerminalConfig(), [{'vessel_id': '=BAD()', 'status': 'censored'}],
                                  [{'time': 0, 'custom_state': 9}], [], {'served_calls': 0}, {'accounting': True})
        with TemporaryDirectory() as folder:
            paths = export_report(Path(folder), result, [], None, dataset)
            self.assertTrue((Path(folder) / 'results.xlsx').is_file())
            full = json.loads((Path(folder) / 'complete_report.json').read_text())
            self.assertEqual(full['simulation']['vessels'][0]['vessel_id'], '=BAD()')
            self.assertEqual(full['simulation']['config']['port'], 'Guam')
            csv_text = (Path(folder) / 'vessel_results.csv').read_text(encoding='utf-8-sig')
            self.assertIn("'=BAD()", csv_text)
            self.assertIn('custom_state', (Path(folder) / 'resource_intervals.csv').read_text(encoding='utf-8-sig'))
            report = (Path(folder) / 'report.html').read_text()
            self.assertIn('&lt;script&gt;bad&lt;/script&gt;', report)
            self.assertNotIn('<script>bad</script>', report)
            self.assertGreaterEqual(len(paths), 13)


class ProjectDataTests(unittest.TestCase):
    def project(self):
        from portlab.contracts import VesselCall
        return {'schema_version': 1, 'config': TerminalConfig().to_dict(),
                'calls': [asdict(VesselCall('A', 0, 12, 3, port='Guam', evidence_type='unverified'))],
                'annual_series': [{'port': 'Guam', 'metric': 'boxes', 'history': [[2024, 85258], [2025, 83574]]}],
                'evidence_notes': ['Uploaded observations remain unverified.'],
                'sources': ['local-schedule.csv']}

    def test_current_schema_roundtrip_and_preserves_zero(self):
        value = self.project()
        value['annual_series'][0]['history'].insert(0, [2023, 0])
        restored = dataset_from_project(json.loads(json.dumps(value)))
        self.assertEqual(restored.calls[0].import_boxes, 12)
        self.assertEqual(restored.calls[0].evidence_type, 'unverified')
        self.assertEqual(restored.annual_series[('Guam', 'boxes')], [(2023, 0), (2024, 85258), (2025, 83574)])
        self.assertEqual(restored.sources, value['sources'])
        self.assertEqual(restored.evidence_notes, value['evidence_notes'])

    def test_optional_measurements_backwards_compatible_and_checked(self):
        value = self.project()
        for name in ('observed_berth_start_hour', 'observed_departure_hour'):
            value['calls'][0].pop(name, None)
        restored = dataset_from_project(value)
        self.assertIsNone(restored.calls[0].observed_berth_start_hour)
        self.assertIsNone(restored.calls[0].observed_departure_hour)
        value['calls'][0].update(evidence_type='observed', observed_berth_start_hour=2, observed_departure_hour=4)
        self.assertEqual(dataset_from_project(value).calls[0].observed_departure_hour, 4)
        value['calls'][0]['observed_departure_hour'] = 1
        with self.assertRaisesRegex(ValueError, 'observed departure precedes'):
            dataset_from_project(value)
        value['calls'][0].update(evidence_type='synthetic', observed_departure_hour=4)
        with self.assertRaisesRegex(ValueError, 'requires observed provenance'):
            dataset_from_project(value)



    def test_custom_ports_allowed_and_identifiers_case_insensitive(self):
        value = self.project()
        value['calls'][0]['port'] = 'Research Terminal'
        restored = dataset_from_project(value)
        self.assertEqual(restored.calls[0].port, 'Research Terminal')
        another = deepcopy(value['calls'][0])
        another.update(port='research terminal', vessel_id='a')
        value['calls'].append(another)
        with self.assertRaisesRegex(ValueError, 'duplicate vessel/call'):
            dataset_from_project(value)

    def test_malformed_calls_fail_with_context(self):
        cases = [('vessel_id', 3), ('port', ' '), ('arrival_hour', True),
                 ('arrival_hour', '0'), ('arrival_hour', float('nan')),
                 ('length_m', float('inf')), ('draft_m', 0), ('box40_share', 1.5),
                 ('box40_share', False), ('import_boxes', 1.5), ('export_boxes', True),
                 ('export_boxes', -1), ('evidence_type', '__import__("os")')]
        for name, malformed in cases:
            with self.subTest(name=name, malformed=malformed):
                value = self.project()
                value['calls'][0][name] = malformed
                with self.assertRaisesRegex(ValueError, 'Project call 1'):
                    dataset_from_project(value)
        value = self.project()
        value['calls'][0]['execute'] = 'dangerous expression'
        with self.assertRaisesRegex(ValueError, 'unsupported fields execute'):
            dataset_from_project(value)
        value = self.project()
        del value['calls'][0]['port']
        with self.assertRaisesRegex(ValueError, 'missing fields port'):
            dataset_from_project(value)

    def test_duplicate_annual_series_years_and_units_rejected(self):
        value = self.project()
        value['annual_series'].append({'port': 'guam', 'metric': 'containers', 'history': [[2022, 12]]})
        with self.assertRaisesRegex(ValueError, 'duplicate annual series'):
            dataset_from_project(value)
        value = self.project()
        value['annual_series'][0]['history'].append([2025, 22])
        with self.assertRaisesRegex(ValueError, 'duplicate fiscal year'):
            dataset_from_project(value)
        for changes in ({'metric': 'volume'}, {'unit': 'TEU'}, {'port': None}):
            with self.subTest(changes=changes):
                value = self.project()
                value['annual_series'][0].update(changes)
                with self.assertRaises(ValueError):
                    dataset_from_project(value)

    def test_annual_nan_fractional_year_and_wrong_types_rejected(self):
        for pair in ([2025.5, 12], [True, 12], ['2025', 12], [2025, float('nan')],
                     [2025, float('inf')], [2025, -1], [2025, '123'],
                     [2025, True], [2025], {'year': 2025, 'value': 12}):
            with self.subTest(pair=pair):
                value = self.project()
                value['annual_series'][0]['history'] = [pair]
                with self.assertRaisesRegex(ValueError, 'history row 1'):
                    dataset_from_project(value)

    def test_project_shapes_notes_and_sources_are_checked_as_data(self):
        for value in ([], None, {'schema_version': True}, {'schema_version': 2},
                      {'schema_version': 1, 'calls': {}, 'annual_series': []}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    dataset_from_project(value)
        for field, bad in (('calls', [None]), ('annual_series', [{}]),
                           ('evidence_notes', 'instruction'), ('evidence_notes', [4]),
                           ('sources', {'path': 'file'}), ('sources', [None])):
            with self.subTest(field=field):
                value = self.project()
                value[field] = bad
                with self.assertRaises(ValueError):
                    dataset_from_project(value)
        value = self.project()
        literal = '__import__("os").system("never execute notes")'
        value['evidence_notes'] = [literal]
        value['sources'] = ['javascript:never-execute']
        restored = dataset_from_project(value)
        self.assertEqual(restored.evidence_notes, [literal])
        self.assertEqual(restored.sources, value['sources'])


class ForecastAccuracyTests(unittest.TestCase):
    def test_observed_holdout_accuracy_is_actual_and_matches_independent_arithmetic(self):
        bundle = load_datasets([ROOT / 'data' / 'Port_Guam_Conley_History.xlsx'])
        records = {(row['port'], row['metric']): row for row in forecast_series(bundle.annual_series)}
        guam = records[('Guam', 'boxes')]
        self.assertEqual([(row['year'], row['forecast'], row['actual']) for row in guam['backtests']],
                         [(2024, 85627, 85258), (2025, 85258, 83574)])
        self.assertAlmostEqual(guam['mae'], (369 + 1684) / 2)
        self.assertAlmostEqual(guam['wape_percent'], 100 * (369 + 1684) / (85258 + 83574))
        for key, error in ((('Conley', 'boxes'), [13678, 20421, 61655, -44365, -21657, 7485]),
                           (('Conley', 'TEU'), [24270, 35216, 107886, -80851, -37810, 11215])):
            record = records[key]
            self.assertEqual([row['error'] for row in record['backtests']], error)
            actual = [value for year, value in bundle.annual_series[key] if year >= 2020]
            self.assertAlmostEqual(record['wape_percent'], 100 * sum(abs(value) for value in error) / sum(actual))
            self.assertAlmostEqual(record['mae'], sum(abs(value) for value in error) / len(error))
            self.assertAlmostEqual(record['rmse'], (sum(value * value for value in error) / len(error)) ** .5)
            self.assertAlmostEqual(record['mean_error'], sum(error) / len(error))
            self.assertEqual(record['baseline_skill_percent'], 0)
            self.assertEqual([row['year'] for row in record['backtests']], list(range(2020, 2026)))
        self.assertTrue(all(record['method'] == 'last_year' for record in records.values()))
        self.assertTrue(all(record['history_end_year'] == 2025 for record in records.values()))
        self.assertTrue(all(record['status'] == 'exploratory_not_validated' for record in records.values()))

    def test_promotion_needs_earlier_and_recent_evidence(self):
        short = forecast_series({('Fixture', 'boxes'): [(2020 + index, value) for index, value in enumerate([10, 20, 30, 40, 50]) ]})[0]
        self.assertEqual(short['method'], 'last_year')
        self.assertEqual(short['selection_details']['inner_holdouts'], 2)
        longer = forecast_series({('Fixture', 'boxes'): [(2020 + index, value) for index, value in enumerate([10, 20, 30, 40, 50, 60, 70]) ]})[0]
        self.assertEqual(longer['method'], 'damped_trend')
        self.assertEqual(longer['selection_details']['threshold_status'], 'heuristic_prototype_guards')
        self.assertEqual(longer['backtests'][0]['method'], 'last_year')
        self.assertEqual(longer['backtests'][1]['method'], 'last_year')
        self.assertEqual(longer['backtests'][2]['method'], 'damped_trend')
        self.assertTrue(all(row['train_last_year'] < row['year'] for row in longer['backtests']))

    def test_error_envelope_uses_only_preceding_outer_errors(self):
        record = forecast_series({('Guam', 'boxes'): [(2020, 85143), (2021, 86794), (2022, 89052),
                                                    (2023, 85627), (2024, 85258), (2025, 83574)]})[0]
        first, second = record['backtests']
        self.assertIsNone(first['uncertainty_low'])
        self.assertIsNone(first['envelope_hit'])
        self.assertEqual(second['uncertainty_low'], second['forecast'] - abs(first['error']))
        self.assertEqual(second['uncertainty_high'], second['forecast'] + abs(first['error']))
        self.assertFalse(second['envelope_hit'])
        self.assertEqual((record['envelope_hits'], record['envelope_evaluated_holdouts']), (0, 1))
        self.assertEqual((record['uncertainty_low'], record['uncertainty_high']), (81890, 85258))

    def test_zero_history_and_tiny_or_gapped_samples_are_honest(self):
        zero = forecast_series({('Fixture', 'boxes'): [(2020 + index, 0) for index in range(7)]})[0]
        self.assertIsNone(zero['wape_percent'])
        self.assertIsNone(zero['baseline_skill_percent'])
        self.assertEqual((zero['forecast'], zero['mae'], zero['rmse']), (0, 0, 0))
        self.assertTrue(all(row['absolute_percentage_error'] is None for row in zero['backtests']))
        self.assertEqual(zero['status'], 'exploratory_not_validated')
        json.dumps(zero, allow_nan=False)
        tiny = forecast_series({('Fixture', 'boxes'): [(2020, 1), (2021, 2)]})[0]
        self.assertIsNone(tiny['forecast'])
        self.assertIsNone(tiny['mae'])
        gap = forecast_series({('Fixture', 'boxes'): [(2020, 1), (2022, 2), (2023, 3)]})[0]
        self.assertIsNone(gap['forecast'])
        self.assertEqual(gap['status'], 'insufficient_data')

    def test_fixed_benchmarks_use_same_target_years_and_never_select_with_outer_errors(self):
        series = [(2020 + index, value) for index, value in enumerate([10, 20, 30, 40, 50, 60, 7000])]
        shorter = forecast_series({('Fixture', 'boxes'): series[:-1]})[0]
        record = forecast_series({('Fixture', 'boxes'): series})[0]
        self.assertEqual(shorter['backtests'], record['backtests'][:len(shorter['backtests'])])
        self.assertEqual(record['backtests'][-1]['method'], 'damped_trend')
        self.assertTrue(all(row['holdouts'] == record['holdouts'] for row in record['method_comparison']))
        naive = next(row for row in record['method_comparison'] if row['method'] == 'last_year')
        self.assertEqual(record['baseline_wape_percent'], naive['wape_percent'])
        self.assertIn('Retrospective', record['evaluation_note'])

if __name__ == '__main__':
    unittest.main()
