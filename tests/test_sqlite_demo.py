import json
from pathlib import Path
import tempfile
import unittest
from catalog_engine.sqlite_demo import connect, load, select_equipment

SOURCE = Path(__file__).resolve().parents[1]/'examples/demo_catalog.json'


class SqlDemoTests(unittest.TestCase):
    def test_repeat_load_and_three_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = connect(Path(tmp)/'demo.sqlite3')
            try:
                self.assertEqual(load(c,SOURCE),load(c,SOURCE))
                self.assertEqual(c.execute('SELECT count(*) FROM equipment').fetchone()[0],3)
                results=select_equipment(c,[{'parameter':'travel_x_mm','min':500,'unit':'mm'},
                                            {'parameter':'spindle_speed_rpm','min':10000,'unit':'rpm'}])
                self.assertEqual([r['result'] for r in results],['pass','fail','insufficient_data'])
                zero=select_equipment(c,[{'parameter':'spindle_speed_rpm','min':0,'unit':'rpm'}])
                self.assertEqual(zero[1]['result'],'pass')
            finally:
                c.close()

    def test_invalid_document_leaves_no_partial_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'demo.sqlite3')
            try:
                data=json.loads(SOURCE.read_text()); data['equipment'][1]['measurements'][0]['value']='bad'
                p=Path(tmp)/'bad.json'; p.write_text(json.dumps(data))
                with self.assertRaises(ValueError): load(c,p)
                self.assertEqual(c.execute('SELECT count(*) FROM documents').fetchone()[0],0)
            finally:
                c.close()

    def test_unit_mismatch_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=connect(Path(tmp)/'demo.sqlite3')
            try:
                load(c,SOURCE)
                result=select_equipment(c,[{'parameter':'travel_x_mm','min':1,'unit':'inch'}])
                self.assertTrue(all(r['result']=='insufficient_data' for r in result))
            finally:
                c.close()
