# MachineCatalog Engine 4.8.5.4

| Module | Responsibility | Extension point |
|---|---|---|
| geometry.py / extract.py | PDF tables, source text, coordinates, explicit model assignment | Geometry adapters |
| schema.py / config/parameter_synonyms.json | Recognize multilingual labels | Scoped label aliases |
| units.py | Unit vocabulary, dimensional checks, conversion scales | Supported units |
| canonical.py | Pure normalization of a fact; options, conditions and fallbacks | Explicit expression parsers |
| config/normalization.json | Canonical parameter names, units and text aliases | Declarative normalization rules |
| normalized_exports.py | Complete tables, source/status/value views, audit | Export formats |
| exports.py | Existing detailed, family and quality reports | Legacy-compatible views |
| replay.py / reexport.py | Reload evidence, rematch previously assigned unmapped cells | Recorded-run recovery |
| storage.py | Atomic artifact replacement and cache identities | Storage policies |
| quality.py | Disclosure requirements and conflicting facts | Validation rules |
| runner.py / main.py | Coordination, cache, progress, checkpoints and diagnostics | CLI options |

`normalized_value` is an accepted transformation. `display_value` remains populated when conversion is rejected; its status identifies the original representation. A complete display does not imply that every measurement is validated.

Every record retains its source fact ID, original label/value/unit, PDF hash, page/row and coordinates. Conditional/optional measurements remain labeled. Unknown model applicability is not inferred from neighboring models.

Wide tables share per-axis columns for explicitly declared XYZ groups. The strict values view omits disputed, optional and conditional measurements. The status view distinguishes source blanks from fields absent in extracted data.

Before exporting, the runner saves extraction reports and raw/source evidence. Atomic writes prevent partial files. A failed export leaves a replayable checkpoint and tracebacks in RUN_DEBUG.log/RUN_ABORTED.json. Rebuild it using `python reexport.py --run PATH_TO_RUN`.

Facts, units, model/configuration entities and cell statuses use single-pass indexes. Wide assembly costs O(models × columns + facts), including the rectangular CSV output. Unavailable source data is not fabricated.
