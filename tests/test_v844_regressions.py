"""8.4.4: semantic/model/units regressions verified against source tables.

Fixtures contain only word coordinates from the user-provided 8.4.3 run;
no PDF path, page-specific engine rule, or guessed machine specification.
"""
import csv
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from catalog_engine.models import parse_header, select_square_variant, footnote_rules, select_footnote
from catalog_engine.schema import Schema
from catalog_engine.values import split_embedded_conditions
from catalog_engine.geometry import Cell, LogicalRow, LogicalTable, recover_trailing_rows
from catalog_engine.quality import check_document, BLOCKING_WARNINGS
from catalog_engine.exports import typed_number, write_typed_measurements

ROOT=Path(__file__).resolve().parents[1]

class TestV844Models(unittest.TestCase):
    def test_dvf_t_spindle_interface_stays_distinct(self):
        headers=['DVF 6500','DVF 6500 T','DVF 8000','DVF 8000T','DVF 8000/50','DVF 8000T/50']
        names=[parse_header(h)[0].model for h in headers]
        self.assertEqual(len(names),len(set(names)))
        self.assertEqual(names[-1],'DVF 8000T/50')
        self.assertNotEqual(names[-1],names[3])
        self.assertNotEqual(names[-1],names[4])
    def test_newline_before_interface_suffix(self):
        self.assertEqual(parse_header('DVF\n8000T/50')[0].model,'DVF 8000T/50')
    def test_mynx_numeral_and_interface_kept(self):
        self.assertEqual(parse_header('Mynx 5400/50\u2161')[0].model,'MYNX 5400/50II')
    def test_dhf_subtype_not_extra_rpm_model(self):
        self.assertEqual([x.model for x in parse_header('DHF 8000/8000ST')],['DHF 8000','DHF 8000ST'])
    def test_lynx_long_bed_selected_not_just_in_raw_text(self):
        refs=parse_header('Lynx\n2100A\n[NT]/LA')
        self.assertEqual([(r.model,r.square_variant) for r in refs],[('LYNX 2100A','short'),('LYNX 2100LA','long')])
        raw='330 [550] (13.0 [21.7])'
        self.assertEqual(select_square_variant(raw,refs[0].square_variant,True)[0],'330 (13.0)')
        self.assertEqual(select_square_variant(raw,refs[1].square_variant,True)[0],'550 (21.7)')
    def test_dhf_pallet_footnote_selected(self):
        d=footnote_rules('** Only DHF 8000ST')
        value='800 x 800 (31.5 x 31.5), ø1000 (ø39.4)**'
        self.assertEqual(select_footnote(value,'DHF 8000',d)[0],'800 x 800 (31.5 x 31.5)')
        self.assertEqual(select_footnote(value,'DHF 8000ST',d)[0],'ø1000 (ø39.4)')

class TestV844Schema(unittest.TestCase):
    def setUp(self): self.schema=Schema()
    def test_nhp_kg_not_a_count_of_tools(self):
        rule=self.schema.match(['Table','Tool storage capacity'],'kg (lb)')
        self.assertEqual(rule['key'],'table_load_kg')
        self.assertIn('disagreement',rule['_schema_warning'])
        self.assertIn(rule['_schema_warning'],BLOCKING_WARNINGS)
    def test_regular_table_load_is_not_flagged_as_a_misprint(self):
        rule=self.schema.match(['Table','Table loading capacity'],'kg (lb)')
        self.assertEqual(rule['key'],'table_load_kg')
        self.assertNotIn('_schema_warning',rule)
    def test_real_atc_tools_still_counted(self):
        rule=self.schema.match(['Automatic tool changer (ATC)','Tool storage capa.'],'ea')
        self.assertEqual(rule['key'],'tool_capacity')
    def test_pull_stud_leaf_wins_over_parent_tool_shank(self):
        self.assertEqual(self.schema.match(['Automatic tool changer (ATC)','Type of tool shank','Pull stud'],'-')['key'],'tool_pull_stud')
        self.assertEqual(self.schema.match(['Automatic tool changer (ATC)','Type of tool shank','Tool shank'],'-')['key'],'tool_shank_type')
    def test_misspelled_control_section(self):
        self.assertEqual(self.schema.match(['Contrel','NC system'],'-')['key'],'control_system')
    def test_mynx_wrong_source_unit_not_silently_corrected(self):
        from catalog_engine.values import value_warnings
        warnings=value_warnings('1000 (1341.0)','kg','kW (Hp)','table_load_kg')
        self.assertIn('source_unit_conflict',warnings)
        self.assertIn('source_unit_conflict',BLOCKING_WARNINGS)
    def test_formatted_variants_of_technical_labels(self):
        examples=[(['Automatic tool changer','Max. tool dia.','Continous'],'mm (inch)','tool_max_diameter_mm'),
                  (['Automatic tool changer','Tool to Tool'],'sec','tool_change_time_sec'),
                  (['Tank capacity','Coolant tank'],'L (gal)','coolant_tank_l'),
                  (['Motor','Coolant pump motor power'],'kW (Hp)','coolant_pump_motor_power_kw')]
        for labels,unit,key in examples:
            with self.subTest(labels=labels):
                self.assertEqual(self.schema.match(labels,unit)['key'],key)
    def test_reject_unsupported_row(self):
        self.assertIsNone(self.schema.match(['Drawing','Sticker'] ,'ea'))

class FakePage:
    def __init__(self,fixture):
        self._words=[tuple(w['bbox'])+(w['text'],0,0,0) for w in fixture['word_rows']]
        self.rect=SimpleNamespace(height=fixture['page_height'],width=fixture['page_width'])
    def get_text(self,kind,sort=False):
        if kind=='words':return self._words
        return ''
    def get_drawings(self): return []

class TestV844Geometry(unittest.TestCase):
    def test_last_row_recovered_without_guessing_data(self):
        fixture=json.loads((ROOT/'tests'/'fixtures'/'nhp_page17_tail_geometry.json').read_text())
        headers=[Cell(tuple(h['bbox']),h['text']) for h in fixture['headers']]
        r=fixture['last_row'];row=LogicalRow(r['row_index'],tuple(r['bbox']),r['labels'],r['unit'],
            [Cell(tuple(c['bbox']),c['text']) for c in r['values']])
        t=LogicalTable(17,(28.346,136.313,566.929,row.bbox[3]),headers,[row],'native_vector_cells')
        self.assertEqual(recover_trailing_rows(FakePage(fixture),t,Schema()),1)
        self.assertEqual(len(t.rows),2)
        self.assertEqual(t.rows[1].labels,['Control','NC system'])
        self.assertEqual(Schema().match(t.rows[1].labels,t.rows[1].unit)['key'],'control_system')
        self.assertEqual(len(t.rows[1].values),1)
        self.assertIn('Fanuc 31i Plus',t.rows[1].values[0].text)
        self.assertEqual(t.rows[1].recovery,'below_geometric_table_edge')
        self.assertEqual(recover_trailing_rows(FakePage(fixture),t,Schema()),0)
    def test_no_trailing_text_is_not_invented(self):
        f=json.loads((ROOT/'tests'/'fixtures'/'nhp_page17_tail_geometry.json').read_text())
        f['word_rows']=[]
        headers=[Cell(tuple(h['bbox']),h['text']) for h in f['headers']]
        t=LogicalTable(17,(28.346,136.313,566.929,763.337),headers,
          [LogicalRow(30,(28,744,566.929,763.337),['Machine dimensions','Weight'],'kg (lb)',[])],
          'native_vector_cells')
        self.assertEqual(recover_trailing_rows(FakePage(f),t,Schema()),0)

class TestV844Typed(unittest.TestCase):
    def test_only_unambiguous_scalar_has_numeric_value(self):
        self.assertEqual(typed_number('1450 (57.1)','mm'),'1450')
        self.assertEqual(typed_number('3500','r/min'),'3500')
        self.assertEqual(typed_number('60 {90,120}','pcs'),'')
        self.assertEqual(typed_number('330 [550] (13.0 [21.7])','mm'),'')
        self.assertEqual(typed_number('800 x 800 (31.5 x 31.5)','mm'),'')
        self.assertEqual(typed_number('15/11','kW'),'')
    def test_multi_magazine_weights_split_with_conditions(self):
        raw='8000 (17636.7) ( 30, 40, 60T), 9000 (354.3) (120T)'
        split=split_embedded_conditions(raw,['Machine dimensions','Weight'],'machine_weight_kg')
        self.assertEqual([(x['condition']['magazine_tools'],x['value']) for x in split],
                         [(30,'8000 (17636.7)'),(40,'8000 (17636.7)'),
                          (60,'8000 (17636.7)'),(120,'9000 (354.3)')])
        from catalog_engine.values import value_warnings
        self.assertEqual(value_warnings(split[0]['value'],'kg','kg (lb)','machine_weight_kg'),[])
        self.assertIn('source_metric_imperial_mismatch',
                      value_warnings(split[-1]['value'],'kg','kg (lb)','machine_weight_kg'))
    def test_unknown_magazine_condition_not_guessed(self):
        raw='8000 (17636.7) (30,40T), irregular text (120T)'
        split=split_embedded_conditions(raw,['Machine dimensions','Weight'],'machine_weight_kg')
        self.assertEqual(len(split),1)
        self.assertEqual(split[0]['warning'],'magazine_configuration_unresolved')

    def test_blocking_unit_and_context_gates_numeric_export(self):
        facts=[{'fact_id':'1','source_file':'a.pdf','source_page':1,'model':'ABC 1234',
                'parameter':'table_load_kg','category':'Table','configuration':'base',
                'base_value':'400 (881.8)','value':'400 (881.8)',
                'unit_expected':'kg','unit_source':'kg (lb)',
                'warnings':['source_label_unit_context_disagreement'],'availability':'present'},
               {'fact_id':'2','source_file':'a.pdf','source_page':1,'model':'ABC 1234',
                'parameter':'travel_x_mm','category':'Travel','configuration':'base',
                'base_value':'300 (11.8)','value':'300 (11.8)',
                'unit_expected':'mm','warnings':[],'availability':'present'}]
        with tempfile.TemporaryDirectory() as td:
            write_typed_measurements(Path(td),facts)
            with (Path(td)/'MEASUREMENTS_TYPED.csv').open(encoding='utf-8-sig') as fp:
                rows=list(csv.DictReader(fp))
            self.assertEqual(rows[0]['typing_status'],'BLOCKED_REVIEW')
            self.assertEqual(rows[0]['numeric_value'],'')
            self.assertEqual(rows[1]['numeric_value'],'300')
            self.assertEqual(rows[1]['typing_status'],'AUTO_CHECKED_UNVERIFIED')

if __name__=='__main__': unittest.main()
