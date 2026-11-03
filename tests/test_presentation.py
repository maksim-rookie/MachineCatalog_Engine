import csv
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from catalog_engine.exports import write_outputs, write_typed_measurements
from catalog_engine.presentation import (GROUPS, LEGEND, REMOVED, hidden_parameters,
                                         project_cells)
from catalog_engine.schema import Schema
from catalog_engine.values import unit_family, unit_multiplier


def fact(parameter, value, unit='mm', **extra):
    return dict(parameter=parameter, value=value, base_value=str(value), unit_source=unit,
                unit_expected=unit, source_file='sample.pdf', model='MC 1000',
                configuration='base', category='Feedrate', **extra)


def cell(facts, key, statuses=()):
    return project_cells(facts, statuses, [key])[key]


class ProjectionTests(unittest.TestCase):
    def test_full_dimensions(self):
        fs = [fact(f'machine_dimension_{i}_mm', i) for i in (1, 2, 3)]
        self.assertEqual(cell(fs, 'machine_dimensions_mm').render(), '1 × 2 × 3 мм')

    def test_duplicate_dimensions(self):
        fs = [fact(f'machine_dimension_{i}_mm', i) for i in (1, 2, 3)]
        fs.append(fact('machine_dimensions_mm', '1x2x3'))
        self.assertEqual(cell(fs, 'machine_dimensions_mm').render(), '1 × 2 × 3 мм')

    def test_dimension_conflict_preserves_both(self):
        fs = [fact(f'machine_dimension_{i}_mm', i) for i in (1, 2, 3)]
        fs.append(fact('machine_dimensions_mm', '4x5x6', source_page=7))
        result = cell(fs, 'machine_dimensions_mm')
        self.assertEqual(result.status, 'REVIEW')
        self.assertIn('4 × 5 × 6', result.render())
        self.assertIn('1 × 2 × 3', result.render())
        self.assertEqual(result.audit[-1]['value'], 3)
        self.assertTrue(any(f.get('source_page') == 7 for f in result.audit))

    def test_partial_dimensions_no_physical_names(self):
        text = cell([fact('machine_dimension_1_mm', 0)], 'machine_dimensions_mm').render()
        self.assertEqual(text, '!- 0 × !- × !- мм')
        self.assertNotIn('длина', text)

    def test_rapid_axes_remain_distinct(self):
        fs = [fact(f'rapid_traverse_{a}_m_min', i, 'm/min') for a, i in zip('xyz', (10, 20, 30))]
        self.assertEqual(cell(fs, 'rapid_traverse_xyz_m_min').render(), 'X: 10; Y: 20; Z: 30 м/мин')

    def test_one_axis_not_broadcast(self):
        text = cell([fact('rapid_traverse_x_m_min', 0, 'm/min')], 'rapid_traverse_xyz_m_min').render()
        self.assertEqual(text, '!- X: 0; Y: !-; Z: !- м/мин')

    def test_explicit_equal_axes(self):
        f = fact('rapid_traverse_xyz_m_min', '24', 'm/min', source_label='Rapid X/Y/Z')
        self.assertEqual(cell([f], f['parameter']).render(), 'X/Y/Z: 24 м/мин')

    def test_unproven_shared_scalar(self):
        f = fact('rapid_traverse_xyz_m_min', '24', 'm/min')
        self.assertEqual(cell([f], f['parameter']).status, 'REVIEW')
        self.assertIn('исходная запись: 24', cell([f], f['parameter']).render())

    def test_explicit_axis_triplet(self):
        f = fact('rapid_traverse_xyz_m_min', '24/24/20', 'm/min', source_label='X/Y/Z')
        self.assertEqual(cell([f], f['parameter']).render(), 'X: 24; Y: 24; Z: 20 м/мин')

    def test_ambiguous_triplet(self):
        f = fact('rapid_traverse_xyz_m_min', '24/24/20', 'm/min')
        self.assertEqual(cell([f], f['parameter']).status, 'REVIEW')

    def test_cutting_conversion_and_zero(self):
        fs = [fact(f'cutting_feed_{a}_m_min', i, 'm/min') for a, i in zip('xyz', ('0', '1.25', '2'))]
        result = cell(fs, 'cutting_feed_xyz_mm_min')
        self.assertEqual(result.render(), 'X: 0/Y: 1250/Z: 2000 мм/мин')
        self.assertEqual(result.numbers, (Decimal(0), Decimal(1250), Decimal(2000)))

    def test_combined_cutting_legacy_alias_converts_once(self):
        f = fact('cutting_feed_xyz_m_min', '1/2/3', 'm/min', source_label='X/Y/Z')
        result = project_cells([f])
        self.assertEqual(result['cutting_feed_xyz_mm_min'].render(), 'X: 1000/Y: 2000/Z: 3000 мм/мин')
        self.assertEqual(project_cells(result), result)

    def test_mm_min_not_reconverted(self):
        f = fact('cutting_feed_xyz_mm_min', '1000', 'mm/min', source_label='X/Y/Z')
        self.assertEqual(cell([f], f['parameter']).render(), 'X/Y/Z: 1000 мм/мин')

    def test_cutting_never_merges_with_rapid(self):
        result = project_cells([fact('cutting_feed_x_m_min', 1, 'm/min'), fact('rapid_traverse_x_m_min', 5, 'm/min')])
        self.assertEqual(set(result), {'cutting_feed_xyz_mm_min', 'rapid_traverse_xyz_m_min'})

    def test_conflicting_axes(self):
        fs = [fact('rapid_traverse_xyz_m_min', '20', 'm/min', source_label='X/Y/Z'), fact('rapid_traverse_x_m_min', '25', 'm/min')]
        result = cell(fs, 'rapid_traverse_xyz_m_min')
        self.assertEqual(result.status, 'REVIEW')
        self.assertIn('25 || 20', result.value)

    def test_table_and_pallet(self):
        result = cell([fact('table_size_mm', '100x200'), fact('pallet_size_mm', '50x60')], 'table_size_mm')
        self.assertEqual(result.render(), 'Стол: 100 × 200 мм; Паллета: 50 × 60 мм')
        self.assertEqual(len(result.audit), 2)

    def test_pallet_only(self):
        self.assertEqual(cell([fact('pallet_size_mm', '50x60')], 'table_size_mm').render(), 'Паллета: 50 × 60 мм')

    def test_table_units_convert(self):
        self.assertEqual(cell([fact('table_size_mm', '10x20', 'cm')], 'table_size_mm').render(), 'Стол: 100 × 200 мм')

    def test_table_conflict(self):
        result = cell([fact('table_size_mm', '10x20'), fact('table_size_mm', '20x30')], 'table_size_mm')
        self.assertEqual(result.status, 'REVIEW')
        self.assertIn('20 × 30', result.render())

    def test_missing_statuses(self):
        self.assertEqual(cell([], 'travel_x_mm').render(), '!-')
        for status in ('ABSENT', 'SOURCE_NOT_LISTED'):
            self.assertEqual(cell([], 'travel_x_mm', [{'parameter': 'travel_x_mm', 'status': status}]).render(), '-')
        self.assertEqual(cell([], 'travel_x_mm', [{'parameter': 'travel_x_mm', 'status': 'SOURCE_BLANK'}]).render(), '!-')
        self.assertEqual(cell([], 'travel_x_mm', [{'parameter': 'travel_x_mm', 'status': 'SOURCE_BLANK', 'source_absence_confirmed': True}]).render(), '-')
        self.assertEqual(cell([], 'travel_x_mm', [{'parameter': 'travel_x_mm', 'status': 'EXTRACTION_FAILED'}]).render(), '!-')

    def test_zero_general(self):
        self.assertEqual(cell([fact('travel_x_mm', 0)], 'travel_x_mm').render(), '0')

    def test_partial_absence_does_not_prove_group_absence(self):
        statuses = [{'parameter': 'rapid_traverse_x_m_min', 'status': 'SOURCE_NOT_LISTED'}]
        self.assertEqual(cell([], 'rapid_traverse_xyz_m_min', statuses).render(), '!-')
        statuses += [{'parameter': f'rapid_traverse_{a}_m_min', 'status': 'SOURCE_NOT_LISTED'} for a in 'yz']
        self.assertEqual(cell([], 'rapid_traverse_xyz_m_min', statuses).render(), '-')

    def test_all_dimensions_absent(self):
        statuses = [{'parameter': f'machine_dimension_{i}_mm', 'status': 'ABSENT'} for i in (1, 2, 3)]
        self.assertEqual(cell([], 'machine_dimensions_mm', statuses).render(), '-')

    def test_both_table_objects_absent(self):
        statuses = [{'parameter': k, 'status': 'ABSENT'} for k in ('table_size_mm', 'pallet_size_mm')]
        self.assertEqual(cell([], 'table_size_mm', statuses).render(), '-')

    def test_unverified_and_legacy_placeholder(self):
        for value in ('[НЕ ИЗВЛЕЧЕНО ИЛИ НЕ УКАЗАНО В КАТАЛОГЕ]', '12'):
            self.assertEqual(cell([fact('travel_x_mm', value, human_verified=False)], 'travel_x_mm').status, 'REVIEW')
        self.assertEqual(cell([fact('travel_x_mm', '[НЕ ИЗВЛЕЧЕНО ИЛИ НЕ УКАЗАНО В КАТАЛОГЕ]')], 'travel_x_mm').render(), '!-')

    def test_idempotence_and_input_immutability(self):
        fs = [fact('cutting_feed_x_m_min', '1.2', 'm/min')]
        original = deepcopy(fs)
        first = project_cells(fs)
        self.assertEqual(first, project_cells(fs))
        self.assertEqual(first, project_cells(project_cells(first)))
        self.assertEqual(fs, original)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            project_cells([{}])
        with self.assertRaises(ValueError):
            project_cells({'bad': 'display'})

    def test_unit_families_and_scaling(self):
        self.assertEqual(unit_family('mm/min'), 'linear_speed')
        self.assertEqual(unit_multiplier('m/min', 'mm/min'), 1000)
        self.assertIsNone(unit_multiplier('mm', 'mm/min'))

    def test_ambiguous_number_and_conflicting_embedded_unit(self):
        for value in ('1,000', '1000 mm/min'):
            f = fact('cutting_feed_x_m_min', value, 'm/min')
            result = cell([f], 'cutting_feed_xyz_mm_min')
            self.assertEqual(result.status, 'REVIEW')
            self.assertEqual(result.numbers, ())

    def test_only_b_is_conditional(self):
        fs = [fact('rapid_traverse_b_rpm', '0', 'rpm')]
        machines = {('sample.pdf', str(i), 'base') for i in range(5)}
        self.assertIn('rapid_traverse_b_rpm', hidden_parameters(fs, machines=machines))
        self.assertNotIn('rapid_traverse_b_rpm', hidden_parameters(fs))
        self.assertEqual(set(REMOVED), hidden_parameters([]))

    def test_schema_cutting_does_not_become_rapid(self):
        schema = Schema()
        self.assertEqual(schema.match(['Cutting feed', 'X-axis'], 'm/min')['key'], 'cutting_feed_x_m_min')


class ExportTests(unittest.TestCase):
    def test_all_wide_exports_share_projection_and_keep_audit(self):
        fs = [fact('rapid_traverse_x_m_min', '10', 'm/min'), fact('cutting_feed_x_m_min', '1', 'm/min')]
        fs += [fact(k, '0', 'rpm') for k in REMOVED]
        fs.append(fact('pallet_size_mm', '100x200'))
        doc = dict(source_file='sample.pdf', facts=fs, registry={'MC 1000': {'configurations': ['base']}}, review=[], cell_statuses=[])
        before = deepcopy(doc)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            write_outputs(output, [doc], Schema(), [])
            tables = [output/'ALL_MODELS_wide.csv', output/'ALL_MODELS_wide_values.csv', output/'ALL_CONFIGURATIONS_wide.csv', output/'by_series/MC_models.csv']
            for path in tables:
                with path.open(encoding='utf-8-sig') as stream:
                    reader = csv.DictReader(stream)
                    row = next(reader)
                    parameters = {c.split('] ')[-1] for c in reader.fieldnames}
                self.assertFalse(parameters & REMOVED)
                self.assertNotIn('pallet_size_mm', parameters)
                self.assertNotIn('rapid_traverse_x_m_min', parameters)
                self.assertIn('rapid_traverse_xyz_m_min', parameters)
                self.assertIn('cutting_feed_xyz_mm_min', parameters)
                self.assertIn('Паллета: 100 × 200 мм', row['[Table] table_size_mm'])
            self.assertEqual((output/'DATA_LEGEND.txt').read_text().strip(), LEGEND)
            self.assertTrue(json.loads((output/'TRANSFORMATION_AUDIT.json').read_text()))
            with (output/'ALL_MODELS_long.csv').open(encoding='utf-8-sig') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), len(fs))
            # A second export must not change source values or double-convert.
            write_outputs(output, [doc], Schema(), [])
            self.assertEqual([f['value'] for f in doc['facts']], [f['value'] for f in before['facts']])

    def test_typed_values_remain_numeric_and_status_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = write_typed_measurements(Path(tmp), [fact('cutting_feed_x_m_min', '0', 'm/min'), fact('cutting_feed_x_m_min', 'bad', 'm/min')])
        self.assertEqual(rows[0]['numeric_value'], '0')
        self.assertEqual(rows[1]['numeric_value'], '')
        self.assertNotIn(rows[1]['numeric_value'], {'-', '!-'})


if __name__ == '__main__':
    unittest.main()
