import csv
from pathlib import Path
import tempfile
import unittest
from catalog_engine.dimensions_view import merge_dimensions,finalize_wide_tables,COLUMN,NOTE,MISSING
from catalog_engine.storage import write_csv


def f(key='machine_dimensions_mm',value='2500 x 1500 x 1800',label='Machine dimensions',unit='mm',**extra):
    return dict(parameter=key,value=value,value_raw=value,source_label=label,unit_source=unit,
                model='M1',configuration='base',source_page=1,fact_id=key,**extra)


class DimensionsTests(unittest.TestCase):
    def test_general_only(self):self.assertEqual(merge_dimensions([f()])[0],'2500 × 1500 × 1800')
    def test_conversion(self):self.assertEqual(merge_dimensions([f(value='2.5 * 1.5 * 1.8',unit='m')])[0],'2500 × 1500 × 1800')
    def test_declared_order_is_rearranged(self):
        self.assertEqual(merge_dimensions([f(value='1500x2500x1800',label='Dimensions W × L × H')])[0],'1500 × 2500 × 1800')
    def test_three_known_roles(self):
        facts=[f(f'machine_dimension_{i}_mm',v,l) for i,v,l in [(1,'1800','Height'),(2,'2500','Length'),(3,'1500','Width')]]
        self.assertEqual(merge_dimensions(facts)[0],'1800 × 2500 × 1500')
    def test_three_explicit_order(self):
        facts=[f(f'machine_dimension_{i}_mm',v,'Dimensions LxWxH') for i,v in enumerate(['2500','1500','1800'],1)]
        self.assertEqual(merge_dimensions(facts)[0],'2500 × 1500 × 1800')
    def test_unknown_order(self):
        value,note=merge_dimensions([f('machine_dimension_1_mm','2500','Dimension 1')])
        self.assertEqual(value,'2500 × !- × !-');self.assertIn('2500',note)
    def test_partial_unverified(self):
        self.assertEqual(merge_dimensions([f('machine_dimension_1_mm','2500','Length'),f('machine_dimension_3_mm','1800','Height')])[0],'2500 × !- × 1800')
    def test_partial_verified_absence(self):
        self.assertEqual(merge_dimensions([f('machine_dimension_1_mm','2500','Length'),f('machine_dimension_3_mm','1800','Height')],lambda i:'-')[0],'2500 × - × 1800')
    def test_partial_general(self):self.assertEqual(merge_dimensions([f(value='2500 x - x 1800')])[0],'2500 × !- × 1800')
    def test_equal_sources_deduplicated(self):
        self.assertEqual(merge_dimensions([f(),f('machine_dimension_1_mm','2500','Length')])[0],'2500 × 1500 × 1800')
    def test_conflicts_preserve_both(self):
        value,note=merge_dimensions([f(),f('machine_dimension_1_mm','2600','Length')])
        self.assertEqual(value,'!-');self.assertIn('2500',note);self.assertIn('2600',note)
    def test_conflicting_general_values(self):self.assertEqual(merge_dimensions([f(),f(value='2600x1500x1800')])[0],'!-')
    def test_packaging_cannot_override_machine(self):
        self.assertEqual(merge_dimensions([f(),f(value='3000x2000x2100',label='Packing dimensions')])[0],'2500 × 1500 × 1800')
    def test_packaging_alone_is_not_machine_dimensions(self):
        self.assertEqual(merge_dimensions([f(label='Transport dimensions')])[0],'!-')
    def test_unknown_units_rejected(self):self.assertEqual(merge_dimensions([f(unit='')])[0],'!-')

    def test_csv_scope_notes_absence_and_other_fields(self):
        parts=[f'machine_dimension_{i}_mm' for i in (1,2,3)]
        fields=['source_file','model','untouched','[Spindle] a','[Spindle] b',COLUMN]+['[Machine dimensions] '+p for p in parts]
        doc=dict(source_file='x.pdf',registry={'M1':{}},facts=[f(source_file='x.pdf')],absence_verifications=[
            dict(model='M1',parameter='a',source_absence_verified=True,evidence='Source checked, page 1')])
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'ALL_MODELS_wide.csv'
            write_csv(path,[dict(source_file='x.pdf',model='M1',untouched='12.5 {30}',**{'[Spindle] a':MISSING,'[Spindle] b':MISSING})],fields)
            finalize_wide_tables(Path(td),[doc])
            with path.open(encoding='utf-8-sig') as stream:
                reader=csv.DictReader(stream);row=next(reader);cols=reader.fieldnames
            self.assertEqual(row['untouched'],'12.5 {30}')
            self.assertEqual(row['[Spindle] a'],'-');self.assertEqual(row['[Spindle] b'],'!-')
            self.assertEqual(row[COLUMN],'2500 × 1500 × 1800 мм');self.assertIn(NOTE,cols)
            self.assertFalse(any(p in c for p in parts for c in cols))


if __name__=='__main__':unittest.main()

class PartialSourceTests(unittest.TestCase):
    def test_length_width_pair_and_named_height(self):
        value,_=merge_dimensions([f(value='2500 x 1500 (98.4 x 59.1)',label='Length x Width',unit='mm (inch)'),f('machine_height_mm','1800','Height')])
        self.assertEqual(value,'2500 × 1500 × 1800')
    def test_pair_missing_height_is_unverified(self):
        self.assertEqual(merge_dimensions([f(value='2500x1500',label='Length x Width')])[0],'2500 × 1500 × !-')
