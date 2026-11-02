"""Regression coverage for cross-catalog data loss and atomic export failures."""
from pathlib import Path
import csv
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io
from catalog_engine.runner import Settings, run
from catalog_engine.canonical import ParameterCatalog, normalize_fact
from catalog_engine.exports import write_outputs, table_cell_coverage
from catalog_engine.normalized_exports import normalize_documents
from catalog_engine.schema import Schema
from catalog_engine.storage import write_csv, ArtifactWriteError
from catalog_engine.units import unit_family, unit_multiplier


def fact(**changes):
    item = dict(fact_id='axis', source_file='axis.pdf', model='M1', configuration='base',
        base_model='M1', manufacturer='Example', source_page=1, source_row=1, source_table=1,
        source_row_id='1:1:1', parameter='rapid_traverse_x_m_min', category='Feedrate',
        value='45 (1771.7)', value_raw='45 (1771.7)', base_value='45 (1771.7)',
        unit_source='m/min (ipm)', unit_expected='m/min', source_label='Rapid traverse / X',
        availability='present', warnings=[], condition='', condition_data={}, options=[])
    item.update(changes)
    return item


def document(items):
    return dict(source_file=items[0]['source_file'], source_sha256='original-pdf-hash', facts=items,
        registry={items[0]['model']: dict(model=items[0]['model'], base_model=items[0]['model'], configurations=['base'])},
        raw_tables=[], source_pages=[], review=[], cell_statuses=[], spec_pages=[1], unmapped_fields=[])


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.schema = Schema()
        self.catalog = ParameterCatalog(self.schema)

    def test_xyz_and_individual_axes_share_values(self):
        grouped = fact(parameter='rapid_traverse_xyz_m_min', value='45/45/45', unit_source='m/min')
        component = normalize_fact(grouped, self.catalog)[1]
        separate = normalize_fact(fact(), self.catalog)[0]
        self.assertEqual(component['canonical_parameter'], separate['canonical_parameter'])
        self.assertEqual(component['normalized_value'], separate['normalized_value'])
        self.assertEqual(component['source_value_raw'], grouped['value_raw'])

    def test_rotary_axis_pair_and_rapid_axis_have_the_same_parameter(self):
        pair = fact(parameter='rotary_axis_speed_rpm', value='60/100', unit_source='rpm',
                    unit_expected='rpm', source_label='B/C 轴最高转速')
        separate = fact(parameter='rapid_traverse_b_rpm', value='25', unit_source='r/min', unit_expected='rpm')
        pair_axis = normalize_fact(pair, self.catalog)[1]
        individual = normalize_fact(separate, self.catalog)[0]
        self.assertEqual(pair_axis['canonical_parameter'], individual['canonical_parameter'])
        self.assertEqual(pair_axis['canonical_unit'], individual['canonical_unit'])

    def test_cutting_range_converted_and_broadcast_to_named_axes(self):
        item = fact(parameter='cutting_feed_xyz_mm_min', value='1 ~ 6000', base_value='1 ~ 6000',
                    unit_source='mm/min', unit_expected='mm/min')
        records = normalize_fact(item, self.catalog)
        self.assertEqual([r['normalized_value'] for r in records], ['0.001 ~ 6'] * 4)
        self.assertEqual(records[1]['canonical_parameter'], 'cutting_feed_x_m_min')
        self.assertTrue(all(r['canonical_unit'] == 'm/min' for r in records))

    def test_vector_options_are_not_discarded_when_axes_expand(self):
        item = fact(parameter='rapid_traverse_xyz_m_min', value='30/30/18 {40/40/24}',
                    unit_source='m/min', options=['40/40/24'])
        records = normalize_fact(item, self.catalog)
        self.assertEqual(records[-1]['display_value'], '18 {24}')
        self.assertEqual(records[-1]['normalized_options'], ['24'])

    def test_cnc_text_and_source_are_preserved(self):
        item = fact(parameter='control_system', value='FANUC 31i Plus', unit_source='-', unit_expected='')
        row = normalize_fact(item, self.catalog, 'sha')[0]
        self.assertEqual(row['normalized_value'], 'FANUC 31i Plus')
        self.assertEqual(row['source_sha256'], 'sha')

    def test_ambiguous_and_conflicting_values_stay_visible(self):
        for changes in [dict(warnings=['source_unit_conflict'], unit_source='N·m'), dict(value='50(RGC), 80(DDM)')]:
            row = normalize_fact(fact(**changes), self.catalog)[0]
            self.assertEqual(row['normalized_value'], '')
            self.assertIn(fact(**changes)['value'], row['display_value'])

    def test_private_glyphs_are_normalized_without_changing_raw(self):
        item = fact(value='770 \uf68830.3\uf689', value_raw='770 \uf68830.3\uf689',
                    parameter='travel_x_mm', unit_source='mm (inch)', unit_expected='mm')
        row = normalize_fact(item, self.catalog)[0]
        self.assertEqual(row['normalized_value'], '770')
        self.assertEqual(row['source_value_raw'], item['value_raw'])

    def test_optional_text_is_not_reported_as_base_equipment(self):
        item = fact(parameter='control_system', value='FANUC', unit_source='', unit_expected='', availability='optional_only')
        row = normalize_fact(item, self.catalog)[0]
        self.assertEqual(row['normalized_value'], '')
        self.assertIn('ОПЦИЯ', row['display_value'])

    def test_invalid_fact_is_isolated_and_logged(self):
        bad = fact(fact_id='bad', warnings='serialized JSON is invalid here')
        with self.assertLogs('catalog_engine.normalized_exports', level='ERROR'):
            records, errors = normalize_documents([document([fact(), bad])], self.schema)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(errors), 1)
        self.assertEqual(records[0]['normalized_value'], '45')
        self.assertIn('ОШИБКА', records[1]['display_value'])

    def test_unit_dimensions_do_not_confuse_speed_length_or_torque(self):
        self.assertEqual(unit_family('mm/min'), 'linear_speed')
        self.assertEqual(unit_multiplier('mm/min', 'm/min'), .001)
        self.assertEqual(unit_multiplier('r/min', 'rpm'), 1)
        self.assertEqual(unit_multiplier('N.m', 'N·m'), 1)
        self.assertIsNone(unit_multiplier('N·m', 'm/min'))


class ExportReliabilityTests(unittest.TestCase):
    def test_derived_columns_cannot_blank_axis_specific_sources(self):
        scalar = fact()
        group = fact(fact_id='group', source_file='group.pdf', model='M2', base_model='M2',
                     parameter='rapid_traverse_xyz_m_min', value='30/30/18', base_value='30/30/18', unit_source='m/min')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_outputs(output, [document([scalar]), document([group])], Schema(), [])
            with (output / 'ALL_MODELS_wide.csv').open(encoding='utf-8-sig') as stream:
                rows = list(csv.DictReader(stream))
            self.assertIn('45', rows[0]['[Feedrate] rapid_traverse_x_m_min'])
            with (output / 'ALL_MODELS_NORMALIZED_wide.csv').open(encoding='utf-8-sig') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]['[Feedrate] rapid_traverse_x_m_min'], '45')
            self.assertEqual(rows[1]['[Feedrate] rapid_traverse_x_m_min'], '30')

    def test_failed_csv_write_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.csv'
            path.write_text('previous complete export', encoding='utf-8')
            def broken_rows():
                yield {'value': 1}
                raise ValueError('simulated mid-export failure')
            with self.assertRaises(ArtifactWriteError):
                write_csv(path, broken_rows(), ['value'])
            self.assertEqual(path.read_text(), 'previous complete export')
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_missing_bbox_does_not_prove_cell_coverage(self):
        doc = document([fact(cell_bbox=[], value_raw='45')])
        doc['raw_tables'] = [dict(page=1, source_table=1, rows=[dict(row_index=1, values=[dict(text='45', bbox=[1, 2, 3, 4])])])]
        self.assertEqual(table_cell_coverage([doc])[0]['source_cells_not_extracted'], 1)


class RunnerRecoveryTests(unittest.TestCase):
    def _settings(self, root):
        input_dir = root / 'pdf'
        input_dir.mkdir()
        (input_dir / 'axis.pdf').write_bytes(b'synthetic input; extraction is mocked')
        return Settings(input_dir=input_dir, output_root=root / 'output', cache_root=root / 'cache', use_cache=False)

    def test_successful_runner_creates_checkpoint_log_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            with patch('catalog_engine.runner.extract_pdf', return_value=document([fact()])), redirect_stdout(io.StringIO()):
                output, summary, code = run(settings)
            self.assertEqual(code, 0)
            self.assertEqual(summary['normalization']['facts_without_export'], 0)
            self.assertTrue((output / 'EXTRACTION_CHECKPOINT.json').is_file())
            self.assertTrue((output / 'RUN_DEBUG.log').is_file())
            self.assertTrue((output / 'ALL_MODELS_NORMALIZED_wide.csv').is_file())

    def test_failed_export_leaves_replayable_checkpoint_and_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            with patch('catalog_engine.runner.extract_pdf', return_value=document([fact()])), \
                 patch('catalog_engine.runner.write_outputs', side_effect=RuntimeError('simulated export failure')), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError) as captured:
                    run(settings)
            output = captured.exception.run_directory
            self.assertTrue((output / 'documents' / 'axis_report.json').is_file())
            self.assertIn('simulated export failure', (output / 'RUN_ABORTED.json').read_text())
            self.assertIn('Traceback', (output / 'RUN_DEBUG.log').read_text())


if __name__ == '__main__':
    unittest.main()
