"""v8.4.2: localized NHP mass-label repair, model-field status and local-only safety.

All tests are synthetic. No customer PDF, benchmark values or network is read.
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import compat_v842 as app


def comparative_table(section='Table', unit='kg (lb)', suffix='500 (1102.3)',
                      inferred=False, shared=True):
    vals = [
        ['Description', '', '', 'Unit', 'NHP 4000', 'NHP 5000', 'NHP 5000S'],
        [section, 'Pallet size', '', 'mm (inch)', '400 x 400 (15.7 x 15.7)',
         '500 x 500 (19.7 x 19.7)', '500 x 600 (19.7 x 19.7)'],
        ['', 'Tool storage capacity', '', unit,
         '(ø24.8 x 35.4h) 400 (881.8)', suffix, ''],
        ['', 'Work inertia ***', '', 'N·m2', '108', '{700 (1543.2)}* 294', ''],
    ]
    result = []
    for i, cells in enumerate(vals):
        boxes = [[j * 100, i * 20, (j + 1) * 100, (i + 1) * 20]
                 for j in range(len(cells))]
        if shared and i in (2, 3):
            boxes[5] = [500, i * 20, 700, (i+1) * 20]
            boxes[6] = None
        result.append({'page': 17, 'table': 2, 'row': i,
                       'kind': 'table', 'cells': cells, 'cell_boxes': boxes,
                       'ocr_geometry': inferred, 'bbox_inferred': inferred})
    return result


class NoOCR:
    def recheck(self, row, col, key, current, unit):
        return None, {'status': 'intentionally_unavailable'}


class TestV842(unittest.TestCase):
    def test_nhp_contextual_mass_and_option_pair(self):
        facts, reviews, registry = app.local_extract(Path('NHP_4000_5000.pdf'),
            comparative_table(), rechecker=NoOCR())
        indexed = {(f['model'], f['parameter']): f for f in facts}
        expected = {'NHP 4000': '400 (881.8)',
                    'NHP 5000': '500 (1102.3) {700 (1543.2)}*',
                    'NHP 5000S': '500 (1102.3) {700 (1543.2)}*'}
        self.assertEqual(set(registry), set(expected))
        for model, value in expected.items():
            f = indexed[(model, 'table_load_kg')]
            self.assertEqual(f['value'], value)
            self.assertEqual(f['review_status'], 'recovered_needs_review')
            self.assertEqual(f['source_row_id'], '17:2:2')
            self.assertEqual(f['value_raw'], '(ø24.8 x 35.4h) 400 (881.8)' if model=='NHP 4000'
                             else '500 (1102.3)')
            self.assertEqual(indexed[(model, 'table_work_inertia_nm2')]['value'],
                             '108' if model=='NHP 4000' else '294')
        self.assertEqual(indexed[('NHP 5000S','table_load_kg')]['scope'],'shared_merged_cell')
        self.assertEqual(indexed[('NHP 5000','table_load_kg')]['options'], ['700 (1543.2)'])
        self.assertEqual(indexed[('NHP 5000','table_load_kg')]['source_extra_row_ids'], ['17:2:3'])
        self.assertFalse(any(f['parameter']=='tool_capacity' for f in facts))
        self.assertFalse(any(r['reason']=='empty_model_cell_ocr_unresolved' and
                             r.get('parameter')=='table_load_kg' for r in reviews))
        self.assertIn('table_load_label_unit_reclassified',{r['reason'] for r in reviews})

    def test_not_in_table_section_stays_in_review(self):
        facts, reviews, _ = app.local_extract(Path('NHP_4000_5000.pdf'),
            comparative_table(section='Magazine'), rechecker=NoOCR())
        self.assertFalse(any(f['parameter']=='table_load_kg' for f in facts))
        self.assertIn('tool_capacity_unit_conflict',{r['reason'] for r in reviews})

    def test_mismatched_mass_pair_is_not_invented(self):
        facts, reviews, _ = app.local_extract(Path('NHP_4000_5000.pdf'),
            comparative_table(suffix='500 (2999.0)'), rechecker=NoOCR())
        self.assertFalse(any(f['model']=='NHP 5000' and f['parameter']=='table_load_kg'
                             for f in facts))
        self.assertFalse(any(f['model']=='NHP 5000S' and f['parameter']=='table_load_kg'
                             for f in facts))
        self.assertTrue(any(f['model']=='NHP 4000' and f['parameter']=='table_load_kg'
                            for f in facts))
        self.assertIn('tool_capacity_unit_conflict',{r['reason'] for r in reviews})

    def test_inferred_ocr_table_not_strong_enough(self):
        facts, reviews, _ = app.local_extract(Path('NHP_4000_5000.pdf'),
            comparative_table(inferred=True), rechecker=NoOCR())
        self.assertFalse(any(f['parameter']=='table_load_kg' for f in facts))
        self.assertTrue(any(r['reason']=='tool_capacity_unit_conflict' for r in reviews))

    def test_missing_merged_geometry_does_not_borrow_column(self):
        facts, _, _ = app.local_extract(Path('NHP_4000_5000.pdf'),
            comparative_table(shared=False), rechecker=NoOCR())
        loads={f['model']:f['value'] for f in facts if f['parameter']=='table_load_kg'}
        self.assertEqual(set(loads), {'NHP 4000','NHP 5000'})

    def test_series_exports_and_field_status(self):
        def fact(model, filename, key, value):
            return {'model':model,'source_file':filename,'source_page':17,
                'source_table':2,'source_row':8,'source_row_id':'17:2:8',
                'parameter':key,'value':value,'method':'local_table:explicit_table_header',
                'review_status':'recovered_needs_review' if key=='table_load_kg' else 'needs_review',
                'recovery_note':'table_load_context_mass_pair_recovered' if key=='table_load_kg' else ''}
        with tempfile.TemporaryDirectory() as temp:
            original = app.OUTPUT
            try:
                app.OUTPUT=Path(temp)
                app.write_outputs([
                  {'source_file':'NHP_4000_5000.pdf',
                   'registry':{'NHP 4000':{'evidence':'native_table_header'},
                               'NHP 5000':{'evidence':'native_table_header'}},
                   'facts':[fact('NHP 4000','NHP_4000_5000.pdf','table_load_kg','400 (881.8)')],
                   'review':[{'source_file':'NHP_4000_5000.pdf', 'models_context':'NHP 5000',
                              'model':'NHP 5000', 'row_id':'17:2:8',
                              'reason':'tool_capacity_unit_conflict','parameter':'tool_capacity',
                              'suggested_parameter':'table_load_kg'}]},
                  {'source_file':'SVM-series.pdf',
                   'registry':{'SVM 4100':{'evidence':'native_table_header'}},
                   'facts':[fact('SVM 4100','SVM-series.pdf','table_t_slot','T-SLOT')],
                   'review':[]}])
                with (Path(temp)/'MODEL_FIELD_STATUS.csv').open(encoding='utf-8-sig',newline='') as h:
                    rows=list(csv.DictReader(h))
                statuses={(r['model'],r['parameter']):r['status'] for r in rows}
                self.assertEqual(statuses['NHP 4000','table_load_kg'],'RECOVERED_REVIEW_REQUIRED')
                self.assertEqual(statuses['NHP 5000','table_load_kg'],'REVIEW_REQUIRED')
                self.assertEqual(statuses['SVM 4100','table_load_kg'],'NOT_LISTED_FOR_SERIES')
                self.assertEqual(statuses['NHP 4000','table_t_slot'],'NOT_LISTED_FOR_SERIES')
                with (Path(temp)/'by_series/NHP_models.csv').open(encoding='utf-8-sig',newline='') as h:
                    nhp=list(csv.DictReader(h))
                self.assertEqual(len(nhp),2)
                self.assertTrue(any('table_load_kg' in k for k in nhp[0]))
                self.assertFalse(any('table_t_slot' in k for k in nhp[0]))
                self.assertTrue((Path(temp)/'by_series/SVM_models.csv').is_file())
                self.assertEqual(json.loads((Path(temp)/'RUN_SUMMARY.json').read_text())['network_calls'],0)
            finally:
                app.OUTPUT=original

if __name__=='__main__':
    unittest.main()
