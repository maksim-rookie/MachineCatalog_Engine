"""Local-only v8.4 regression tests. No external services, no customer PDFs."""
import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import compat_v842 as app


def make_table(cells, page=16, table=1, with_boxes=False):
    rows = []
    for i, cells_i in enumerate(cells):
        row = {'page': page, 'table': table, 'row': i, 'kind': 'table',
               'cells': list(cells_i), 'ocr_geometry': False}
        if with_boxes:
            row['cell_boxes'] = [[100*j, 20*i, 100*(j+1), 20*(i+1)] for j in range(len(cells_i))]
        rows.append(row)
    return rows


class FakeReOCR:
    def __init__(self, fixes):
        self.fixes = fixes
        self.calls = []

    def recheck(self, row, col, key, current, unit):
        self.calls.append((row['row'], col, key))
        improved = self.fixes.get((row['row'], col))
        return improved, {'status': 'ocr_consensus_candidate' if improved else 'ocr_no_consensus',
                          'bbox': (1, 2, 3, 4), 'candidate': improved}


class TestV84(unittest.TestCase):
    def test_no_remote_mode_even_when_api_key_exists(self):
        self.assertEqual(app.MODE, 'local')
        with patch.dict(os.environ, {'ANALYSIS_MODE':'hybrid','GROQ_API_KEY':'test-key'}):
            with self.assertRaises(SystemExit) as raised:
                app.main()
            self.assertIn('LOCAL ONLY',str(raised.exception))

    def test_native_blank_cell_audited_but_not_filled(self):
        rows = make_table([
            ['Description', '', 'Unit', 'SVM 4100', 'SVM 5100L'],
            ['Motor Coolant', 'pump motor power', 'kW (Hp)', '', '0.4 (0.54)'],
        ], with_boxes=True)
        facts, review, registry = app.local_extract(Path('SVM-series.pdf'),rows,rechecker=FakeReOCR({}))
        self.assertEqual(set(registry),{'SVM 4100','SVM 5100L'})
        self.assertFalse(any(f['parameter']=='coolant_pump_motor_power_kw' and f['model']=='SVM 4100' for f in facts))
        self.assertTrue(any(r['reason']=='missing_model_value' and r.get('model')=='SVM 4100' for r in review))
        self.assertTrue(any(r['reason']=='empty_model_cell_ocr_unresolved' for r in review))

    def test_tool_change_decimal_restored_only_with_ocr_consensus(self):
        rows = make_table([
            ['Description', '', 'Unit', 'SVM 4100', 'SVM 5100L'],
            ['Tool', 'change time (Tool-to-tool}', 'sec', '13', '13'],
            ['Tool', 'change time (Chip-to-chip)', 'sec', '3.3', '3.3'],
        ], with_boxes=True)
        repair = FakeReOCR({(1,3):'1.3',(1,4):'1.3'})
        facts, review, _ = app.local_extract(Path('SVM-series.pdf'),rows,rechecker=repair)
        actual = [f for f in facts if f['parameter']=='tool_change_time_sec' and f['condition']=='tool_to_tool']
        self.assertEqual(len(actual), 2)
        self.assertTrue(all(f['value']=='1.3' and f['value_raw']=='13' for f in actual))
        self.assertTrue(all(f['recovery_note']=='cell_ocr_consensus_candidate' for f in actual))
        self.assertEqual(sum(r['reason']=='cell_ocr_recovered_needs_review' for r in review),2)

    def test_taper_ocr_can_restore_but_not_hallucinate(self):
        rows=make_table([
            ['Description','', 'Unit','SVM 4100','SVM 5100L'],
            ['Taper','','','150 #40','ISO #40'],
        ],with_boxes=True)
        facts,review,_=app.local_extract(Path('SVM-series.pdf'), rows,
                                          rechecker=FakeReOCR({(1,3):'ISO #40'}))
        self.assertEqual({f['model']:f['value'] for f in facts if f['parameter']=='spindle_taper'},
                         {'SVM 4100':'ISO #40','SVM 5100L':'ISO #40'})

    def test_mass_count_conflict_is_contextually_reclassified_but_unverified(self):
        rows=make_table([
            ['Description', '', 'Unit', 'NHP 4000','NHP 5000','NHP 5000S'],
            ['Table', 'Tool storage capacity', 'kg (lb)', '(ø24.8 x 35.4h) 400 (881.8)',
             '500 (1102.3)', ''],
        ],page=17,with_boxes=True)
        facts, review, _=app.local_extract(Path('NHP_4000_5000.pdf'),rows,rechecker=FakeReOCR({}))
        recovered = [f for f in facts if f['parameter'] == 'table_load_kg']
        self.assertEqual({f['model']:f['value'] for f in recovered},
                         {'NHP 4000':'400 (881.8)', 'NHP 5000':'500 (1102.3)'})
        self.assertTrue(all(f['review_status']=='recovered_needs_review' for f in recovered))
        self.assertFalse(any(f['parameter']=='tool_capacity' for f in facts))
        candidates=[r for r in review if r['reason']=='tool_capacity_unit_conflict']
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['suggested_parameter'],'table_load_kg')
        self.assertEqual(candidates[0]['candidates_unverified'][0]['candidate_value'],'400 (881.8)')
        # No geometric span was shown; do not copy the value to NHP 5000S.
        self.assertNotIn('NHP 5000S',{f['model'] for f in recovered})

    def test_metric_imperial_validation_and_option_pairing(self):
        self.assertEqual(app.invalid_unit('spindle_torque_nm','N-m (ft-lbs)','5.5 (122.1)'),
                         'metric_imperial_pair_mismatch')
        self.assertEqual(app.invalid_unit('machine_width_mm','mm (inch)','00 (11.8)'),
                         'metric_imperial_pair_invalid')
        self.assertEqual(app.invalid_unit('cutting_feed_m_min','m/min (ipm)','000 (472.4)'),
                         'metric_imperial_pair_invalid')
        self.assertEqual(app.invalid_unit('tool_max_length_mm','mm (inch)','300 (11.8)'), '')
        base, options, _ = app.parse_options('230 (169.7) {303, 221 (223.6, 163.1)}*')
        self.assertEqual(base, '230 (169.7)')
        self.assertEqual(options, ['303 (223.6)', '221 (163.1)'])
        self.assertEqual(app.duty_values_from_power('18.5/5.5 (24.8/7.4)', 'S3/continuous'),
                         {'S3':'18.5','Continuous':'5.5'})

    def test_inferred_ocr_blank_is_not_authoritative(self):
        # Unit tests of the real local OCR gate; mock only image recognition.
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'SVM-series.pdf'
            path.write_bytes(b'not a PDF')
            reader=app.LocalCellOCR(path)
            reader._pdf=None
            reader._crop=lambda page,bbox: Image.new('RGB',(220,60),'white')
            row={'page':16,'table':1,'row':29,'kind':'table',
                 'cells':['Motor','','kW (Hp)','','0.4 (0.54)'],
                 'cell_boxes':[(0,0,60,30)]*5, 'bbox_inferred':True}
            with patch('pytesseract.image_to_string',side_effect=['1.1 (1.5)','1.1 (1.5)']):
                val, event=reader.recheck(row,3,'coolant_pump_motor_power_kw','','kW (Hp)')
            self.assertIsNone(val)
            self.assertEqual(event['status'],'ocr_candidate_weak_column_evidence')
            reader.close()

    def test_wide_audit_files_generated(self):
        with tempfile.TemporaryDirectory() as td:
            prior=app.OUTPUT
            try:
                app.OUTPUT=Path(td)
                row={'source_file':'SVM-series.pdf','source_page':16,'table':1,'row':29,
                     'row_id':'16:1:29','models_context':'SVM 4100','model':'SVM 4100',
                     'parameter':'coolant_pump_motor_power_kw','cells':['Motor','Power','kW (Hp)','','0.4'],
                     'reason':'missing_model_value'}
                app.write_outputs([{'source_file':'SVM-series.pdf','facts':[],
                                    'review':[row],'registry':{'SVM 4100':{'evidence':'native_table_header'}}}])
                self.assertTrue((Path(td)/'CELL_ISSUES.csv').exists())
                with open(Path(td)/'RUN_SUMMARY.json',encoding='utf-8') as stream:
                    self.assertEqual(json.load(stream)['network_calls'],0)
                with open(Path(td)/'CELL_ISSUES.csv',encoding='utf-8-sig') as f:
                    self.assertEqual(next(csv.DictReader(f))['parameter'],'coolant_pump_motor_power_kw')
            finally:
                app.OUTPUT=prior


if __name__=='__main__':
    unittest.main()
