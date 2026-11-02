from pathlib import Path
import contextlib
import csv
import io
import json
import shutil
import tempfile
import unittest
import fitz
from catalog_engine.extract import extract_pdf
from catalog_engine.geometry import extract_tables
from catalog_engine.schema import Schema
from catalog_engine.quality import check_document
from catalog_engine.runner import Settings,run
from benchmarks.verify_v843 import evaluate,DEFAULT_EXPECTED

ROOT=Path(__file__).resolve().parents[1]
from catalog_engine.paths import data_paths
PDF=data_paths().input
FILES=['lynx-2100-2600-series-new.pdf','DVF_5000.pdf','dhf-8000-series.pdf','dn-solutions-vertical-machining-center-mynx-ii-series.pdf']

class TestRealCatalogues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in FILES:
            if not (PDF/name).exists(): raise unittest.SkipTest('Reference PDF not present: '+name)
        cls.docs={name:extract_pdf(PDF/name) for name in FILES}
    def test_all_source_transcribed_reference_values(self):
        with DEFAULT_EXPECTED.open(encoding='utf-8-sig',newline='') as f: expected=list(csv.DictReader(f))
        results=evaluate([f for d in self.docs.values() for f in d['facts']],expected)
        failed=[r for r in results if r['result']!='PASS']
        self.assertEqual(failed,[],str(failed[:10]))
    def test_model_counts_and_generations(self):
        self.assertEqual([len(self.docs[n]['registry']) for n in FILES],[15,1,2,7])
        self.assertIn('DVF 5000 2nd Gen.',self.docs['DVF_5000.pdf']['registry'])
        self.assertNotIn('DVF 5000',self.docs['DVF_5000.pdf']['registry'])
    def test_no_source_specific_profile_used(self):
        for d in self.docs.values():
            self.assertTrue(all(f['method'].startswith('native_') for f in d['facts']))
    def test_no_forward_fill_of_blank_cells(self):
        d=self.docs[FILES[0]]
        self.assertFalse(any(f['model']=='LYNX 2100LMSB' and f['parameter']=='driven_tool_speed_rpm' for f in d['facts']))
        self.assertTrue(any(r['reason']=='empty_source_cell' and r.get('model')=='LYNX 2100LMSB' for r in d['review']))
    def test_primary_and_counter_spindle_do_not_merge(self):
        d=self.docs[FILES[0]]
        values={f['parameter']:f['value'] for f in d['facts'] if f['model']=='LYNX 2100LMSB' and f['parameter'].endswith('speed_rpm')}
        self.assertEqual(values['spindle_speed_rpm'],'4500');self.assertEqual(values['sub_spindle_speed_rpm'],'6000')
    def test_not_applicable_is_not_zero(self):
        d=self.docs[FILES[0]]
        f=next(f for f in d['facts'] if f['model']=='LYNX 2100A' and f['parameter']=='sub_spindle_speed_rpm')
        self.assertEqual(f['value'],'-');self.assertEqual(f['availability'],'not_applicable')
    def test_optional_header_qualifier_retained(self):
        d=self.docs[FILES[0]]
        self.assertIn('NT',d['registry']['LYNX 2100A']['header_qualifiers'])
        self.assertFalse(any('[NT]' in model for model in d['registry']))
    def test_dvf_controller_blocks_are_complete(self):
        d=self.docs['DVF_5000.pdf']
        self.assertEqual(sum(f['parameter']=='spindle_power_kw' for f in d['facts']),9)
        self.assertEqual(sum(f['parameter']=='spindle_torque_nm' for f in d['facts']),9)
    def test_accessory_dimensions_not_used_as_machine_dimensions(self):
        d=self.docs['DVF_5000.pdf']
        f=next(f for f in d['facts'] if f['parameter']=='machine_dimensions_mm')
        self.assertEqual(f['source_page'],21);self.assertNotIn('1875',f['value'])
    def test_mynx_source_errors_are_preserved(self):
        d=self.docs[FILES[3]]
        f=next(f for f in d['facts'] if f['model']=='MYNX 5400II' and f['parameter']=='table_load_kg')
        self.assertEqual(f['unit_source'],'kW (Hp)');self.assertEqual(f['value'],'1000 (1341.0)')
        self.assertIn('source_unit_conflict',f['warnings'])
    def test_renamed_source_has_same_model_values(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'unrelated_renamed_file.pdf';shutil.copyfile(PDF/FILES[0],p)
            other=extract_pdf(p)
            as_set=lambda d:{(f['model'],f['configuration'],f['parameter'],f['condition'],f['value']) for f in d['facts']}
            self.assertEqual(as_set(other),as_set(self.docs[FILES[0]]))
    def test_all_facts_have_visual_provenance(self):
        for d in self.docs.values():
            for f in d['facts']:
                self.assertEqual(len(f['cell_bbox']),4);self.assertGreater(f['source_page'],0)
                self.assertTrue(f['source_header']);self.assertTrue(f['value_raw']);self.assertFalse(f['human_verified'])
    def test_extraction_gate_is_not_quality_certificate(self):
        import copy
        q=check_document(copy.deepcopy(self.docs[FILES[3]]),Schema())
        self.assertTrue(q['extraction_gate_passed']);self.assertFalse(q['quality_passed'])


def synthetic_grid(path:Path,title=True):
    doc=fitz.open();p=doc.new_page(width=600,height=800)
    p.insert_text((30,30),'Vertical Machining Center')
    if title: p.insert_text((30,60),'MACHINE SPECIFICATIONS',fontsize=17)
    data=[['Description','Unit','ABC 1234'],['Travel distance X-axis','mm (inch)','100 (3.9)'],
          ['Travel distance Y-axis','mm (inch)','200 (7.9)'],['Travel distance Z-axis','mm (inch)','300 (11.8)'],
          ['Spindle speed','r/min','8000'],['Tool storage capacity','ea','30'],
          ['Machine weight','kg (lb)','1000 (2204.6)'],['Control NC system','-','FANUC 0i']]
    xs=[30,300,400,570];ys=[100+i*35 for i in range(len(data)+1)]
    for x in xs: p.draw_line((x,ys[0]),(x,ys[-1]))
    for y in ys: p.draw_line((xs[0],y),(xs[-1],y))
    for i,row in enumerate(data):
        for j,txt in enumerate(row): p.insert_text((xs[j]+5,ys[i]+22),txt,fontsize=9)
    doc.save(path);doc.close()

class TestGeneralFallback(unittest.TestCase):
    def test_line_only_table_without_fill_rectangles(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'test.pdf';synthetic_grid(p)
            d=extract_pdf(p)
            self.assertIn('ABC 1234',d['registry'])
            self.assertTrue(any(f['parameter']=='travel_x_mm' and f['value']=='100 (3.9)' for f in d['facts']))
    def test_no_title_table_can_qualify_structurally(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'test.pdf';synthetic_grid(p,title=False)
            d=extract_pdf(p)
            self.assertIn('ABC 1234',d['registry'])
            self.assertEqual(d['source_pages'][0]['kind'],'machine_specifications_structural')
    def test_unsupported_page_never_gets_guessed_values(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'test.pdf';d=fitz.open();page=d.new_page();page.insert_text((30,30),'Decorative brochure without a specification table');d.save(p);d.close()
            result=extract_pdf(p);self.assertEqual(result['facts'],[])
            self.assertFalse(check_document(result,Schema())['extraction_gate_passed'])

class TestRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PDF/'DVF_5000.pdf').exists(): raise unittest.SkipTest('DVF fixture missing')
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.input=self.root/'pdf';self.input.mkdir();shutil.copyfile(PDF/'DVF_5000.pdf',self.input/'DVF_5000.pdf')
        self.settings=Settings(self.input,self.root/'output'/'v8_4_3',self.root/'cache')
    def tearDown(self): self.tmp.cleanup()
    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()): return run(self.settings)
    def test_real_pipeline_cache_and_history(self):
        one,s1,c1=self.execute();two,s2,c2=self.execute()
        self.assertNotEqual(one,two);self.assertTrue((one/'RUN_MANIFEST.json').exists())
        self.assertEqual(s1['cache_hits'],0);self.assertEqual(s2['cache_hits'],1)
        self.assertEqual(c1,0);self.assertEqual(c2,0);self.assertFalse(s2['quality_passed'])
        self.assertEqual((self.settings.output_root/'LATEST_RUN.txt').read_text().strip(),two.name)
    def test_corrupt_cache_rebuilt(self):
        self.execute()
        next(self.settings.cache_root.glob('*.json')).write_text('{bad json')
        out,summary,code=self.execute()
        self.assertEqual(summary['cache_hits'],0);self.assertEqual(code,0)
        self.assertIn('cache_rebuilt_reason',json.loads((out/'RUN_TIMING.json').read_text())['file_timings'][0])
    def test_strict_quality_returns_nonzero_after_export(self):
        self.settings.strict_quality=True;out,summary,code=self.execute()
        self.assertEqual(code,2);self.assertTrue((out/'ALL_MODELS_long.csv').exists())
    def test_no_cache_does_not_create_cache(self):
        self.settings.use_cache=False;out,summary,code=self.execute()
        self.assertFalse(self.settings.cache_root.exists());self.assertEqual(summary['cache_hits'],0)
    def test_eight_hidden_columns_and_clean_missing_cells(self):
        out,_,_=self.execute()
        with (out/'ALL_MODELS_wide.csv').open(encoding='utf-8-sig') as f: reader=csv.DictReader(f);rows=list(reader);cols=reader.fieldnames
        hidden=set(Schema().config['hidden_wide_parameters'])
        self.assertTrue(all(c.split('] ',1)[-1] not in hidden for c in cols))
        self.assertIn('[Table] table_load_kg',cols)
        with (out/'ALL_MODELS_long.csv').open(encoding='utf-8-sig') as f:
            rows=list(csv.DictReader(f));self.assertTrue(any(r['parameter']=='tool_shank_type' for r in rows))
    def test_options_not_polluted_with_other_configuration(self):
        shutil.copyfile(PDF/'dhf-8000-series.pdf',self.input/'dhf-8000-series.pdf')
        out,_,_=self.execute()
        with (out/'ALL_MODELS_wide.csv').open(encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
        row=next(r for r in rows if r['model']=='DHF 8000')
        self.assertTrue(row['[Automatic tool changer (ATC)] tool_capacity'].startswith('60'))
        with (out/'ALL_CONFIGURATIONS_wide.csv').open(encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
        hs=next(r for r in rows if r['model']=='DHF 8000' and r['configuration']!='base')
        self.assertTrue(hs['[Automatic tool changer (ATC)] tool_capacity'].startswith('80'))

if __name__=='__main__': unittest.main()
