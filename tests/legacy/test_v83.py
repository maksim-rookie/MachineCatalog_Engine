"""Run from project root: python -m unittest discover -s tests -v.

No API calls; no actual customer's PDF needed. These tests verify the
failure modes documented in the v8.2 audit.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import compat_v842 as app


def make_table(cells, page=16, number=1):
    return [dict(page=page, table=number, row=i, kind='table', cells=list(row),
                 ocr_geometry=False) for i,row in enumerate(cells)]


class TestV83(unittest.TestCase):
    def test_svm_individual_models_and_header_recovery(self):
        raw = [
            ['Description', '', 'Unit', 'SVM 4100', 'SVM 5100L'],
            ['Travels', 'Xaxis', 'mm (inch)', '770 (30.3)', '1270 (50.0)'],
            ['Travel', 'Y axis', 'mm (inch)', '410 (16.1)', '510 (20.1)'],
            ['Travel', 'Z axis', 'mm (inch)', '510 (20.1)', '510 (20.1)'],
            ['Table', 'surface type', 'mm', 'T-SLOT (3-125 x 18H8)', 'T-SLOT (4-125 x 18H8)'],
            ['Spindle', 'Max. spindle speed', 'r/min', '12000', '12000'],
            ['Taper','','','150 #40','ISO #40'],
            ['Spindle','spindle power (S3/continuous)', 'kW (Hp)', '18.5/5.5 (24.8/7.4)', '18.5/5.5 (24.8/7.4)'],
            ['Feedrates', 'Xaxis m/min', '(ipm)', '36 (1417.3)', '36 (1417.3)'],
            ['', 'Y axis m/min', '(ipm)', '36 (1417.3)', '36 (1417.3)'],
            ['', 'Zaxis m/min', '(ipm)', '36 (1417.3)', '36 (1417.3)'],
            ['Tool', 'storage capa. m/min', '(ipm)', '30', '30'],
            ['', 'Continous', 'mm (inch)', '80 (3.1)', '80 (3.1)'],
            ['', 'Without adjacent tools', 'mm (inch)', '125 (4.9)', '125 (4.9)'],
            ['Tool','change time (Tool-to-tool}', 'sec','13','13'],
            ['Tool','change time (Chip-to-chip)', 'sec','3.3','3.3'],
            ['Motor Coolant','pump motor power','kW (Hp)','','0.4 (0.54)'],
            ['Compressed','air supply','MPa (psi)','0.54','0.54'],
            ['Width','','mm (inch)','2250 (88.6)','3100 (122.0}'],
            ['Control NC','system','','DN Solutions CNC D300','DN Solutions CNC D300'],
        ]
        facts, review, registry = app.local_extract(Path('SVM-series.pdf'), make_table(raw))
        self.assertEqual(set(registry), {'SVM 4100', 'SVM 5100L'})
        indexed = {(f['model'],f['parameter'],f.get('condition')):f for f in facts}
        for model in registry:
            for key in ('travel_x_mm','table_t_slot','rapid_traverse_x_m_min',
                        'rapid_traverse_y_m_min','rapid_traverse_z_m_min',
                        'air_pressure_mpa','control_system'):
                self.assertIn((model,key,''),indexed, (model,key))
            self.assertIn((model,'tool_max_diameter_mm','continuous_adjacent_tools'),indexed)
            self.assertIn((model,'tool_max_diameter_mm','without_adjacent_tools'),indexed)
            self.assertNotIn((model,'tool_change_time_sec','tool_to_tool'), indexed)
            self.assertNotIn((model,'tool_capacity',''), indexed) # source unit conflict
            self.assertEqual(indexed[model,'spindle_power_kw','']['duty_cycle'], 'S3/continuous')
        self.assertIn(('SVM 5100L','spindle_taper',''),indexed)
        self.assertNotIn(('SVM 4100','spindle_taper',''),indexed) # 150 != ISO
        self.assertIn(('SVM 5100L','coolant_pump_motor_power_kw',''),indexed)
        self.assertNotIn(('SVM 4100','coolant_pump_motor_power_kw',''),indexed)
        self.assertEqual(indexed['SVM 5100L','machine_width_mm','']['value'], '3100')
        reasons = {r['reason'] for r in review}
        self.assertIn('tool_to_tool_exceeds_chip_to_chip',reasons)
        self.assertIn('tool_capacity_unit_conflict',reasons)
        self.assertIn('spindle_taper_ocr_uncertain',reasons)
        self.assertIn('rapid_traverse_header_inferred',reasons)
        self.assertIn('ocr_imperial_bracket_broken_metric_only',reasons)

    def test_numeric_prose_and_inertia_mass_bleed(self):
        self.assertEqual(app.invalid_unit('tool_change_time_sec','sec','Tool change 8 times during processing'),
                         'non_numeric_measurement')
        self.assertEqual(app.invalid_unit('table_work_inertia_nm2','N*m2','{700 (1543.2)}* 294'),
                         'non_numeric_measurement')
        scalar, reason = app.recover_inertia_value('{700 (1543.2)}* 294')
        self.assertEqual(scalar,'294')
        self.assertEqual(reason,'inertia_mass_bleed_recovered')
        self.assertFalse(app.invalid_unit('table_work_inertia_nm2','N*m2',scalar))

    def test_shared_geometry_and_page_scope(self):
        head=['Description','','','Unit','NHP 4000','NHP 5000','NHP 5000S']
        boxes=[(i*100,0,(i+1)*100,25) for i in range(7)]
        line=['Spindle','Max. spindle speed','','r/min','15000 {20000}*','','']
        span_boxes=[(i*100,26,(i+1)*100,50) for i in range(7)]
        span_boxes[4]=(400,26,700,50)
        span_boxes[5]=None
        span_boxes[6]=None
        rows=make_table([head,line],page=17,number=2)
        rows[0]['cell_boxes']=boxes
        rows[1]['cell_boxes']=span_boxes
        f,r,registry=app.local_extract(Path('NHP_4000_5000.pdf'),rows)
        self.assertEqual(set(registry),{'NHP 4000','NHP 5000','NHP 5000S'})
        self.assertEqual(len(f),3)
        self.assertTrue(all(x['scope']=='shared_merged_cell' for x in f))
        self.assertTrue(all(x['source_page']==17 and x['source_row_id']=='17:2:1' for x in f))
        self.assertTrue(all(x['options']==['20000'] for x in f))

    def test_cross_page_control_does_not_merge(self):
        def fact(page, value, method):
            return {'source_file':'NHP_4000_5000.pdf','model':'NHP 5000',
                    'parameter':'control_system','value':value,'condition':'','duty_cycle':'',
                    'method':method,'source_page':page,'source_table':1,
                    'source_row':2,'source_row_id':f'{page}:1:2','source_quote':value}
        correct=fact(17,'Fanuc 31i Plus','local_table:merged_geometry')
        misleading=fact(13,'S-ONE','local_table:filename_order_candidate')
        accepted,review=app.resolve_quality_conflicts(Path('NHP_4000_5000.pdf'),[correct, misleading],[])
        self.assertEqual(len(accepted),1)
        self.assertEqual(accepted[0]['value'],'Fanuc 31i Plus')
        self.assertIn('cross_page_conflict',{r['reason'] for r in review})
        tied=[fact(17,'Controller A','local_table:explicit_table_header'),
              fact(13,'Controller B','local_table:explicit_table_header')]
        accepted,review=app.resolve_quality_conflicts(Path('NHP_4000_5000.pdf'),tied,[])
        self.assertEqual(accepted,[]) # cannot choose between equally grounded pages

    def test_options_and_duty_separated(self):
        base,options,kind=app.parse_options(
            '30/18.5 (40.2/24.8) {37/22 (49.6/29.5), 37/18.5 (49.6/24.8)}*')
        self.assertEqual(base,'30/18.5 (40.2/24.8)')
        self.assertEqual(options,['37/22 (49.6/29.5)','37/18.5 (49.6/24.8)'])
        self.assertEqual(kind,'catalog_star_option')
        self.assertEqual(app.duty_condition('Spindle power (S3/Cont.)'),'S3/Cont.')
        self.assertEqual(app.duty_condition('Max spindle power (S3/continuous)'),'S3/continuous')

    def test_wide_exports_have_provenance_and_options(self):
        rows=make_table([
            ['Description','','','Unit','NHP 4000','NHP 5000','NHP 5000S'],
            ['Spindle','Max. spindle speed','','r/min','15000 {20000}*','15000 {20000}*','15000 {20000}*']
        ],page=17,number=2)
        facts,review,reg=app.local_extract(Path('NHP_4000_5000.pdf'),rows)
        with tempfile.TemporaryDirectory() as td:
            prior=app.OUTPUT
            try:
                app.OUTPUT=Path(td)
                app.write_outputs([{'source_file':'NHP_4000_5000.pdf','facts':facts,
                                    'review':review,'registry':reg}],None)
                import csv
                with open(Path(td)/'ALL_MODELS_long.csv',newline='',encoding='utf-8-sig') as f:
                    long=list(csv.DictReader(f))
                with open(Path(td)/'ALL_MODELS_options.csv',newline='',encoding='utf-8-sig') as f:
                    opts=list(csv.DictReader(f))
                self.assertEqual(len(long),3)
                self.assertEqual(len(opts),3)
                self.assertTrue(all(x['source_row_id']=='17:2:1' for x in long))
                self.assertTrue(all(x['base_value']=='15000' for x in long))
                self.assertTrue(all(x['option_value']=='20000' for x in opts))
            finally:
                app.OUTPUT=prior


if __name__ == '__main__':
    unittest.main()
