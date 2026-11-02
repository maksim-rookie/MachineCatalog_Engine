# MachineCatalog Engine 4.8.5.3

## Cross-catalog normalization

- Added `NORMALIZED_PARAMETERS.csv`: canonical parameter, unit and value beside original label, value, unit and source coordinates.
- Added `ALL_MODELS_NORMALIZED_wide.csv`: a shared column schema across source catalogs; explicit XYZ triplets expand into the same X/Y/Z fields used by axis-by-axis catalogs.
- Added `NORMALIZATION_AUDIT.csv`: parameter keys with source-label, unit or value-format variation, with examples and counts.
- Ambiguous or blocking-warning values stay in source fields and remain blank in normalized fields for review.
- Existing raw and provenance exports remain intact.
