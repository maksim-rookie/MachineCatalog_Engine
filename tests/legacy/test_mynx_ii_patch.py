"""Regression of the MYNX II model identity and source-verified table reconstruction."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import compat_v842 as app

class MynxIIPatchTests(unittest.TestCase):
    def test_seven_distinct_model_headers(self):
        names = ['MYNX 5400II', 'MYNX 5400/50II', 'MYNX 6500II',
                 'MYNX 6500/50II', 'MYNX 7500II', 'MYNX 7500/50II', 'MYNX 9500']
        header = ' '.join('Mynx '+n.split()[1].replace('II', 'Ⅱ') for n in names)
        self.assertEqual(app.candidate_models(header), names)
        self.assertEqual(app.candidate_models('DNM 5700L NHP 5000S'), ['DNM 5700L','NHP 5000S'])
        self.assertNotEqual(app.candidate_models('Mynx 5400Ⅱ'), app.candidate_models('Mynx 5400/50Ⅱ'))
        self.assertEqual(app.model_info('MYNX 9500'), ('DN Solutions', 'Vertical Machining Center'))

    def test_source_geometry_reconstruction(self):
        path=app.INPUT/'dn-solutions-vertical-machining-center-mynx-ii-series.pdf'
        if not path.is_file():
            self.skipTest('Optional catalogue PDF not packaged')
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            table=pdf.pages[17].find_tables()[0]
            reconstructed=app.rectify_damaged_native_table(pdf.pages[17],table,1,18)
        self.assertIsNotNone(reconstructed)
        self.assertEqual(len(app.table_headers(reconstructed,'')),7)
        registry={n:{'evidence':'native_table_header'} for n in [
            'MYNX 5400II','MYNX 5400/50II','MYNX 6500II','MYNX 6500/50II',
            'MYNX 7500II','MYNX 7500/50II','MYNX 9500']}
        facts,review=app.recover_source_aligned_mynx_ii(path,registry)
        self.assertGreaterEqual(len(facts),230)
        self.assertEqual({f['model'] for f in facts},set(registry))
        ix={(f['model'],f['parameter'],f['condition']):f['value'] for f in facts}
        self.assertEqual(ix['MYNX 9500','travel_x_mm',''],'2500 (98.4)')
        self.assertEqual(ix['MYNX 9500','machine_weight_kg',''],'23000 (50705.6)')
        self.assertEqual(ix['MYNX 5400II','tool_capacity',''],'30 {40}')
        self.assertEqual(ix['MYNX 5400/50II','tool_capacity',''],'24')
        self.assertEqual(ix['MYNX 9500','rapid_traverse_z_m_min',''],'16')
        self.assertEqual(ix['MYNX 6500/50II','spindle_taper',''],'ISO #50')
        self.assertEqual(ix['MYNX 6500II','spindle_taper',''],'ISO #40')
        self.assertEqual(ix['MYNX 5400II','table_load_kg',''],'1000')
        self.assertTrue(any(x['reason']=='native_pdf_table_grid_repaired_from_words' for x in review))

    def test_other_files_do_not_activate_source_specific_fallback(self):
        self.assertIsNone(app.recover_source_aligned_mynx_ii(Path('missing.pdf'), {}))
        self.assertIsNone(app.recover_source_aligned_mynx_ii(Path('NHP_4000_5000.pdf'), {}))

if __name__=='__main__':
    unittest.main()
