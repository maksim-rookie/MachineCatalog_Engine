"""MachineCatalog Engine v4.8.5.4: source-grounded export checks."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from catalog_engine import PROJECT_NAME, PROJECT_VERSION
from catalog_engine.models import parse_header
from catalog_engine.schema import Schema
from catalog_engine.extract import extract_pdf
from catalog_engine.quality import check_document
from catalog_engine.normalization import canonical_scalar, projection
from catalog_engine.exports import table_cell_coverage, control_mentions, write_outputs

ROOT=Path(__file__).resolve().parents[1]


def source_fact(**kw):
    d=dict(fact_id='f1',source_file='example.pdf',source_page=8,
           model='AB-1000',configuration='base',category='Machine',
           parameter='machine_weight_kg',unit_source='t',unit_expected='kg',
           value='36',base_value='36',availability='present',warnings=[],
           condition_data={},source_label='机床重量',human_verified=False)
    d.update(kw)
    return d


class CoreTests(unittest.TestCase):
    def test_version_and_name(self):
        self.assertEqual(PROJECT_NAME,'MachineCatalog Engine')
        self.assertEqual(PROJECT_VERSION,'4.8.5.4')

    def test_machine_weight_tonnes_to_kg(self):
        self.assertEqual(canonical_scalar(source_fact()),('36000',1000.0))

    def test_table_drive_watts_to_kw(self):
        item=source_fact(parameter='table_drive_power_kw',unit_source='w',unit_expected='kW',value='1400',base_value='1400')
        self.assertEqual(canonical_scalar(item),('1.4',.001))

    def test_unsafe_unit_conflict_is_not_normalized(self):
        x=source_fact(unit_source='kW',warnings=['source_unit_conflict'])
        self.assertIsNone(canonical_scalar(x))

    def test_compound_weight_preserves_source(self):
        x=source_fact(value='36/50',base_value='36/50')
        self.assertIsNone(canonical_scalar(x))

    def test_xyz_rapid_traverse_components(self):
        x=source_fact(parameter='rapid_traverse_xyz_m_min',value='10/20/20',category='Feedrate')
        self.assertEqual([(r['parameter'],r['value']) for r in projection(x)],
                         [('rapid_traverse_x_m_min','10'),('rapid_traverse_y_m_min','20'),('rapid_traverse_z_m_min','20')])

    def test_axis_power_components(self):
        x=source_fact(parameter='axis_motor_power_xyz_kw',value='3/3/4.4')
        self.assertEqual([r['value'] for r in projection(x)],['3','3','4.4'])

    def test_accuracy_components_respect_unit(self):
        x=source_fact(parameter='positioning_accuracy',value='0.01/0.02/0.03',unit_source='mm',
                      condition_data={'axes':'X/Y/Z'})
        self.assertEqual([x['parameter'] for x in projection(x)],
                         ['positioning_accuracy_x_mm','positioning_accuracy_y_mm','positioning_accuracy_z_mm'])

    def test_dimension_order_not_guessed(self):
        x=source_fact(parameter='machine_dimensions_mm',value='8300×5700×5950')
        names=[r['parameter'] for r in projection(x)]
        self.assertEqual(names,['machine_dimension_1_mm','machine_dimension_2_mm','machine_dimension_3_mm'])
        self.assertNotIn('machine_length_mm',names)

    def test_power_supply_pair(self):
        x=source_fact(parameter='power_supply_specification',value='380V/100kVA')
        self.assertEqual([(r['parameter'],r['value']) for r in projection(x)],
                         [('power_supply_voltage_v','380'),('power_supply_kva','100')])

    def test_tool_full_empty_diameters(self):
        x=source_fact(parameter='tool_max_diameter_mm',value='φ110/φ220',
                      source_label='最大刀具直径（满刀 / 空位）')
        self.assertEqual([r['value'] for r in projection(x)],['110','220'])

    def test_t_slot_triple(self):
        x=source_fact(parameter='table_t_slot',value='22-200-9')
        self.assertEqual([r['value'] for r in projection(x)],['22','200','9'])

    def test_bad_triplet_not_projected(self):
        x=source_fact(parameter='rapid_traverse_xyz_m_min',value='10/20')
        self.assertEqual(projection(x),[])

    def test_no_components_for_not_applicable(self):
        x=source_fact(parameter='rapid_traverse_xyz_m_min',value='10/20/20',availability='not_applicable')
        self.assertEqual(projection(x),[])

    def test_kaibo_manufacturer_config(self):
        s=Schema()
        self.assertEqual(s.identify_manufacturer([{'text':'NINGBO KAIBO CNC MACHNERY CO., LTD.'}]),'KAIBO CNC')
        self.assertEqual(s.identify_manufacturer([{'text':'DN SOLUTIONS HORIZONTAL BORING MACHINE'}]),'DN Solutions')

    def test_unknown_manufacturer_not_fabricated(self):
        self.assertEqual(Schema().identify_manufacturer([{'text':'Machining center'}]),'')

    def test_kaibo_chinese_header(self):
        s=Schema();self.assertTrue(s.header_is('项目','description'));self.assertTrue(s.header_is('单位','unit'))
        self.assertEqual(s.match(['工作台','最大工作负重'],'kg')['key'],'table_load_kg')

    def test_boring_schema_w_and_quill(self):
        s=Schema()
        self.assertEqual(s.match(['Travels','Travel distance','W-axis'],'mm (inch)')['key'],'travel_w_mm')
        self.assertEqual(s.match(['Spindle','Quill diameter'],'mm (inch)')['key'],'quill_diameter_mm')

    def test_chinese_model_suffix_kept(self):
        refs=parse_header('GV-3020-5H')
        self.assertEqual([r.model for r in refs],['GV-3020-5H'])
        self.assertEqual([r.model for r in parse_header('DC600-4500-2Z')],['DC600-4500-2Z'])

    def test_new_machine_class_from_json_without_parser_code(self):
        from catalog_engine.identity import table_identity
        from catalog_engine.geometry import LogicalTable, LogicalRow, Cell
        with tempfile.TemporaryDirectory() as folder:
            override=Path(folder)/'overrides.json'
            override.write_text(json.dumps({'machine_classes':[{
                'key':'grinding','machine_type':'Surface Grinding Machine',
                'min_score':4,'features':[{'scope':'cover','pattern':'surface grinding machine','weight':7}]}],
                'required_fields':{'grinding':['travel_x_mm']},
                'rules':[{'key':'grinding_wheel_diameter_mm','pattern':'grinding wheel diameter',
                          'unit':'mm','category':'Grinding'}]}),encoding='utf8')
            schema=Schema(ROOT/'config/schema.json',override)
            rows=[LogicalRow(i,(200,110+i*25,500,130+i*25),[label],'mm',[]) for i,label in enumerate(
                ['X-axis','Y-axis','Z-axis','Table size'])]
            table=LogicalTable(1,(200,90,500,260),[Cell((300,90,400,110),'ZX-900')],rows,'test')
            class Page:
                def get_text(self,kind):
                    return {'blocks':[{'lines':[
                        {'bbox':(100,30,390,55),'spans':[{'text':'Surface Grinding Machine','size':22}]},
                        {'bbox':(100,65,240,75),'spans':[{'text':'Specification','size':8}]},
                    ]}]}
            kind,typ,evidence=table_identity(Page(),table,schema)
            self.assertEqual((kind,typ),('grinding','Surface Grinding Machine'))
            self.assertEqual(schema.required('grinding'),['travel_x_mm'])

    def test_cli_version(self):
        res=subprocess.run([sys.executable,str(ROOT/'main.py'),'--version'],capture_output=True,text=True)
        self.assertEqual(res.returncode,0)
        self.assertIn('4.8.5.4',res.stdout)


class PDFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc={}
        for name,key,env in [('凯博数控电子样本2025.pdf','kaibo','MCE_TEST_KAIBO'),
                             ('dn-solutions-boring-machines-dbc-series.pdf','dbc','MCE_TEST_DBC')]:
            candidate=os.getenv(env,'')
            file=Path(candidate) if candidate else ROOT/'pdf'/name
            if file.is_file():cls.doc[key]=extract_pdf(file,Schema())

    def required_doc(self,key):
        if key not in self.doc:self.skipTest('Optional integration PDF not supplied: '+key)
        return self.doc[key]

    def test_kaibo_38_models(self):
        d=self.required_doc('kaibo')
        self.assertEqual(len(d['registry']),38)
        self.assertIn('GV-3020-5H',d['registry'])
        self.assertIn('DC600-4500-2Z',d['registry'])

    def test_kaibo_cells_captured_and_raw_intact(self):
        d=self.required_doc('kaibo')
        cov=table_cell_coverage([d]);self.assertEqual(sum(r['source_cells_nonempty'] for r in cov),1059)
        self.assertEqual(sum(r['source_cells_not_extracted'] for r in cov),0)
        self.assertEqual(sum(r['source_cells_blank_in_pdf'] for r in cov),5)

    def test_kaibo_source_blanks_are_not_cloned(self):
        d=self.required_doc('kaibo')
        self.assertFalse([f for f in d['facts'] if f['model']=='DC-6040A' and f['parameter']=='tool_capacity'])
        self.assertTrue([f for f in d['facts'] if f['model']=='DC-6040D' and f['parameter']=='tool_capacity'])

    def test_kaibo_machine_type_scoped_per_page(self):
        d=self.required_doc('kaibo')
        self.assertEqual(d['registry']['KWJ-630']['machine_type'],'Horizontal Machining Center')
        self.assertEqual(d['registry']['GV-3020-5H']['machine_type'],'5-Axis Machining Center')

    def test_kaibo_mentions_are_unassigned(self):
        d=self.required_doc('kaibo')
        mentions=control_mentions([d]);self.assertGreaterEqual(len(mentions),1)
        self.assertTrue(all(x['automatic_model_assignment'] is False for x in mentions))

    def test_kaibo_canonical_values(self):
        d=self.required_doc('kaibo')
        facts=d['facts']
        for model,key,expected in [('GV-3020-5H','machine_weight_kg','36000'),
                                   ('KWJ-630','table_drive_power_kw','1.4')]:
            item=next(f for f in facts if f['model']==model and f['parameter']==key)
            self.assertEqual(canonical_scalar(item)[0],expected)

    def test_dbc_eight_models_and_cnc(self):
        d=self.required_doc('dbc')
        self.assertEqual(len(d['registry']),8)
        self.assertEqual(len(d['unmapped_fields']),0)
        self.assertTrue(all(m['machine_class']=='boring' for m in d['registry'].values()))
        for model in d['registry']:
            self.assertEqual(len([f for f in d['facts'] if f['model']==model and f['parameter']=='control_system']),1,model)

    def test_dbc_explicit_power_supply_exception(self):
        d=self.required_doc('dbc')
        values=lambda model:[f['value'] for f in d['facts'] if f['model']==model and f['parameter']=='power_supply_kva']
        self.assertEqual(values('DBC 110S'),['70kVA'])
        self.assertEqual(values('DBC 130S'),['75kVA'])

    def test_dbc_full_fact_coverage(self):
        d=self.required_doc('dbc')
        self.assertEqual(len(d['facts']),262)
        cov=table_cell_coverage([d])
        self.assertEqual(sum(r['source_cells_not_extracted'] for r in cov),0)


if __name__=='__main__':unittest.main()
