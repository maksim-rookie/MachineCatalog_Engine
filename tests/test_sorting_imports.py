from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from catalog_engine.exports import build_dataset, write_outputs
from catalog_engine.imports import import_catalogue
from catalog_engine.presentation import project_cells
from catalog_engine.quality import check_document
from catalog_engine.runner import Settings, run
from catalog_engine.schema import Schema
from catalog_engine.sorting import category_key, natural_key, value_key, sort_model_rows
from catalog_engine.validation import DocumentValidationError, normalize_document


class SortingTests(unittest.TestCase):
    def test_natural_models_and_parameters(self):
        self.assertEqual(sorted(['MC100','MC20','MC2','MC10'], key=natural_key), ['MC2','MC10','MC20','MC100'])
        self.assertLess(natural_key('dimension_2'), natural_key('dimension_10'))

    def test_numeric_mixed_types_and_missing_last(self):
        items = [None, '!-', '100', 2.0, '', '-', 0, '10 mm', float('nan'), 'text']
        result = sorted(items, key=value_key)
        self.assertEqual(result[:5], [0, 2.0, '10 mm', '100', 'text'])
        self.assertEqual(result[5:9], [None, '!-', '', '-'])

    def test_category_hierarchy(self):
        self.assertEqual(sorted(['Spindle','Feedrate','Travel','Accuracy','Table'],key=category_key),
                         ['Accuracy','Travel','Table','Feedrate','Spindle'])

    def test_missing_metadata_and_decimal_values(self):
        self.assertEqual(sorted([None,'MC10','-','MC2'],key=natural_key),['MC2','MC10',None,'-'])
        self.assertEqual(sorted(['1,25','10','0.5'],key=value_key),['0.5','1,25','10'])

    def test_stability_and_parameter_sorting(self):
        rows = [{'model': str(i), 'cells': {'weight': {'value': v}}} for i,v in enumerate(['100','-','2','!-','0'])]
        result = sort_model_rows(rows, 'weight')
        self.assertEqual([r['model'] for r in result], ['4','2','0','1','3'])
        rows = [{'model':'MC2','source_file':'a','marker':i} for i in range(3)]
        self.assertEqual([r['marker'] for r in sort_model_rows(rows)], [0,1,2])


class ImportValidationTests(unittest.TestCase):
    def setUp(self):
        self.schema = Schema()

    def test_case_types_unknown_fields_and_invalid_rows(self):
        doc = {'source_file':'sample.json', 'facts':[
            {'MODEL':'MC2','Parameter':'SPINDLE_SPEED_RPM','Value':0,'Unit':'rpm'},
            {'model':'MC2','parameter':'new_custom_parameter','value':12},
            {'model':None,'parameter':'broken','value':1}, None], 'registry':{}}
        normalized = normalize_document(doc,self.schema)
        self.assertEqual(normalized['facts'][0]['value'], '0')
        self.assertIn('unknown_parameter',normalized['facts'][1]['warnings'])
        self.assertEqual(len(normalized['review']),2)
        self.assertEqual(normalize_document(normalized,self.schema),normalized)
        self.assertEqual(doc['facts'][0]['Value'],0)

    def test_bad_container_fails_with_actionable_error(self):
        for doc in ({}, {'facts':{}}, {'facts':[],'registry':[]}):
            with self.assertRaises(DocumentValidationError):
                normalize_document(doc,self.schema)

    def test_import_long_json_wide_csv_and_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'long.json').write_text(json.dumps([{'MODEL':'MC2','Parameter':'spindle_speed_rpm','Value':0,'Unit':'rpm'}, {'model':'MC10','parameter':'x','value':None}]))
            (root/'wide.csv').write_text('Model;SPINDLE_SPEED_RPM;custom_value\nMC2;0;test\n')
            import openpyxl
            wb=openpyxl.Workbook(); ws=wb.active
            ws.append(['Model','Spindle_Speed_Rpm']); ws.append(['MC2',0]); wb.save(root/'wide.xlsx')
            for name in ('long.json','wide.csv','wide.xlsx'):
                d=import_catalogue(root/name,self.schema)
                fact=next(f for f in d['facts'] if f['parameter']=='spindle_speed_rpm')
                self.assertEqual(fact['value'],'0')
                self.assertEqual(d['input_kind'],'structured')

    def test_excel_notes_sheet_does_not_discard_valid_data(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'book.xlsx'; wb=openpyxl.Workbook(); ws=wb.active
            ws.append(['MODEL','spindle_speed_rpm']); ws.append(['MC2',0])
            notes=wb.create_sheet('Notes'); notes.append(['Notes']); notes.append(['Example'])
            wb.save(p)
            doc=import_catalogue(p,self.schema)
            self.assertEqual(len(doc['facts']),1)
            self.assertEqual(doc['facts'][0]['source_sheet'],'Sheet')
            self.assertEqual(doc['review'][0]['reason'],'invalid_sheet')

    def test_duplicate_csv_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.csv'; p.write_text('Model,MODEL,spindle_speed_rpm\nMC2,MC3,1\n')
            with self.assertRaisesRegex(DocumentValidationError,'Duplicate'):
                import_catalogue(p,self.schema)

    def test_missing_long_value_column_is_diagnosed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.json'; p.write_text('[{"model":"MC2","parameter":"spindle_speed_rpm"}]')
            doc=import_catalogue(p,self.schema)
            self.assertFalse(doc['facts'])
            self.assertIn('value column',doc['review'][0]['instruction'])

    def test_structured_markers_and_zero(self):
        doc=normalize_document({'input_kind':'structured','source_file':'a.json','facts':[
            {'model':'MC2','parameter':'travel_x_mm','value':'-'},
            {'model':'MC2','parameter':'travel_y_mm','value':'!-'},
            {'model':'MC2','parameter':'travel_z_mm','value':0}]},self.schema)
        cells=project_cells(doc['facts'],doc['cell_statuses'])
        self.assertEqual(cells['travel_x_mm'].render(),'-')
        self.assertEqual(cells['travel_y_mm'].render(),'!-')
        self.assertEqual(cells['travel_z_mm'].value,'0')

    def test_quality_and_projection_are_idempotent(self):
        doc=normalize_document({'input_kind':'structured','source_file':'a.json','facts':[
            {'model':'MC2','parameter':'spindle_speed_rpm','value':0,'unit_source':'rpm'},
            {'model':'MC2','parameter':'spindle_speed_rpm','value':10,'unit_source':'rpm'}]},self.schema)
        q1=check_document(doc,self.schema); before=deepcopy(doc)
        q2=check_document(doc,self.schema)
        self.assertEqual(q1,q2); self.assertEqual(doc,before)
        dataset=build_dataset([doc],self.schema,[q1])
        self.assertEqual(build_dataset([doc],self.schema,[q2]),dataset)

    def test_invalid_file_does_not_stop_other_catalogues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'input'; source.mkdir()
            (source/'bad.json').write_text('{invalid json')
            (source/'valid.json').write_text('[{"model":"MC2","parameter":"spindle_speed_rpm","value":0,"unit":"rpm"}]')
            output, summary, code=run(Settings(source,root/'out',root/'cache'))
            self.assertEqual(code,1)
            self.assertEqual(summary['models'],1)
            self.assertTrue((output/'CATALOG.json').exists())
            self.assertFalse(list(output.rglob('*.csv')))
            self.assertEqual(summary['review_reason_counts']['processing_error'],1)

    def test_structural_ambiguities_are_reported_separately(self):
        doc=normalize_document({'input_kind':'structured','source_file':'a.json','facts':[
            {'model':'MC2','parameter':'rapid_traverse_x_m_min','value':10,'unit_source':'m/min'}]},self.schema)
        d=build_dataset([doc],self.schema,[])
        cell=d['rows'][0]['cells']['rapid_traverse_xyz_m_min']
        self.assertIn('partial_axes',cell['review_reasons'])
        self.assertTrue(cell['human_verification_pending'])
        self.assertGreater(d['summary']['cells_with_source_issues'],0)

    def test_logs_distinguish_data_issues_from_human_verification(self):
        doc=normalize_document({'input_kind':'structured','source_file':'a.json','facts':[
            {'model':'MC2','parameter':'spindle_speed_rpm','value':0,'unit_source':'rpm'}]},self.schema)
        with tempfile.TemporaryDirectory() as tmp, self.assertLogs('catalog_engine.exports',level='INFO') as capture:
            write_outputs(Path(tmp),[doc],self.schema,[])
        log=' '.join(capture.output)
        self.assertNotIn('need review',log)
        self.assertIn('not extraction errors',log)


if __name__ == '__main__':
    unittest.main()
