"""8.4.5: multilingual geometry, local identity and conservative numeric exports."""
from pathlib import Path
import contextlib
import csv
import io
import json
import shutil
import tempfile
import unittest
import pymupdf
from catalog_engine.extract import extract_pdf
from catalog_engine.exports import write_typed_measurements, display_value
from catalog_engine.geometry import extract_tables
from catalog_engine.models import parse_header
from catalog_engine.quality import check_document
from catalog_engine.schema import Schema, DEFAULT_CONFIG
from catalog_engine.storage import engine_fingerprint
from catalog_engine.values import range_bounds, value_warnings, unit_multiplier

ROOT=Path(__file__).resolve().parents[1]


def grid(page,x=30,y=90,heading='Vertical Machining Center',models=('AA-1000-5H','AA-1000-3H'),ruled=True,blank=False,turning=False):
    page.insert_text((x,y-30),heading,fontsize=15)
    xs=[x,x+170,x+230,x+330,x+430]
    rows=[['Description','Unit',*models],['Travel distance X-axis','mm','100','200'],
          ['Travel distance Y-axis','mm','110','210'],['Travel distance Z-axis','mm','120','220'],
          ['Table size','mm','100x200','200x300'],['Spindle speed','rpm','8000','9000'],
          ['Tool capacity','pcs','20','' if blank else '24'],['Machine weight','kg','1500','1600']]
    if turning:
        rows[2]=['Max turning diameter','mm','110','210']
        rows[4]=['Max turning length','mm','500','700']
    for n,row in enumerate(rows):
        for k,txt in enumerate(row):
            # Data and unit anchors are centred; labels are left aligned.
            xpos=xs[k]+4 if k==0 else (xs[k]+xs[k+1])/2-pymupdf.get_text_length(txt,fontsize=8)/2
            page.insert_text((xpos,y+n*20),txt,fontsize=8)
        if ruled:page.draw_line((xs[0],y+n*20+6),(xs[-1],y+n*20+6))
    return rows


class TestModels485(unittest.TestCase):
    def test_full_designations(self):
        names=['BM-3022-5H','GV-3020-5H','GJ550-5T','DC600-4500-2Z','FH-850L','DC-6060 石墨机']
        self.assertEqual([parse_header(n)[0].model for n in names],names)
    def test_suffixes_cannot_collapse(self):
        names=['GV-3020','GV-3020-5H','GV-3020-3H','GV-3020-5H-A','GV-3020-5H-B']
        self.assertEqual(len({parse_header(n)[0].model for n in names}),5)
    def test_interface_suffix_and_rpm_stay_separate(self):
        ref=parse_header('BM-3022-5H 30000r/min')[0]
        self.assertEqual(ref.model,'BM-3022-5H');self.assertEqual(ref.configuration,'30000rpm')


class TestDictionaries485(unittest.TestCase):
    def test_chinese_and_russian(self):
        s=Schema()
        for labels,unit,key in [(['加工范围','X 轴行程'],'mm','travel_x_mm'),
                                (['主轴','主轴最高转速'],'rpm','spindle_speed_rpm'),
                                (['刀库（选配）','刀库容量'],'pcs','tool_capacity'),
                                (['Ход по оси X'],'mm','travel_x_mm')]:
            self.assertEqual(s.match(labels,unit)['key'],key)
        self.assertTrue(s.has_table_header('项目\n单位'))
        self.assertTrue(s.header_is('單位','unit'))
        self.assertTrue(s.is_optional('刀库（选配）'))
    def test_dictionary_changes_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);shutil.copytree(ROOT/'config',folder/'config')
            cfg=folder/'config/schema.json';one=engine_fingerprint(Schema(cfg).config)
            p=folder/'config/dictionaries/zh.json';d=json.loads(p.read_text());d['rules'][0]['pattern']='customtranslation';p.write_text(json.dumps(d))
            self.assertNotEqual(one,engine_fingerprint(Schema(cfg).config))
            self.assertEqual(Schema(cfg).match(['customtranslation'],'mm')['key'],'travel_x_mm')
    def test_json_override_precedes_dictionary(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'override.json';p.write_text(json.dumps({'rules':[{'key':'custom','pattern':'刀库容量','unit':'pcs','category':'Custom'}]}))
            self.assertEqual(Schema(overrides=p).match(['刀库容量'])['key'],'custom')


class TestCoordinateGeometry485(unittest.TestCase):
    def extract(self,ruled=True,blank=False):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'native.pdf';d=pymupdf.open();grid(d.new_page(width=540,height=400),ruled=ruled,blank=blank);d.save(p);d.close()
            return extract_pdf(p)
    def test_horizontal_lines_without_verticals(self):
        d=self.extract();self.assertEqual(set(d['registry']),{'AA-1000-5H','AA-1000-3H'})
        vals={(f['model'],f['parameter']):f['value'] for f in d['facts']}
        self.assertEqual(vals[('AA-1000-5H','travel_x_mm')],'100')
        self.assertEqual(vals[('AA-1000-3H','travel_x_mm')],'200')
        self.assertTrue(all(t['method']=='native_coordinate_columns' for t in d['raw_tables']))
    def test_borderless_rows(self):
        d=self.extract(ruled=False)
        self.assertTrue(any(f['parameter']=='machine_weight_kg' and f['value']=='1600' for f in d['facts']))
    def test_blank_cell_is_not_copied(self):
        d=self.extract(blank=True)
        self.assertFalse(any(f['model']=='AA-1000-3H' and f['parameter']=='tool_capacity' for f in d['facts']))
        self.assertTrue(any(s['model']=='AA-1000-3H' and s['status']=='SOURCE_BLANK' for s in d['cell_statuses']))
    def test_mixed_catalogue_is_classified_per_model(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'mixed.pdf';d=pymupdf.open()
            grid(d.new_page(width=540,height=400),heading='Vertical Machining Center')
            page=d.new_page(width=540,height=400)
            grid(page,heading='Turning Center',models=('TT-1000','TT-2000'),turning=True)
            d.save(p);d.close();result=extract_pdf(p)
            self.assertEqual(result['machine_class'],'mixed')
            self.assertEqual(result['registry']['AA-1000-5H']['machine_class'],'machining')
            self.assertEqual(result['registry']['TT-1000']['machine_class'],'turning')
            quality=check_document(result,Schema())
            turning=next(m for m in quality['models'] if m['model']=='TT-1000')
            self.assertNotIn('travel_y_mm',turning['missing_required'])
            self.assertIn('bar_working_diameter_mm',turning['missing_required'])

    def test_two_tables_on_one_page_keep_separate_classes(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'spread.pdf';d=pymupdf.open();page=d.new_page(width=1040,height=400)
            grid(page)
            grid(page,x=550,heading='Turning Center',models=('TT-1000','TT-2000'),turning=True)
            d.save(p);d.close();result=extract_pdf(p)
            self.assertEqual(result['registry']['AA-1000-5H']['machine_class'],'machining')
            self.assertEqual(result['registry']['TT-1000']['machine_class'],'turning')

    def test_conflicting_title_and_parameters_require_review(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'conflict.pdf';d=pymupdf.open();grid(d.new_page(width=540,height=400),heading='Turning Center')
            d.save(p);d.close();result=extract_pdf(p)
            self.assertEqual(result['registry']['AA-1000-5H']['machine_class'],'unknown')
            self.assertEqual(result['registry']['AA-1000-5H']['class_evidence'],'class_evidence_conflict')

    def test_text_between_model_columns_is_not_copied_to_both(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'ambiguous.pdf';d=pymupdf.open();page=d.new_page(width=540,height=400)
            grid(page,blank=True)
            # The boundary between the two model columns is x=360. A wide
            # undivided string there has no safe per-model assignment.
            page.insert_text((340,90+6*20),'88888888888888',fontsize=8)
            d.save(p);d.close();result=extract_pdf(p)
            self.assertTrue(any(r['reason']=='ambiguous_column_alignment' for r in result['review']))
            self.assertFalse(any('888888' in f['value'] for f in result['facts']))


class TestTypedValues485(unittest.TestCase):
    def typed(self,**extra):
        f={'value':'35','base_value':'35','unit_source':'t','unit_expected':'kg','availability':'present','warnings':[],**extra}
        with tempfile.TemporaryDirectory() as td:
            write_typed_measurements(Path(td),[f])
            with (Path(td)/'MEASUREMENTS_TYPED.csv').open(encoding='utf-8-sig') as f:
                return next(csv.DictReader(f))
    def test_tonnes_are_not_exported_as_kilograms(self):
        row=self.typed();self.assertEqual(row['numeric_value'],'35000');self.assertEqual(row['value'],'35')
        self.assertEqual(row['unit_conversion_factor'],'1000.0')
    def test_source_and_expected_unit_conflict(self):
        warnings=value_warnings('35','kg','kW','machine_weight_kg')
        self.assertEqual(self.typed(warnings=warnings)['typing_status'],'BLOCKED_REVIEW')
    def test_unknown_unit_is_blocked(self):
        warnings=value_warnings('35','kg','mystery','machine_weight_kg')
        self.assertIn('source_unit_unrecognized',warnings)
        self.assertEqual(self.typed(unit_source='mystery',warnings=warnings)['numeric_value'],'')
    def test_optional_is_not_a_standard_scalar(self):
        row=self.typed(availability='optional_only');self.assertEqual(row['typing_status'],'OPTIONAL_ONLY')
        self.assertEqual(row['numeric_value'],'')
        self.assertEqual(display_value([{'value':'35','availability':'optional_only'}],clean=True),'')
    def test_signed_angular_ranges(self):
        row=self.typed(value='﹣110~110',base_value='﹣110~110',unit_source='°',unit_expected='deg')
        self.assertEqual((row['range_min'],row['range_max']),('-110','110'));self.assertEqual(row['numeric_value'],'')
        self.assertEqual(row['typing_status'],'RANGE_UNVERIFIED')
    def test_reversed_ranges_blocked(self):
        self.assertIn('reversed_range',value_warnings('4500-1600','mm','mm','travel_x_mm'))
    def test_parentheses_do_not_imply_imperial_count(self):
        warnings=value_warnings('24 (30)','pcs','pcs','tool_capacity')
        self.assertIn('parenthesized_value_semantics_unresolved',warnings)
        row=self.typed(value='24 (30)',base_value='24 (30)',unit_source='pcs',unit_expected='pcs',warnings=warnings)
        self.assertEqual(row['numeric_value'],'')
    def test_t_slots_are_not_reversed_ranges(self):
        self.assertNotIn('reversed_range',value_warnings('14-5','','mm','table_t_slot'))
    def test_compounds_are_not_scalars(self):
        row=self.typed(value='3/3/3',base_value='3/3/3',unit_source='kW',unit_expected='kW')
        self.assertEqual(row['numeric_value'],'');self.assertEqual(row['typing_status'],'NOT_SCALAR')


class TestKaibo485(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from catalog_engine.paths import data_paths
        cls.pdf=data_paths().input/'凯博数控电子样本2025.pdf'
        if not cls.pdf.exists():raise unittest.SkipTest('Kaibo PDF fixture unavailable')
        cls.document=extract_pdf(cls.pdf)
    def test_all_190_source_transcribed_cells(self):
        with (ROOT/'benchmarks/expected_kaibo_845.csv').open(encoding='utf-8-sig') as f:cases=list(csv.DictReader(f))
        self.assertEqual(len(cases),190)
        for case in cases:
            with self.subTest(model=case['model'],parameter=case['parameter']):
                found=[f for f in self.document['facts'] if f['model']==case['model'] and f['parameter']==case['parameter']]
                self.assertEqual(len(found),1)
                self.assertEqual(found[0]['value'],case['expected'])
                self.assertEqual(found[0]['unit_source'],case['unit_source'])
                self.assertEqual(found[0]['source_page'],int(case['source_page']))
    def test_all_tables_models_and_labels_recovered(self):
        self.assertEqual(len(self.document['raw_tables']),13);self.assertEqual(len(self.document['registry']),38)
        self.assertEqual(self.document['unmapped_fields'],[])
        self.assertTrue(all(len(f['cell_bbox'])==4 and f['source_header'] and not f['human_verified'] for f in self.document['facts']))
    def test_machine_types_are_local(self):
        registry=self.document['registry']
        expected={'BM-3022-5H':'Bridge 5-Axis Machining Center','DC600-4500-2Z':'Profile Machining Center',
                  'DC-6060 石墨机':'Graphite Machining Center','KWJ-630':'Horizontal Machining Center','FH-1580L':'Vertical Machining Center'}
        for model,kind in expected.items():self.assertEqual(registry[model]['machine_type'],kind)
    def test_optional_magazines_and_blank_cells(self):
        capacities=[f for f in self.document['facts'] if f['parameter']=='tool_capacity']
        self.assertEqual(len(capacities),37);self.assertTrue(all(f['availability']=='optional_only' and not f['base_value'] for f in capacities))
        self.assertFalse(any(f['model']=='DC-6040A' for f in capacities))
    def test_rotary_accuracy_is_not_linear_accuracy_conflict(self):
        doc=self.document;check_document(doc,Schema())
        self.assertFalse(any(r['reason']=='unresolved_value_conflict' for r in doc['review']))
        facts=[f for f in doc['facts'] if f['model']=='BM-3022-5H' and f['parameter']=='positioning_accuracy']
        self.assertEqual({f['condition'] for f in facts},{'axes=X/Y/Z','axes=A/C'})
    def test_quality_does_not_certify_optional_or_absent_fields(self):
        q=check_document(self.document,Schema());self.assertFalse(q['quality_passed'])
        bm=next(m for m in q['models'] if m['model']=='BM-3022-5H')
        self.assertEqual(set(bm['missing_required']),{'control_system','tool_capacity'})
