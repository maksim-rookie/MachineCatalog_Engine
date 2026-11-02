"""Progress reporting behavior and timing artifacts for v8.4.2 PATCH3."""
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import compat_v842 as app


class TestProgressPatch(unittest.TestCase):
    def test_monotone_progress_and_timing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            monitor = app.RunProgress(path, 2)
            monitor.file_work(0, 0.5, "Чтение", filename="a.pdf", detail="стр 3/6")
            monitor.file_work(0, 0.4, "Чтение", filename="a.pdf")
            monitor.file_work(1, 1.0, "Кэш", filename="b.pdf")
            monitor.update(96, "CSV")
            timing = monitor.finish()
            events = [json.loads(line) for line in (path / "RUN_PROGRESS.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["progress_percent"], 0)
            self.assertEqual(events[-1]["progress_percent"], 100)
            percentages = [e["progress_percent"] for e in events]
            self.assertEqual(percentages, sorted(percentages))
            self.assertEqual(events[1]["source_file"], "a.pdf")
            self.assertIn("файл 50%", events[1]["detail"])
            self.assertGreaterEqual(timing["elapsed_seconds"], 0)
            self.assertEqual(json.loads((path / "RUN_TIMING.json").read_text(encoding="utf-8"))["duration_hms"], timing["duration_hms"])

    def test_real_main_creates_timing_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / 'pdf'
            pdf.mkdir()
            (pdf / 'smoke.txt').write_text('A file with no model specs.\n', encoding='utf-8')
            original = app.BASE, app.INPUT, app.OUTPUT_BASE, app.OUTPUT, app.CACHE
            try:
                app.BASE = root
                app.INPUT = pdf
                app.OUTPUT_BASE = root / 'output' / 'v8_4_2'
                app.OUTPUT = app.new_run_directory(app.OUTPUT_BASE, stamp='progress')
                app.CACHE = root / 'output' / 'cache_v8_4_2'
                app.main()
                output = app.OUTPUT
                summary = json.loads((output / 'RUN_SUMMARY.json').read_text(encoding='utf-8'))
                timing = json.loads((output / 'RUN_TIMING.json').read_text(encoding='utf-8'))
                manifest = json.loads((output / 'RUN_MANIFEST.json').read_text(encoding='utf-8'))
                self.assertIn('RUN_TIMING.json', manifest['artifacts'])
                self.assertIn('RUN_PROGRESS.jsonl', manifest['artifacts'])
                self.assertEqual(summary['progress_log'], 'RUN_PROGRESS.jsonl')
                self.assertEqual(summary['timing_report'], 'RUN_TIMING.json')
                self.assertEqual(timing['files_total'], 1)
                self.assertEqual(timing['file_timings'][0]['source_file'], 'smoke.txt')
                self.assertEqual(timing['file_timings'][0]['status'], 'ok')
                self.assertFalse(timing['file_timings'][0]['cache_hit'])
                self.assertGreaterEqual(timing['elapsed_seconds'], 0)
                app.main()
                cached = json.loads((app.OUTPUT / 'RUN_TIMING.json').read_text(encoding='utf-8'))
                self.assertTrue(cached['file_timings'][0]['cache_hit'])
            finally:
                app.BASE, app.INPUT, app.OUTPUT_BASE, app.OUTPUT, app.CACHE = original


if __name__ == '__main__':
    unittest.main()
