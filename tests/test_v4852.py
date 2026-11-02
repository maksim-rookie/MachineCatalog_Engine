"""v4.8.5.3: family isolation, field groups, multi-vendor aliases, evidence."""
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

from catalog_engine.classification import document_identity
from catalog_engine.families import resolve_family
from catalog_engine.models import parse_header
from catalog_engine.report_layout import ordered_columns,aggregate_families
from catalog_engine.review import apply_corrections, load_corrections
from catalog_engine.schema import Schema
from catalog_engine.extract import extract_pdf

ROOT=Path(__file__).resolve().parents[1]

class GroupingAndIdentityTests(unittest.TestCase):
    def setUp(self):self.schema=Schema()

    def test_dhf_family_from_rule(self):
        self.assertEqual(resolve_family('DHF 8000',self.schema)[:2],('DHF 8000','standard'))
        self.assertEqual(resolve_family('DHF 8000ST',self.schema)[:2],('DHF 8000','ST'))
        self.assertEqual(parse_header('DHF 8000 30000r/min / PSC')[0].configuration,'30000rpm / PSC')

    def test_no_unsafe_merging_of_other_models(self):
        self.assertEqual(resolve_family('DVF 8000T',self.schema)[0],'DVF 8000T')
        self.assertEqual(resolve_family('DVF 8000T/50',self.schema)[0],'DVF 8000T/50')
        self.assertNotEqual(resolve_family('LYNX 2100A',self.schema)[0],resolve_family('LYNX 2100LA',self.schema)[0])

    def test_custom_family_rule_from_json(self):
        with tempfile.TemporaryDirectory() as p:
            file=Path(p)/'config.json'
            file.write_text(json.dumps({'model_family_rules':[{'pattern':r'^(?P<base>ABC 1000)(?P<variant>ST)?$',
                       'base_model':'{base}','model_variant':'{variant}'}]}),encoding='utf8')
            s=Schema(overrides=file)
            self.assertEqual(resolve_family('ABC 1000ST',s)[:2],('ABC 1000','ST'))

    def test_order_of_parameter_groups(self):
        raw=['[Control] control_system','[Spindle] spindle_power_kw',
             '[Automatic tool changer (ATC)] tool_capacity','[Feedrate] rapid_traverse_x_m_min',
             '[Machine dimensions] machine_weight_kg','[Table] table_size_mm','[Travel] travel_x_mm']
        sorted_cols=ordered_columns(raw,self.schema)
        self.assertEqual([x.split('] ')[0][1:] for x in sorted_cols],self.schema.config['parameter_groups'][:7])

    def test_axis_order_xyz_not_alpha(self):
        cols=['[Travel] travel_z_mm','[Travel] travel_y_mm','[Travel] travel_x_mm']
        self.assertEqual(ordered_columns(cols,self.schema),cols[::-1])

    def test_synonyms_different_vendors_same_key(self):
        s=self.schema
        en=s.match(['Travel','Travel distance','X-axis'],'mm (inch)')
        zh=s.match(['加工范围','X 轴行程'],'mm')
        self.assertEqual(en['key'],zh['key'])
        self.assertEqual(en['category'],zh['category'])
        self.assertEqual(en['category'],'Travel')
        self.assertEqual(s.match(['Spindle','主轴功率'],'kW')['key'],s.match(['Spindle','Max. spindle motor power'],'kW (Hp)')['key'])

    def test_cnc_synonyms(self):
        for label in ['NC system','CNC system','Control system','控制系统']:
            result=self.schema.match(['Control',label],'')
            self.assertEqual(result['key'],'control_system',label)

    def test_units_do_not_confuse_chinese_cutting_feed(self):
        x=self.schema.match(['驱动进给','X/Y/Z 最大切削速度 G01'],'m/min')
        self.assertEqual((x['key'],x['unit']),('cutting_feed_xyz_m_min','m/min'))

    def test_unknown_alias_kept_unmapped(self):
        self.assertIsNone(self.schema.match(['Travel','Genuinely unknown parameter'],'mm'))

    def test_cover_based_hdhf_and_mixed_not_global(self):
        self.assertEqual(document_identity([{'text':'DHF SIMULTANEOUS 5 AXIS HORIZONTAL MACHINING CENTER'}],self.schema),
                         ('machining','Horizontal 5-Axis Machining Center','explicit_document_cover'))
        self.assertIsNone(document_identity([{'text':'KAIBO CNC COMPANY PROFILE'}],self.schema))

    def test_family_aggregation_tags_differences(self):
        keys=['source_file','model','base_model','model_variant','generation','machine_class','machine_type']
        common={'source_file':'x.pdf','base_model':'DHF 8000','generation':'',
                'machine_class':'machining','machine_type':'Horizontal 5-Axis Machining Center'}
        rows=[{**common,'model':'DHF 8000','model_variant':'standard','configuration':'base',
               '[Automatic tool changer (ATC)] tool_capacity':'60','[Travel] travel_x_mm':'1450'},
              {**common,'model':'DHF 8000ST','model_variant':'ST','configuration':'base',
               '[Automatic tool changer (ATC)] tool_capacity':'60','[Travel] travel_x_mm':'1450'},
              {**common,'model':'DHF 8000','model_variant':'standard','configuration':'30000rpm / PSC',
               '[Automatic tool changer (ATC)] tool_capacity':'80','[Travel] travel_x_mm':'1450'}]
        summary,clean,meta=aggregate_families(rows,keys,['[Automatic tool changer (ATC)] tool_capacity','[Travel] travel_x_mm'])
        self.assertEqual(len(summary),1)
        self.assertEqual(summary[0]['model'],'DHF 8000')
        self.assertIn('60 [исполнения:',summary[0]['[Automatic tool changer (ATC)] tool_capacity'])
        self.assertIn('80 [исполнения:',summary[0]['[Automatic tool changer (ATC)] tool_capacity'])
        self.assertEqual(clean[0]['[Automatic tool changer (ATC)] tool_capacity'],'')
        self.assertEqual(summary[0]['[Travel] travel_x_mm'],'1450')

    def test_manual_review_requires_provenance_and_preserves_original(self):
        fact={'fact_id':'id1','model':'DHF 8000','parameter':'table_load_kg','value':'1000','base_value':'1000',
              'value_raw':'1000 (2204.6)','source_quote':'table load | kg | 1000 (2204.6)',
              'cell_bbox':[10,20,30,40],'unit_source':'kg','warnings':[],'transformations':[]}
        doc={'source_file':'x.pdf','source_sha256':'sha123','facts':[fact], 'review':[]}
        bad=[{'source_file':'x.pdf','source_sha256':'WRONG','fact_id':'id1','corrected_value':'1200','reviewer':'Ada','reason':'Proof'}]
        self.assertEqual(apply_corrections(doc,bad)[0]['status'],'NOT_APPLIED')
        self.assertEqual(fact['value'],'1000')
        audit=apply_corrections(doc,[{**bad[0],'source_sha256':'sha123'}])
        self.assertEqual(audit[0]['status'],'APPLIED')
        self.assertEqual(fact['value'],'1200')
        self.assertEqual(fact['value_extracted'],'1000')
        self.assertEqual(fact['value_raw'],'1000 (2204.6)')
        self.assertEqual(fact['source_quote'],'table load | kg | 1000 (2204.6)')
        self.assertTrue(fact['human_verified'])
        self.assertEqual(fact['fact_id'],'id1')


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs={}
        for key,env in [('dhf','MCE_TEST_DHF'),('kaibo','MCE_TEST_KAIBO'),('dbc','MCE_TEST_DBC')]:
            path=os.environ.get(env)
            if path and Path(path).is_file():cls.docs[key]=extract_pdf(Path(path),Schema())

    def require(self,key):
        if key not in self.docs:self.skipTest('Optional integration PDF missing: '+key)
        return self.docs[key]

    def test_dhf_all_87_source_facts_retained(self):
        d=self.require('dhf')
        self.assertEqual(len(d['facts']),87)
        self.assertEqual(len(d['registry']),2)
        self.assertEqual({m['base_model'] for m in d['registry'].values()},{'DHF 8000'})
        self.assertEqual(set(d['registry']['DHF 8000']['configurations']),{'base','30000rpm / PSC'})

    def test_dhf_special_values_not_merged(self):
        facts=self.require('dhf')['facts']
        def v(model,config,key):
            return [x['value'] for x in facts if x['model']==model and x['configuration']==config and x['parameter']==key]
        self.assertEqual(v('DHF 8000','base','tool_capacity')[0].split()[0],'60')
        self.assertEqual(v('DHF 8000','30000rpm / PSC','tool_capacity')[0].split()[0],'80')
        self.assertTrue(v('DHF 8000ST','base','pallet_size_mm')[0].startswith('ø1000'))
        self.assertTrue(v('DHF 8000','base','pallet_size_mm')[0].startswith('800'))

    def test_dhf_machine_type(self):
        d=self.require('dhf')
        self.assertEqual({m['machine_type'] for m in d['registry'].values()},{'Horizontal 5-Axis Machining Center'})

    def test_kaibo_preserves_source_labels_and_38_models(self):
        d=self.require('kaibo')
        self.assertEqual(len(d['registry']),38)
        self.assertEqual(len(d['facts']),1059)
        item=next(f for f in d['facts'] if f['parameter']=='travel_x_mm')
        self.assertTrue(item.get('source_label'))
        self.assertEqual(item['parameter_normalized'],'travel_x_mm')

    def test_dbc_same_boring_type_and_no_split_regressions(self):
        d=self.require('dbc')
        self.assertEqual(len(d['registry']),8)
        self.assertEqual(len(d['facts']),262)
        self.assertEqual({m['machine_class'] for m in d['registry'].values()},{'boring'})

if __name__=='__main__':unittest.main()
