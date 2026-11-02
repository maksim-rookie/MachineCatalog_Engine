"""Simulate the Windows cp1251 default without requiring a Windows host."""
from pathlib import Path
import unittest
from unittest.mock import patch


class WindowsEncodingTests(unittest.TestCase):
    def test_legend_integration_with_cp1251_default(self):
        from tests.test_logical_fields import CompatibilityTests
        original=Path.read_text
        def windows_read(path,encoding=None,errors=None):
            return original(path,encoding=encoding or 'cp1251',errors=errors)
        with patch.object(Path,'read_text',windows_read):
            case=CompatibilityTests('test_tables_one_field_legend_and_repeatable')
            result=unittest.TestResult();case.run(result)
        self.assertTrue(result.wasSuccessful(),str(result.errors+result.failures))


if __name__=='__main__':unittest.main()
