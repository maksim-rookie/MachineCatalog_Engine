import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from catalog_engine.logical_fields import merge_feedrate,normalize_logical_records,FEED_COLUMN,LEGEND
from catalog_engine.dimensions_view import finalize_wide_tables,COLUMN,MISSING
from catalog_engine.storage import write_csv


def fact(key,value,**extra):
    return dict(parameter=key,value=value,value_raw=value,unit_source='m/min',model='M1',source_file='x.pdf',
                configuration='base',source_page=1,fact_id=key,**extra)

def axes(values):return [fact(f'rapid_traverse_{a}_m_min',v) for a,v in values.items()]

class FeedrateTests(unittest.TestCase):
    def test_equal_all_axes(self):self.assertEqual(merge_feedrate(axes({'x':'30','y':'30','z':'30'}))[0],'X/Y/Z: 30 м/мин')
    def test_different_axes(self):self.assertEqual(merge_feedrate(axes({'x':'30','y':'30','z':'24'}))[0],'X: 30/ Y: 30/ Z: 24 м/мин')
    def test_partial_axes(self):self.assertEqual(merge_feedrate(axes({'x':'30','z':'24'}))[0],'X: 30/ Y: !-/ Z: 24 м/мин')
    def test_general_only_not_broadcast(self):self.assertEqual(merge_feedrate([fact('rapid_traverse_xyz_m_min','30')])[0],'!- (общий: 30) м/мин')
    def test_general_vector_labelled(self):
        f=fact('rapid_traverse_xyz_m_min','30/30/24',source_label='X/Y/Z')
        self.assertEqual(merge_feedrate([f])[0],'X: 30/ Y: 30/ Z: 24 м/мин')
    def test_general_vector_without_order_not_assigned_to_axes(self):
        v,_=merge_feedrate([fact('rapid_traverse_xyz_m_min','30/24/18')])
        self.assertEqual(v,'!- (общий: 30 / 24 / 18) м/мин')
    def test_general_and_axes_dedup(self):
        fs=[fact('rapid_traverse_xyz_m_min','30')]+axes({'x':'30','y':'30','z':'30'})
        self.assertEqual(merge_feedrate(fs)[0],'X/Y/Z: 30 м/мин')
    def test_general_partial_duplicate_retains_axis_scope(self):
        value,_=merge_feedrate([fact('rapid_traverse_xyz_m_min','30')]+axes({'x':'30'}))
        self.assertIn('!-',value);self.assertIn('X: 30',value);self.assertNotIn('Y: 30',value)
    def test_conflict_keeps_source_values(self):
        v,n=merge_feedrate([fact('rapid_traverse_xyz_m_min','30')]+axes({'x':'24'}))
        self.assertIn('!-',v);self.assertIn('30',n);self.assertIn('24',n)
    def test_unit_conversion(self):
        f=fact('rapid_traverse_x_m_min','30000');f['unit_source']='mm/min'
        self.assertEqual(merge_feedrate([f])[0],'X: 30/ Y: !-/ Z: !- м/мин')
    def test_uncertainty(self):
        self.assertIn('!-',merge_feedrate([fact('rapid_traverse_x_m_min','30',warnings=['source_unit_conflict'])])[0])

class CompatibilityTests(unittest.TestCase):
    def test_general_order_is_preserved_with_named_confirmation(self):
        from catalog_engine.dimensions_view import merge_dimensions
        fs=[dict(fact('machine_dimensions_mm','1500x2500x1800'),unit_source='mm',source_label='W x L x H'),dict(fact('machine_length_mm','2500'),unit_source='mm',source_label='Length')]
        self.assertEqual(merge_dimensions(fs)[0],'1500 × 2500 × 1800')
    def test_old_names_and_provenance_preserved_and_idempotent(self):
        fs=axes({'x':'30','y':'30','z':'24'})
        fs += [dict(fact(f'machine_dimension_{i}_mm',v),unit_source='mm') for i,v in enumerate(['2500','1500','1800'],1)]
        original=copy.deepcopy(fs);result=normalize_logical_records(fs)
        self.assertEqual(fs,original)
        self.assertEqual({r['parameter'] for r in result},{'machine_dimensions_mm','rapid_traverse_xyz_m_min'})
        self.assertEqual(normalize_logical_records(result),result)
        self.assertEqual(sum(len(r['source_records']) for r in result),6)
        self.assertEqual(next(r['value'] for r in result if r['parameter']=='machine_dimensions_mm'),'2500 × 1500 × 1800')
    def test_repeated_source_does_not_duplicate_records(self):
        f=fact('rapid_traverse_x_m_min','30')
        out=normalize_logical_records([f,f]);self.assertEqual(len(out),1);self.assertEqual(len(out[0]['source_records']),1)
    def test_normalized_historical_records_accepted(self):
        row={'canonical_parameter':'rapid_traverse_x_m_min','source_model':'M1','source_file':'x.pdf','source_value':'30','source_unit':'m/min'}
        out=normalize_logical_records([row]);self.assertEqual(out[0]['value'],'X: 30/ Y: !-/ Z: !- м/мин');self.assertEqual(out[0]['source_records'][0],row)
    def test_absence_confirmation(self):
        f=fact('rapid_traverse_xyz_m_min','-',source_absence_verified=True,evidence='Reviewed page 1')
        self.assertEqual(normalize_logical_records([f])[0]['value'],'-')

    def test_tables_one_field_legend_and_repeatable(self):
        fs=axes({'x':'30','y':'30','z':'24'})+[dict(fact('machine_dimension_1_mm','2500'),unit_source='mm')]
        doc={'source_file':'x.pdf','registry':{'M1':{}},'facts':fs}
        fields=['source_file','model','other',COLUMN,'[Machine dimensions] machine_dimension_1_mm']+[f'[Feedrate] rapid_traverse_{a}_m_min' for a in 'xyz']
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);write_csv(p/'ALL_MODELS_wide.csv',[dict(source_file='x.pdf',model='M1',other=MISSING)],fields)
            finalize_wide_tables(p,[doc]);before=(p/'ALL_MODELS_wide.csv').read_bytes()
            finalize_wide_tables(p,[doc]);self.assertEqual(before,(p/'ALL_MODELS_wide.csv').read_bytes())
            with (p/'ALL_MODELS_wide.csv').open(encoding='utf-8-sig') as f:
                r=csv.DictReader(f);row=next(r);cols=r.fieldnames
            self.assertEqual(row[FEED_COLUMN],'X: 30/ Y: 30/ Z: 24 м/мин')
            self.assertEqual(row[COLUMN],'2500 × !- × !- мм');self.assertEqual(row['other'],'!-')
            self.assertFalse(any(c.endswith(('rapid_traverse_x_m_min','rapid_traverse_y_m_min','rapid_traverse_z_m_min','machine_dimension_1_mm')) for c in cols))
            self.assertEqual(json.loads((p/'FIELD_DISPLAY.json').read_text())['legend'],LEGEND)
