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
            self.assertEqual((first/'CATALOG.json').read_bytes(),
                             (second/'CATALOG.json').read_bytes())
            self.assertFalse(list(first.rglob('*.csv')))
            dataset = json.loads((second/'CATALOG.json').read_text())
            row = dataset['rows'][0]['cells']
            self.assertEqual(row['cutting_feed_xyz_mm_min']['display'], '!- X: 1500/Y: !-/Z: !- мм/мин')
            self.assertEqual(row['rapid_traverse_xyz_m_min']['display'], '!- X: 24; Y: !-; Z: !- м/мин')
            self.assertEqual(row['machine_dimensions_mm']['display'], '!- 2000 × 1800 × 2200 мм')
            self.assertEqual(row['table_size_mm']['display'], '!- Стол: 600 × 400 мм')
            fact_index = {f['fact_id']: f for f in dataset['facts']}
            feed = fact_index[row['cutting_feed_xyz_mm_min']['source_fact_ids'][0]]
            self.assertEqual(feed['value_raw'], '1.5')
            self.assertEqual(feed['source_page'], 1)


    def test_headerless_table_without_description_or_unit_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'input'; source.mkdir()
            pdf=pymupdf.open(); page=pdf.new_page(width=650,height=700)
            page.insert_text((40,35),'Machine Specification')
            page.insert_text((280,75),'MC200')
            page.insert_text((490,75),'MC1000')
            rows=[('Accuracy','','',''),('X/Y/Z position accuracy','mm(in)','0.002/0.002/0.002','0.003/0.003/0.003'),
                  ('Travel','','',''),('X/Y/Z','mm(in)','500/400/300','600/500/400'),
                  ('Table','','',''),('Table size','mm(in)','600x400','700x500'),
                  ('Max. load','kg(lb)','300 (661.5)','400 (882)'),
                  ('Feed','','',''),('X/Y/Z feed','m/min(in/min)','10 (393)','12 (472)'),
                  ('X/Y/Z rapid traverse','m/min(in/min)','15/15/15 (590/590/590)','18 (708)'),
                  ('Spindle','','',''),('Spindle speed','(r/min)','28,000','24,000'),
                  ('Tool magazine','','',''),('Max.capacity','','20','32'),
                  ('Machine weight','kg(lb)','4,600 (10,143)','5,500 (12,128)')]
            for i,(label,unit,a,b) in enumerate(rows):
                y=100+i*27
                for x,value in [(40,label),(160,unit),(230,a),(440,b)]:
                    page.insert_text((x,y),value,fontsize=8)
            pdf.save(source/'headerless.pdf'); pdf.close()
            out,summary,code=run(Settings(source,root/'out',root/'cache',export_csv=True))
            self.assertEqual(code,0); self.assertEqual(summary['models'],2)
            d=json.loads((out/'CATALOG.json').read_text())
            self.assertEqual([r['model'] for r in d['rows']],['MC200','MC1000'])
            c=d['rows'][0]['cells']
            self.assertEqual(c['travel_x_mm']['value'],'500')
            self.assertEqual(c['travel_z_mm']['value'],'300')
            self.assertEqual(c['cutting_feed_xyz_mm_min']['numeric_components'],[10000,10000,10000])
            self.assertEqual(c['rapid_traverse_xyz_m_min']['numeric_components'],[15,15,15])
            measurement=next(m for m in d['measurements'] if m['numeric_value']==28000)
            self.assertEqual(measurement['numeric_unit'],'rpm')
            self.assertEqual([p.name for p in out.rglob('*.csv')],['CATALOG.csv'])

    def test_corrupt_fact_in_cache_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'input'; source.mkdir()
            (source/'a.json').write_text('[{"model":"MC2","parameter":"spindle_speed_rpm","value":0,"unit":"rpm"}]')
            settings=Settings(source,root/'out',root/'cache')
            run(settings)
            cache_file=next((root/'cache').glob('*.json'))
            envelope=json.loads(cache_file.read_text())
            del envelope['document']['facts'][0]['parameter']
            cache_file.write_text(json.dumps(envelope))
            out,summary,code=run(settings)
            self.assertEqual(code,0); self.assertEqual(summary['cache_hits'],0)
            timing=json.loads((out/'RUN_TIMING.json').read_text())
            self.assertIn('Cache contains invalid fact',timing['file_timings'][0]['cache_rebuilt_reason'])


if __name__ == '__main__':
    unittest.main()
