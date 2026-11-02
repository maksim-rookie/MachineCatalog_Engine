"""Active v8.4.3 engine tests, independent of the archived compatibility layer."""
from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from catalog_engine.models import parse_header,select_square_variant,select_footnote,footnote_rules
from catalog_engine.schema import Schema
from catalog_engine.values import option_parts,value_warnings,split_embedded_conditions
from catalog_engine.storage import engine_fingerprint,cache_key
from catalog_engine.progress import RunProgress
from catalog_engine.quality import check_document

class TestModels(unittest.TestCase):
    def test_slash_suffix_expansion(self):
        self.assertEqual([r.model for r in parse_header('Lynx 2100A [NT]/LA')],['LYNX 2100A','LYNX 2100LA'])
    def test_three_letter_suffix_is_not_lost(self):
        self.assertEqual(parse_header('Lynx 2100LMSA')[0].model,'LYNX 2100LMSA')
    def test_spaced_suffix_is_expanded(self):
        self.assertEqual([r.model for r in parse_header('Lynx 2100 MA/LMA/LMSA')],['LYNX 2100MA','LYNX 2100LMA','LYNX 2100LMSA'])
    def test_numeric_slash_is_not_spindle_interface(self):
        self.assertEqual([r.model for r in parse_header('DHF 8000/8000ST')],['DHF 8000','DHF 8000ST'])
    def test_mynx_interface_and_roman_numeral(self):
        self.assertEqual(parse_header('Mynx 5400/50\u2161')[0].model,'MYNX 5400/50II')
    def test_generation_preserved_even_if_superscript_is_first(self):
        self.assertEqual(parse_header('2nd DVF 5000')[0].model,'DVF 5000 2nd Gen.')
    def test_st_suffix_not_generation(self):
        self.assertEqual(parse_header('DHF 8000ST')[0].generation,'')
    def test_speed_configuration_not_new_model(self):
        refs=parse_header('DHF 8000 30000r/min / PSC')
        self.assertEqual(len(refs),1);self.assertEqual(refs[0].model,'DHF 8000')
        self.assertEqual(refs[0].configuration,'30000rpm / PSC')
    def test_long_and_short_need_source_legend(self):
        raw='330 [550] (13.0 [21.7])'
        self.assertEqual(select_square_variant(raw,'long',True)[0],'550 (21.7)')
        self.assertEqual(select_square_variant(raw,'short',True)[0],'330 (13.0)')
        self.assertEqual(select_square_variant(raw,'long',False),(raw,'square_bracket_semantics_unresolved'))
    def test_model_specific_footnote(self):
        rules=footnote_rules('** Only DHF 8000ST\n')
        raw='800 x 800 (31.5 x 31.5), \u00f81000 (\u00f839.4)**'
        self.assertEqual(select_footnote(raw,'DHF 8000ST',rules)[0],'\u00f81000 (\u00f839.4)')
        self.assertEqual(select_footnote(raw,'DHF 8000',rules)[0],'800 x 800 (31.5 x 31.5)')

class TestSchemaAndValues(unittest.TestCase):
    def setUp(self): self.s=Schema()
    def test_main_and_sub_spindles_are_separate(self):
        self.assertEqual(self.s.match(['Spindle','Spindle speed'])['key'],'spindle_speed_rpm')
        self.assertEqual(self.s.match(['Sub spindle','Spindle speed'])['key'],'sub_spindle_speed_rpm')
    def test_turret_is_not_magazine(self):
        self.assertEqual(self.s.match(['Turret','No.of tool stations'])['key'],'turret_station_count')
    def test_chuck_inches_not_millimetres(self):
        self.assertEqual(self.s.match(['Capacity','Chuck size'],'inch')['key'],'chuck_size_inch')
        self.assertEqual(self.s.match(['Capacity','Chuck size'],'mm (inch)')['key'],'chuck_size_mm')
    def test_rapid_traverse_and_travel_are_separate(self):
        self.assertEqual(self.s.match(['Rapid traverse rate','B axis'])['key'],'rapid_traverse_b_rpm')
        self.assertEqual(self.s.match(['Travel distance','B axis'])['key'],'travel_b_deg')
    def test_legacy_label_typos_do_not_change_source_strings(self):
        labels=['Sub spindle','Spindle moter power (FANUC)']
        self.assertEqual(self.s.match(labels)['key'],'sub_spindle_power_kw')
        self.assertIn('moter',labels[1])
    def test_unknown_field_is_not_guessed(self):
        self.assertIsNone(self.s.match(['Unknown','Flux modulation']))
    def test_optional_only_is_not_standard(self):
        b,o,a=option_parts('{6000}','spindle_speed_rpm')
        self.assertEqual((b,o,a),('', ['6000'],'optional_only'))
    def test_imperial_options_not_duplicate_primary_options(self):
        self.assertEqual(option_parts('80 {76} (3.1 {3.0})','tool_max_diameter_mm')[1],['76'])
    def test_source_units_are_flagged_not_corrected(self):
        self.assertIn('source_unit_conflict',value_warnings('1000 (1341.0)','kg','kW (Hp)','table_load_kg'))
        self.assertIn('source_metric_imperial_mismatch',value_warnings('700 (938.7)','mm','mm (inch)','tailstock_travel_mm'))
    def test_compound_weight_unit_mismatch(self):
        self.assertIn('source_metric_imperial_mismatch',value_warnings('8000 (17636.7) (30, 40, 60T), 9000 (354.3) (120T)','kg','kg (lb)','machine_weight_kg'))
    def test_controller_speed_drive_axes(self):
        rows=split_embedded_conditions('TNC7 :\n15K Direct 17/10\n(S6 25%/cont.)\n20K Built-in 30/24\n(S6 40%/cont.)',['Spindle','Max. power'],'spindle_power_kw')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['condition'],{'controller':'TNC7','spindle_rpm':20000,'drive':'built-in'})
        self.assertEqual(rows[1]['value'],'30/24')
    def test_no_invented_y_requirement_for_lathe(self):
        self.assertIn('travel_z_mm',self.s.required('turning'))
        self.assertNotIn('travel_y_mm',self.s.required('turning'))
    def test_schema_override_is_live(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'extra.json'
            p.write_text(json.dumps({'rules':[{'key':'flux_index','pattern':'Flux modulation','unit':'','category':'Other'}]}))
            self.assertEqual(Schema(overrides=p).match(['Flux modulation'])['key'],'flux_index')
    def test_cache_depends_on_schema(self):
        a=engine_fingerprint(self.s.config);other=dict(self.s.config);other['schema_version']='changed'
        self.assertNotEqual(a,engine_fingerprint(other))
    def test_cache_depends_on_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'a.pdf';p.write_bytes(b'first');a=cache_key(p,'v')
            p.write_bytes(b'second');self.assertNotEqual(a,cache_key(p,'v'))
    def test_progress_monotone_and_quality_separate(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            p=Path(temp);tracker=RunProgress(p,2)
            tracker.file_work(0,.9,'Read');tracker.file_work(0,.2,'Read')
            result=tracker.finish(quality_passed=False)
            rows=[json.loads(s) for s in (p/'RUN_PROGRESS.jsonl').read_text().splitlines()]
            values=[r['progress_percent'] for r in rows]
            self.assertEqual(values,sorted(values));self.assertEqual(values[-1],100)
            self.assertFalse(result['quality_passed']);self.assertFalse(result['completed_with_errors'])

if __name__=='__main__': unittest.main()
