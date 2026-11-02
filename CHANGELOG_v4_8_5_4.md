# 4.8.5.4

- Fixed derived columns erasing direct measurements for another model.
- Preserved text, ranges, private PDF bracket glyphs, options, blanks and ambiguous values with statuses.
- Separated unit vocabulary, pure normalization, table assembly and recorded-run recovery.
- Added declarative canonical rules in config/normalization.json.
- Unified mm/min and m/min cutting-feed fields; expanded explicit axes while retaining option scope.
- Preserved original text, units, fact IDs, PDF hashes and physical source-cell boxes.
- Recognized Recommanded turning dia.; replay recovers three physical cells/six model facts.
- Corrected physical coverage of explicitly inferred shared spans; missing boxes cannot prove coverage.
- Added atomic CSV/JSONL writes, extraction checkpoints and full debug tracebacks.
- Added reexport.py, source/status/value views, unit dictionary, exhaustive variant audit and completeness checks.
- Added 15 regressions covering data loss, incompatible units, options, invalid records, atomic writes and export-failure recovery.
