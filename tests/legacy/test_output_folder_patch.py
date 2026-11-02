"""Output-folder patch: independent runs and requested narrow wide CSV.

Uses fabricated facts and temp paths; does not read the user's source documents.
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import compat_v842 as app


class TestVersionedOutput(unittest.TestCase):
    def test_unique_run_folder_and_portable_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / 'pdf'
            pdf.mkdir()
            (pdf / 'smoke.txt').write_text('No structured specifications on this page.', encoding='utf-8')
            before = app.BASE, app.INPUT, app.OUTPUT_BASE, app.OUTPUT, app.CACHE
            try:
                app.BASE = root
                app.INPUT = pdf
                app.OUTPUT_BASE = root / 'output' / 'v8_4_2'
                app.OUTPUT = app.new_run_directory(app.OUTPUT_BASE, stamp='same')
                app.CACHE = root / 'output' / 'cache_v8_4_2'
                app.main()
                first = app.OUTPUT
                self.assertTrue((first / 'RUN_MANIFEST.json').exists())
                self.assertTrue((first / 'ALL_MODELS_wide.csv').exists())
                app.main()
                second = app.OUTPUT
                self.assertNotEqual(first, second)
                self.assertTrue((first / 'RUN_MANIFEST.json').exists())
                self.assertTrue((second / 'RUN_MANIFEST.json').exists())
                self.assertEqual((app.OUTPUT_BASE / 'LATEST_RUN.txt').read_text(encoding='utf-8').strip(),
                                 str(second.relative_to(root)))
                meta = json.loads((second / 'RUN_MANIFEST.json').read_text(encoding='utf-8'))
                self.assertEqual(meta['project_version'], '8.4.2')
                self.assertEqual(meta['folder_in_project'], str(second.relative_to(root)))
            finally:
                app.BASE, app.INPUT, app.OUTPUT_BASE, app.OUTPUT, app.CACHE = before

    def test_only_wide_drops_eight_columns(self):
        fact = {'model': 'NHP 4000', 'source_file': 'catalogue.pdf',
                'source_page': 1, 'source_table': 1, 'source_row': 1,
                'source_row_id': '1:1:1', 'source_label': 'Taper',
                'parameter': 'spindle_taper', 'value': 'ISO #40',
                'method': 'local_table:explicit_table_header', 'review_status': 'needs_review'}
        speed = dict(fact, parameter='spindle_speed_rpm', value='8000', source_label='Speed')
        with tempfile.TemporaryDirectory() as temp:
            original = app.OUTPUT
            try:
                app.OUTPUT = Path(temp)
                app.write_outputs([{'source_file': 'catalogue.pdf',
                                    'registry': {'NHP 4000': {'evidence': 'native_table_header'}},
                                    'facts': [fact, speed], 'review': []}])
                def load(name):
                    with (Path(temp) / name).open(encoding='utf-8-sig', newline='') as stream:
                        reader = csv.DictReader(stream)
                        return reader.fieldnames, list(reader)
                columns, wide = load('ALL_MODELS_wide.csv')
                self.assertFalse(set(columns) & app.HIDDEN_WIDE_COLUMNS)
                self.assertEqual(wide[0]['[Spindle] spindle_speed_rpm'], '8000')
                long_columns, long_rows = load('ALL_MODELS_long.csv')
                self.assertIn('spindle_taper', {r['parameter'] for r in long_rows})
                series_columns, _ = load('by_series/NHP_models.csv')
                self.assertIn('[Spindle] spindle_taper', series_columns)
                status_columns, status_rows = load('MODEL_FIELD_STATUS.csv')
                self.assertIn('spindle_taper', {r['parameter'] for r in status_rows})
                summary = json.loads((Path(temp) / 'RUN_SUMMARY.json').read_text(encoding='utf-8'))
                self.assertEqual(set(summary['hidden_wide_columns']), app.HIDDEN_WIDE_COLUMNS)
            finally:
                app.OUTPUT = original


if __name__ == '__main__':
    unittest.main()
