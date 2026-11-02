# Windows UTF-8 fix

`FIELD_DISPLAY.json` already uses UTF-8. The test previously called `Path.read_text()` without encoding, allowing Windows CP1251 to garble the Russian legend. The test now uses `read_text(encoding="utf-8")`, as do the other affected test reads. Production data and serialization logic are unchanged.

A regression test simulates a CP1251 default and runs the full affected integration test. Validation on Linux: 116 tests discovered, 102 passed, 14 optional PDF tests skipped; no failures. Native Windows execution has not been performed.
