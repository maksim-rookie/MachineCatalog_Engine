"""End-to-end native PDF test, including cache reuse and actual CSV assertions."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

from catalog_engine.runner import Settings, run


class PdfWorkflowTests(unittest.TestCase):
    def test_native_extraction_projection_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'input'
            source.mkdir()
            pdf = pymupdf.open()
            page = pdf.new_page(width=650, height=750)
            page.insert_text((40, 35), 'Vertical machining center - Machine specifications')
            xs = [40, 360, 440, 610]
            rows = [
                ['Description', 'Unit', 'MC 1000'],
                ['Travel X-axis travel', 'mm', '500'],
                ['Travel Y-axis travel', 'mm', '400'],
                ['Travel Z-axis travel', 'mm', '300'],
                ['Rapid traverse X-axis', 'm/min', '24'],
                ['Cutting feed X-axis', 'm/min', '1.5'],
                ['Table size', 'mm', '600 x 400'],
                ['Spindle speed', 'rpm', '8000'],
                ['Tool storage capacity', 'pcs', '20'],
                ['Machine weight', 'kg', '4000'],
                ['Control system', '', 'FANUC'],
                ['Dimension 1', 'mm', '2000'],
                ['Dimension 2', 'mm', '1800'],
                ['Dimension 3', 'mm', '2200'],
            ]
            for i, row in enumerate(rows):
                y = 70 + i*32
                for j, text in enumerate(row):
                    page.draw_rect((xs[j], y, xs[j+1], y+32), width=.5)
                    page.insert_text((xs[j]+5, y+20), text, fontsize=9)
            pdf.save(source/'catalogue.pdf')
            pdf.close()
            settings = Settings(source, root/'output', root/'cache')
            first, summary, code = run(settings)
            self.assertEqual(code, 0)
            self.assertEqual(summary['facts'], 13)
            self.assertTrue(summary['extraction_gate_passed'])
            second, repeated, code = run(settings)
            self.assertEqual(code, 0)
            self.assertEqual(repeated['cache_hits'], 1)
            self.assertEqual((first/'ALL_MODELS_wide.csv').read_bytes(),
                             (second/'ALL_MODELS_wide.csv').read_bytes())
            with (second/'ALL_MODELS_wide.csv').open(encoding='utf-8-sig') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row['[Feedrate] cutting_feed_xyz_mm_min'], '!- X: 1500/Y: !-/Z: !- мм/мин')
            self.assertEqual(row['[Feedrate] rapid_traverse_xyz_m_min'], '!- X: 24; Y: !-; Z: !- м/мин')
            self.assertEqual(row['[Machine dimensions] machine_dimensions_mm'], '!- 2000 × 1800 × 2200 мм')
            self.assertEqual(row['[Table] table_size_mm'], '!- Стол: 600 × 400 мм')
            audit = json.loads((second/'TRANSFORMATION_AUDIT.json').read_text())
            feed = next(r for r in audit if r['parameter'] == 'cutting_feed_xyz_mm_min')
            self.assertEqual(feed['source_facts'][0]['value_raw'], '1.5')
            self.assertEqual(feed['source_facts'][0]['source_page'], 1)


if __name__ == '__main__':
    unittest.main()
