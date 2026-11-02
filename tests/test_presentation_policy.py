import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from catalog_engine.logical_fields import merge_feedrate,merge_cutting_feed,merge_table_size,normalize_logical_records,REMOVED,CUT,TABLE
from catalog_engine.dimensions_view import finalize_wide_tables,MISSING
from catalog_engine.storage import write_csv
from catalog_engine.canonical import normalize_fact,ParameterCatalog
from catalog_engine.schema import Schema


def f(key,value,unit,**kw):
    return dict(parameter=key,value=value,value_raw=value,unit_source=unit,model='M1',source_file='x.pdf',configuration='base',fact_id=key,source_page=1,**kw)


class GroupRulesTests(unittest.TestCase):
    def test_rapid_explicit_equal_with_unit(self):
        self.assertEqual(merge_feedrate([f(f'rapid_traverse_{a}_m_min','30','m/min') for a in 'xyz'])[0],'X/Y/Z: 30 м/мин')
    def test_rapid_diff_and_partial(self):
        value,_=merge_feedrate([f('rapid_traverse_x_m_min','30','m/min'),f('rapid_traverse_z_m_min','24','m/min')])
        self.assertEqual(value,'X: 30/ Y: !-/ Z: 24 м/мин')
    def test_compact_general_is_not_proof_for_all_axes(self):
        value,note=merge_feedrate([f('rapid_traverse_xyz_m_min','30','m/min')])
        self.assertIn('!-',value);self.assertNotIn('X/Y/Z:',value)
    def test_verified_missing_axis(self):
        value,_=merge_feedrate([f('rapid_traverse_x_m_min','30','m/min')],lambda a:'-' if a=='Y' else '!-')
        self.assertIn('Y: -/',value);self.assertIn('Z: !-',value)
    def test_cutting_conversion_and_partial(self):
        value,_=merge_cutting_feed([f('cutting_feed_x_m_min','1.5','m/min'),f('cutting_feed_z_m_min','0.5','m/min')])
        self.assertEqual(value,'X: 1500; Y: !-; Z: 500 мм/мин')
    def test_cutting_general_and_axes_equivalence(self):
        facts=[f(CUT,'30000/30000/24000','mm/min',source_label='X/Y/Z')]+[f(f'cutting_feed_{a}_m_min',v,'m/min') for a,v in zip('xyz',['30','30','24'])]
        self.assertEqual(merge_cutting_feed(facts)[0],'X: 30000; Y: 30000; Z: 24000 мм/мин')
    def test_axis_conflict_preserves_known_other_axes(self):
        facts=[f('rapid_traverse_x_m_min','30','m/min'),f('rapid_traverse_x_m_min','40','m/min'),f('rapid_traverse_z_m_min','24','m/min')]
        value,note=merge_feedrate(facts)
        for text in ['!-','30','40','Z: 24']:self.assertIn(text,value)
    def test_partial_uncertainty_preserves_other_axis(self):
        value,_=merge_cutting_feed([f('cutting_feed_x_m_min','2','m/min'),f('cutting_feed_z_m_min','3','unknown')])
        self.assertIn('X: 2000',value);self.assertIn('Z: !-',value);self.assertIn('3 unknown',value)
    def test_table_and_pallet_both_remain_even_if_equal(self):
        value,_=merge_table_size([f('table_size_mm','600x500','mm'),f('pallet_size_mm','0.6x0.5','m')])
        self.assertEqual(value,'Стол: 600 × 500 мм; Паллета: 600 × 500 мм')
    def test_partial_table_keeps_known_component(self):
        self.assertEqual(merge_table_size([f('table_size_mm','600x-','mm')])[0],'Стол: 600 × !- мм')
    def test_table_conflict_visible(self):
        value,_=merge_table_size([f('table_size_mm','600x500','mm'),f('table_size_mm','700x500','mm')])
        self.assertIn('!-',value);self.assertIn('600',value);self.assertIn('700',value)
    def test_zero_is_not_absence(self):
        self.assertEqual(merge_feedrate([f(f'rapid_traverse_{a}_m_min',0,'m/min') for a in 'xyz'])[0],'X/Y/Z: 0 м/мин')
        item=f('rapid_traverse_x_m_min',0,'m/min');item['category']='Feedrate'
        self.assertEqual(normalize_fact(item,ParameterCatalog(Schema()))[0]['normalized_value'],'0')
    def test_groups_and_unit_conversion_idempotent(self):
        raw=[f('rapid_traverse_x_m_min','1','m/min'),f('cutting_feed_x_m_min','1','m/min'),f('table_size_mm','600x500','mm')]
        original=copy.deepcopy(raw);logical=normalize_logical_records(raw)
        self.assertEqual(raw,original);self.assertEqual(normalize_logical_records(logical),logical)
        values={r['parameter']:r['value'] for r in logical}
        self.assertIn('X: 1000',values[CUT]);self.assertIn('X: 1/',values['rapid_traverse_xyz_m_min']);self.assertIn('Стол:',values[TABLE])

    def test_unqualified_group_is_not_projected_as_confirmed_axes(self):
        from catalog_engine.normalization import projection
        item=f('rapid_traverse_xyz_m_min','30','m/min')
        self.assertEqual(len(normalize_fact(item,ParameterCatalog(Schema()))),1)
        item['value']='30/30/24'
        self.assertEqual(projection(item),[])
    def test_old_logical_cutting_record_migrates_from_original_units(self):
        item={'parameter':'cutting_feed_xyz_m_min','logical_canonical':True,'value':'1','unit':'m/min','source_records':[f('cutting_feed_x_m_min','1','m/min')]}
        value=normalize_logical_records([item])[0]
        self.assertEqual(value['parameter'],CUT);self.assertIn('X: 1000',value['value'])
        self.assertEqual(normalize_logical_records([value]),[value])
    def test_table_components_dedup_against_general_size(self):
        facts=[f('table_size_mm','600x500','mm'),f('table_dimension_1_mm','600','mm'),f('table_dimension_2_mm','500','mm')]
        self.assertEqual(merge_table_size(facts)[0],'Стол: 600 × 500 мм')
    def test_hidden_rapid_c_alias_does_not_hide_other_rotary_sources(self):
        from catalog_engine.normalized_exports import write_normalized_outputs
        facts=[f('rapid_traverse_c_rpm','20','rpm',unit_expected='rpm'),f('rotary_axis_speed_rpm','60/100','rpm',unit_expected='rpm',source_label='B/C axis rotating speed',category='Feedrate')]
        doc={'source_file':'x.pdf','registry':{'M1':{'configurations':['base']}},'facts':facts,'cell_statuses':[]}
        with tempfile.TemporaryDirectory() as td:
            write_normalized_outputs(Path(td),[doc],Schema())
            with (Path(td)/'ALL_MODELS_NORMALIZED_wide.csv').open(encoding='utf-8-sig') as stream:row=next(csv.DictReader(stream))
            self.assertEqual(row['[Feedrate] rotary_axis_speed_c_rpm'],'100')
            self.assertFalse(any(c.endswith('] rapid_traverse_c_rpm') for c in row))
    def test_export_only_explicit_exclusions_and_tool_fields_retained(self):
        # Include one rare non-listed field; its occupancy must not hide it.
        fields=['source_file','model','[Other] rare_kept','[Feedrate] rapid_traverse_x_m_min','[Feedrate] cutting_feed_x_m_min','[Table] table_size_mm','[Table] pallet_size_mm']
        columns={k:f'[Other] {k}' for k in REMOVED}
        fields+=list(columns.values())
        tools={'tool_max_diameter_mm':'80','tool_max_length_mm':'300','tool_max_weight_kg':'8'}
        fields += ['[Automatic tool changer (ATC)] '+k for k in tools]
        facts=[f('rapid_traverse_x_m_min','30','m/min'),f('cutting_feed_x_m_min','1','m/min'),f('table_size_mm','600x500','mm')]
        facts += [f(k,v,'kg' if k.endswith('_kg') else 'mm') for k,v in tools.items()]
        facts += [f(k,'10','mm') for k in REMOVED]
        doc={'source_file':'x.pdf','registry':{'M1':{}},'facts':facts}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);row={'source_file':'x.pdf','model':'M1','[Other] rare_kept':'0',**{v:'10' for v in columns.values()},**{'[Automatic tool changer (ATC)] '+k:v for k,v in tools.items()}}
            write_csv(root/'ALL_MODELS_wide.csv',[row],fields)
            finalize_wide_tables(root,[doc]);before=(root/'ALL_MODELS_wide.csv').read_bytes()
            finalize_wide_tables(root,[doc]);self.assertEqual(before,(root/'ALL_MODELS_wide.csv').read_bytes())
            with (root/'ALL_MODELS_wide.csv').open(encoding='utf-8-sig') as stream:r=csv.DictReader(stream);result=next(r);cols=r.fieldnames
            self.assertTrue(all(k not in c for k in REMOVED for c in cols))
            self.assertEqual(result['[Other] rare_kept'],'0')
            for k,v in tools.items():self.assertEqual(result['[Automatic tool changer (ATC)] '+k],v)
            self.assertIn('[Feedrate] '+CUT,cols);self.assertIn('[Table] '+TABLE,cols)
            self.assertEqual(len(json.loads((root/'HIDDEN_PARAMETERS_ARCHIVE.json').read_text(encoding='utf-8'))),8)
