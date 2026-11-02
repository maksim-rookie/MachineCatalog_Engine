"""Regression of the uploaded Kaibo run: wide columns, units and external data."""
import copy
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from catalog_engine.extract import extract_pdf
from catalog_engine.exports import write_outputs,display_value
from catalog_engine.normalization import enrich_document
from catalog_engine.paths import data_paths,PROJECT_ROOT,DATA_ENV
from catalog_engine.quality import check_document
from catalog_engine.schema import Schema


def fact(key='machine_weight_kg',category='Machine',value='35',unit='t',configuration='base'):
    return {'fact_id':key+configuration,'source_file':'sample.pdf','model':'AA-1000','generation':'',
            'configuration':configuration,'parameter':key,'category':category,'value':value,'value_raw':value,
            'base_value':value,'unit_expected':'kg','unit_source':unit,'availability':'present','options':[],
            'condition':'','condition_data':{},'warnings':[],'source_page':1,'source_label':'',
            'cell_bbox':[10,20,30,40],'transformations':[],'human_verified':False}


def document(facts,configurations=None):
    return {'source_file':'sample.pdf','facts':facts,'registry':{'AA-1000':{'configurations':configurations or ['base']}},
            'review':[],'raw_tables':[],'source_pages':[],'unmapped_fields':[],'cell_statuses':[]}


def export(doc,schema=None):
    with tempfile.TemporaryDirectory() as td:
        p=Path(td);write_outputs(p,[doc],schema or Schema(),[])
        result={}
        for name in ['ALL_MODELS_wide','ALL_MODELS_wide_values','ALL_CONFIGURATIONS_wide','WIDE_EXPORT_AUDIT']:
            with (p/(name+'.csv')).open(encoding='utf-8-sig') as f:
                reader=csv.DictReader(f);result[name]=(reader.fieldnames,list(reader))
        result['audit']=json.loads((p/'WIDE_EXPORT_AUDIT.json').read_text())
        return result


class TestWide8451(unittest.TestCase):
    def test_language_categories_do_not_duplicate_parameter_columns(self):
        first=fact(key='travel_x_mm',category='Capacity',value='3000',unit='mm');first['unit_expected']='mm'
        second=copy.deepcopy(first);second.update(fact_id='second',category='Travel',value='4000',configuration='fast')
        cols,rows=export(document([first,second],['base','fast']))['ALL_MODELS_wide']
        self.assertEqual([c for c in cols if c.endswith('] travel_x_mm')],['[Travel] travel_x_mm'])
        self.assertIn('3000',rows[0]['[Travel] travel_x_mm']);self.assertIn('4000',rows[0]['[Travel] travel_x_mm'])
        self.assertIn('configuration: fast',rows[0]['[Travel] travel_x_mm'])
    def test_non_base_only_model_is_present_in_model_view(self):
        result=export(document([fact(configuration='30000rpm')],['30000rpm']))
        rows=result['ALL_MODELS_wide'][1];self.assertEqual(len(rows),1)
        self.assertIn('35000',rows[0]['[Machine dimensions] machine_weight_kg'])
    def test_not_applicable_configuration_is_retained_beside_present_value(self):
        missing=fact(value='-',configuration='alternate');missing['availability']='not_applicable'
        result=export(document([fact(),missing],['base','alternate']))
        value=result['ALL_MODELS_wide'][1][0]['[Machine dimensions] machine_weight_kg']
        self.assertIn('35000',value);self.assertIn('НЕ ПРИМЕНИМО',value);self.assertIn('configuration: alternate',value)
    def test_wide_mass_is_in_kilograms_with_original_tonnes_visible(self):
        result=export(document([fact()]))
        self.assertEqual(result['ALL_MODELS_wide'][1][0]['[Machine dimensions] machine_weight_kg'],'35000 [исходник: 35 t]')
        self.assertEqual(result['ALL_MODELS_wide_values'][1][0]['[Machine dimensions] machine_weight_kg'],'35000')
    def test_audit_explicitly_reports_intentional_hiding(self):
        schema=Schema();schema.config['hidden_wide_parameters']=['machine_weight_kg']
        result=export(document([fact()]),schema)
        self.assertEqual(result['audit']['facts_hidden_by_config'],1)
        self.assertEqual(result['WIDE_EXPORT_AUDIT'][1][0]['export_status'],'HIDDEN_BY_CONFIG')
    def test_default_wide_schema_hides_nothing(self):
        self.assertEqual(Schema().config['hidden_wide_parameters'],[])
    def test_rotary_and_linear_accuracy_units_are_visible(self):
        f=fact(key='positioning_accuracy',value='8/8',unit='″');f['unit_expected']=''
        self.assertIn('ед.: ″',display_value([f]))
    def test_optional_only_stays_out_of_clean_values(self):
        f=fact();f.update(availability='optional_only',base_value='')
        self.assertIn('ОПЦИЯ',display_value([f]));self.assertEqual(display_value([f],clean=True),'')


class TestNormalization8451(unittest.TestCase):
    def test_axis_components_follow_printed_order_and_keep_parent(self):
        f=fact(key='rapid_traverse_xyz_m_min',value='20/30/40',unit='m/min')
        f.update(unit_expected='m/min',source_label='X/Y/Z 快速移动速度 G00')
        doc=enrich_document(document([f]),Schema())
        components={f['parameter']:f for f in doc['facts'] if f.get('parent_fact_id')}
        self.assertEqual({k:v['value'] for k,v in components.items()},
                         {'rapid_traverse_x_m_min':'20','rapid_traverse_y_m_min':'30','rapid_traverse_z_m_min':'40'})
        for f in components.values():self.assertEqual(f['value_raw'],'20/30/40');self.assertTrue(f['parent_fact_id'])
        before=len(doc['facts']);enrich_document(doc,Schema());self.assertEqual(len(doc['facts']),before)
    def test_missing_or_unlabelled_axis_values_are_not_guessed(self):
        for label,value in [('X/Y/Z 快速移动速度','20/30'),('快速移动速度','20/30/40')]:
            f=fact(key='rapid_traverse_xyz_m_min',value=value,unit='m/min');f['source_label']=label
            self.assertEqual(len(enrich_document(document([f]),Schema())['facts']),1)
    def test_embedded_voltage_and_power_are_typed_independently(self):
        f=fact(key='power_supply_specification',value='380V/100kVA',unit='');f['unit_expected']=''
        result=enrich_document(document([f]),Schema())
        components={f['parameter']:f for f in result['facts'] if f.get('parent_fact_id')}
        self.assertEqual(components['power_supply_kva']['value'],'100')
        self.assertEqual(components['power_supply_kva']['unit_source'],'kVA')
        self.assertEqual(components['power_supply_voltage_v']['value'],'380')
    def test_machine_dimension_order_is_not_invented(self):
        f=fact(key='machine_dimensions_mm',value='6700×5700×5400',unit='mm');f['source_label']='外形尺寸'
        doc=enrich_document(document([f]),Schema())
        self.assertEqual(len(doc['facts']),1)


class TestExternalPaths8451(unittest.TestCase):
    def test_defaults_are_outside_project(self):
        with patch.dict(os.environ,{},clear=True):
            paths=data_paths()
            self.assertEqual(paths.root,PROJECT_ROOT.parent/'MachineCatalog_Engine_data')
            self.assertNotIn(PROJECT_ROOT,paths.root.parents)
            self.assertEqual(paths.runs.name,'v8_4_5_1')
    def test_environment_override(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{DATA_ENV:td}):
            self.assertEqual(data_paths().input,Path(td)/'input')
    def test_data_inside_source_tree_is_rejected(self):
        with self.assertRaises(ValueError):data_paths(PROJECT_ROOT/'data')
    def test_init_command_creates_external_layout(self):
        with tempfile.TemporaryDirectory() as td:
            result=subprocess.run([sys.executable,str(PROJECT_ROOT/'main.py'),'--data-dir',td,'--init-data'],
                                  capture_output=True,text=True,cwd=td)
            self.assertEqual(result.returncode,0,result.stderr)
            for folder in ['input','runs/v8_4_5_1','cache/v8_4_5_1']:self.assertTrue((Path(td)/folder).is_dir())
        self.assertFalse((PROJECT_ROOT/'pdf').exists());self.assertFalse((PROJECT_ROOT/'output').exists())


class TestKaiboWide8451(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pdf=data_paths().input/'凯博数控电子样本2025.pdf'
        if not pdf.exists():raise unittest.SkipTest('External Kaibo fixture not available')
        cls.schema=Schema();cls.doc=extract_pdf(pdf)
        cls.result=export(cls.doc,cls.schema)
        cls.rows={r['model']:r for r in cls.result['ALL_MODELS_wide'][1]}
    def test_each_parameter_has_exactly_one_wide_column(self):
        columns=[c.split('] ',1)[1] for c in self.result['ALL_MODELS_wide'][0] if '] ' in c]
        self.assertEqual(len(columns),len(set(columns)))
    def test_every_source_fact_and_component_is_audited(self):
        audit=self.result['audit'];self.assertEqual(audit['source_facts'],1059)
        self.assertEqual(audit['facts_exported'],len(self.doc['facts']))
        self.assertEqual(audit['facts_hidden_by_config'],0)
        self.assertEqual(len(self.rows),38)
    def test_all_190_original_cells_survive_wide_projection(self):
        with (PROJECT_ROOT/'benchmarks/expected_kaibo_845.csv').open(encoding='utf-8-sig') as f:
            cases=list(csv.DictReader(f))
        columns=self.result['ALL_MODELS_wide'][0]
        for case in cases:
            with self.subTest(model=case['model'],parameter=case['parameter']):
                col=next(c for c in columns if c.endswith('] '+case['parameter']))
                expected=case['expected']
                if case['parameter']=='machine_weight_kg':expected=format(float(expected)*1000,'.12g')
                self.assertTrue(self.rows[case['model']][col].startswith(expected),self.rows[case['model']][col])
    def test_missing_kaibo_columns_now_have_explicit_axis_and_power_values(self):
        row=self.rows['BM-3022-5H']
        for axis in 'xyz':self.assertIn('20',row[f'[Feedrate] rapid_traverse_{axis}_m_min'])
        self.assertEqual(row['[Power source] power_supply_kva'],'100')
        self.assertEqual(row['[Power source] power_supply_voltage_v'],'380')
        self.assertEqual(row['manufacturer'],'Ningbo Kaibo CNC Machinery Co., Ltd.')
        self.assertNotIn('[Capacity] travel_x_mm',row)
    def test_unreported_controller_is_not_fabricated(self):
        self.assertIn('НЕ ИЗВЛЕЧЕНО',self.rows['BM-3022-5H'].get('[Control] control_system','НЕ ИЗВЛЕЧЕНО'))
