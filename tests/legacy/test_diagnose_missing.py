"""Only checks benchmark diagnostics; never changes extracted facts."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / 'benchmarks'))
import diagnose_missing as diag

class TestMissingDiagnostics(unittest.TestCase):
    def test_joins_same_model_and_parameter(self):
        benchmark=[{'model':'SVM 4100','parameter':'spindle_taper',
                    'condition':'','reference_page':'16','reference_value':'ISO #40'}]
        review=[{'model':'SVM 4100','models_context':'SVM 4100',
                 'parameter':'spindle_taper','source_page':16,
                 'reason':'spindle_taper_ocr_uncertain',
                 'row_id':'16:1:10', 'cells':['Taper','','','150 #40','ISO #40']}]
        rows=diag.make_audit(benchmark,[],review)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['candidate_1_reason'],'spindle_taper_ocr_uncertain')
        self.assertEqual(rows[0]['candidate_1_row_id'],'16:1:10')
    def test_does_not_borrow_other_model_or_overwrite(self):
        benchmark=[{'model':'SVM 4100','parameter':'coolant_pump_motor_power_kw',
                    'condition':'','reference_page':'16','reference_value':'1.1'}]
        observed=[{'model':'SVM 5100L','parameter':'coolant_pump_motor_power_kw',
                   'condition':'','value':'0.4'}]
        review=[{'model':'SVM 5100L','models_context':'SVM 5100L',
                 'parameter':'coolant_pump_motor_power_kw', 'reason':'missing_model_value'}]
        rows=diag.make_audit(benchmark,observed,review)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['issue_found'],'no')
    def test_trusts_suggestion_as_candidate_only(self):
        benchmark=[{'model':'NHP 4000','parameter':'table_load_kg',
                    'condition':'','reference_page':'17','reference_value':'400'}]
        review=[{'model':'','models_context':'NHP 4000, NHP 5000',
                 'parameter':'tool_capacity', 'suggested_parameter':'table_load_kg',
                 'source_page':17, 'reason':'tool_capacity_unit_conflict'}]
        rows=diag.make_audit(benchmark,[],review)
        self.assertEqual(rows[0]['issue_found'],'yes')
        self.assertEqual(rows[0]['candidate_1_reason'],'tool_capacity_unit_conflict')
