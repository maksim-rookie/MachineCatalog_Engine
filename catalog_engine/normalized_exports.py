"""Export canonical records, complete display tables, provenance and audits.

This module owns table assembly. Physical normalization lives in canonical.py;
file replacement lives in storage.py. A rejected conversion remains visible.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import logging
from pathlib import Path
from .canonical import ParameterCatalog, format_signature, normalize_fact, source_record
from .report_layout import ordered_columns
from .storage import atomic_json, write_csv

LOGGER = logging.getLogger(__name__)
IDENTITY = ['manufacturer', 'machine_class', 'machine_type', 'model', 'base_model',
            'model_variant', 'generation', 'source_file', 'configuration']
LONG_FIELDS = [
    'source_fact_id', 'category', 'canonical_parameter', 'canonical_unit',
    'normalized_value', 'display_value', 'normalization_status', 'normalization_method',
    'source_label', 'source_value', 'source_value_raw', 'source_unit', 'source_unit_expected',
    'source_file', 'source_sha256', 'source_page', 'source_table', 'source_row_id',
    'source_model', 'configuration', 'cell_bbox', 'source_parameter', 'condition',
    'condition_data', 'duty_cycle', 'availability', 'options', 'normalized_options',
    'warnings', 'is_component', 'human_verified',
]


def normalize_documents(documents: list[dict], schema) -> tuple[list[dict], list[dict]]:
    catalog = ParameterCatalog(schema)
    records, errors = [], []
    for document in documents:
        if not isinstance(document.get('facts'), list) or not isinstance(document.get('registry'), dict):
            raise ValueError(f"Invalid document structure: {document.get('source_file', '<unknown>')}")
        for fact in document['facts']:
            try:
                records.extend(normalize_fact(fact, catalog, document.get('source_sha256', '')))
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                if not isinstance(fact, dict):
                    raise ValueError(f"Non-object fact in {document['source_file']}") from exc
                LOGGER.exception('Normalization rejected fact %s in %s', fact.get('fact_id'), document['source_file'])
                errors.append({'source_file': document['source_file'], 'source_fact_id': fact.get('fact_id', ''),
                               'error_type': type(exc).__name__, 'error': str(exc)})
                records.append({**source_record(fact, document.get('source_sha256', '')),
                    'canonical_parameter': fact.get('parameter') or 'unmapped_parameter',
                    'canonical_unit': '', 'normalized_value': '',
                    'display_value': str(fact.get('value', '')) + ' [ОШИБКА НОРМАЛИЗАЦИИ]',
                    'normalization_status': 'INVALID_FACT', 'normalization_method': 'source_preserved',
                    'category': fact.get('category', 'Other'), 'is_component': False})
        seen_blanks = set()
        for cell in document.get('cell_statuses', []):
            if cell.get('status') != 'SOURCE_BLANK' or not cell.get('parameter'):
                continue
            identity = (cell.get('model'), cell.get('configuration', 'base'),
                        cell.get('source_row_id'), cell['parameter'])
            if identity in seen_blanks:
                continue
            seen_blanks.add(identity)
            key = catalog.key(cell['parameter'])
            records.append({**source_record(cell, document.get('source_sha256', '')),
                'canonical_parameter': key, 'canonical_unit': catalog.unit(key, cell),
                'category': catalog.category(key, cell), 'normalized_value': '',
                'display_value': '[ПУСТАЯ ЯЧЕЙКА В PDF]', 'normalization_status': 'SOURCE_BLANK',
                'normalization_method': 'source_blank', 'is_component': False})
    return records, errors


def write_audit(output: Path, records: list[dict]) -> int:
    groups = defaultdict(list)
    variants = {}
    for record in records:
        groups[record['canonical_parameter']].append(record)
        signature = format_signature(str(record.get('source_value', '')))
        key = (record['canonical_parameter'], record['source_file'], record['source_label'],
               record['source_parameter'], record['source_unit'], signature)
        item = variants.setdefault(key, {'canonical_parameter': key[0], 'source_file': key[1],
            'source_label': key[2], 'source_parameter': key[3], 'source_unit': key[4],
            'value_format': signature, 'canonical_unit': record['canonical_unit'], 'fact_ids': set()})
        item['fact_ids'].add(record['source_fact_id'])
    rows = []
    for parameter, items in sorted(groups.items()):
        labels = sorted({r['source_label'] for r in items})
        units = sorted({r['source_unit'] for r in items})
        expected = sorted({r['source_unit_expected'] for r in items})
        parameters = sorted({r['source_parameter'] for r in items})
        formats = sorted({format_signature(str(r['source_value'])) for r in items})
        if any(len(values) > 1 for values in (labels, units, expected, parameters, formats)):
            rows.append({'canonical_parameter': parameter, 'canonical_units': sorted({r['canonical_unit'] for r in items}),
                'source_labels': labels, 'source_units': units, 'expected_units_in_source_schema': expected,
                'source_parameters': parameters, 'value_formats': formats,
                'fact_count': len({r['source_fact_id'] for r in items}),
                'source_files': sorted({r['source_file'] for r in items})})
    fields = ['canonical_parameter', 'canonical_units', 'source_labels', 'source_units',
              'expected_units_in_source_schema', 'source_parameters', 'value_formats', 'fact_count', 'source_files']
    write_csv(output / 'NORMALIZATION_AUDIT.csv', rows, fields)
    variant_rows = [{**r, 'fact_count': len(r['fact_ids']), 'fact_ids': sorted(r['fact_ids'])} for r in variants.values()]
    write_csv(output / 'NORMALIZATION_VARIANTS.csv', variant_rows,
              ['canonical_parameter', 'canonical_unit', 'source_parameter', 'source_file',
               'source_label', 'source_unit', 'value_format', 'fact_count', 'fact_ids'])
    return len(rows)


def column_name(record: dict) -> str:
    return f"[{record['category']}] {record['canonical_parameter']}"


def unique_join(values) -> str:
    return ' || '.join(dict.fromkeys(value for value in values if value))


def write_wide_tables(output: Path, records: list[dict], documents: list[dict], schema) -> dict:
    # Axis groups retain their original record in the long table; the wide view
    # uses shared per-axis columns instead of a second vendor-specific XYZ column.
    component_ids = {r['source_fact_id'] for r in records if r.get('is_component')}
    from .logical_fields import REMOVED
    selected = [r for r in records if (r.get('is_component') or r['source_fact_id'] not in component_ids) and r.get('source_parameter') not in REMOVED and r['canonical_parameter'] not in REMOVED]
    columns = ordered_columns([column_name(r) for r in selected], schema)
    index = defaultdict(list)
    column_units = defaultdict(set)
    entities = {}
    for document in documents:
        for model, metadata in sorted(document['registry'].items()):
            for configuration in metadata.get('configurations') or ['base']:
                key = (document['source_file'], model, configuration)
                entities[key] = {name: metadata.get(name, document.get(name, '')) for name in IDENTITY}
                entities[key].update(source_file=key[0], model=model, configuration=configuration)
    for record in selected:
        entity = (record['source_file'], record['source_model'], record['configuration'])
        if entity not in entities:
            LOGGER.warning('Fact entity missing from registry: %r', entity)
            entities[entity] = dict(source_file=entity[0], model=entity[1], configuration=entity[2])
        index[(*entity, column_name(record))].append(record)
        column_units[column_name(record)].add(record['canonical_unit'])
    displays, values, statuses, provenance = [], [], [], []
    for entity, identity in entities.items():
        display, value, status, sources = (dict(identity) for _ in range(4))
        for column in columns:
            items = index.get((*entity, column), [])
            display[column] = unique_join(r['display_value'] for r in items)
            value[column] = unique_join(r['normalized_value'] for r in items
                if r['availability'] != 'optional_only' and not r.get('options') and not r.get('normalized_options') and not r.get('condition') and not r.get('duty_cycle'))
            status[column] = unique_join(r['normalization_status'] for r in items) or 'NOT_PRESENT_IN_EXTRACTED_DATA'
            sources[column] = display[column]
            sources[column + '__unit'] = unique_join(r['canonical_unit'] for r in items if r['normalized_value'])
            sources[column + '__source_unit'] = unique_join(r['source_unit'] for r in items)
            sources[column + '__source_value'] = unique_join(str(r['source_value_raw']) for r in items)
            sources[column + '__source_ref'] = unique_join(
                f"{r['source_file']}:page={r['source_page']}:row={r['source_row_id']}:fact={r['source_fact_id']}" for r in items)
            sources[column + '__status'] = status[column]
        displays.append(display); values.append(value); statuses.append(status); provenance.append(sources)
    write_csv(output / 'ALL_MODELS_NORMALIZED_wide.csv', displays, IDENTITY + columns)
    write_csv(output / 'ALL_MODELS_NORMALIZED_values.csv', values, IDENTITY + columns)
    write_csv(output / 'NORMALIZED_CELL_STATUS.csv', statuses, IDENTITY + columns)
    detailed_columns = [name for column in columns for name in
                        (column, column + '__unit', column + '__source_value', column + '__source_unit', column + '__source_ref', column + '__status')]
    write_csv(output / 'ALL_MODELS_NORMALIZED_with_sources.csv', provenance, IDENTITY + detailed_columns)
    write_csv(output / 'NORMALIZED_PARAMETER_DICTIONARY.csv', [
        {'column': column, 'canonical_unit': unique_join(sorted(column_units[column]))}
        for column in columns], ['column', 'canonical_unit'])
    return {'wide_rows': len(displays), 'wide_parameter_columns': len(columns)}


def write_normalized_outputs(output: Path, documents: list[dict], schema) -> dict:
    LOGGER.info('Normalizing %d document(s)', len(documents))
    records, errors = normalize_documents(documents, schema)
    write_csv(output / 'NORMALIZED_PARAMETERS.csv', records, LONG_FIELDS)
    write_csv(output / 'NORMALIZATION_ERRORS.csv', errors, ['source_file', 'source_fact_id', 'error_type', 'error'])
    discrepancy_count = write_audit(output, records)
    summary = write_wide_tables(output, records, documents, schema)
    original_ids = {f['fact_id'] for d in documents for f in d['facts'] if isinstance(f, dict) and f.get('fact_id')}
    exported_ids = {r['source_fact_id'] for r in records}
    summary.update(source_facts=len(original_ids), normalized_records=len(records),
        facts_without_export=len(original_ids - exported_ids),
        populated_source_records_without_display=sum(bool(r['source_value']) and not r['display_value'] for r in records),
        status_counts=dict(Counter(r['normalization_status'] for r in records)),
        discrepancy_parameters=discrepancy_count, normalization_errors=len(errors))
    atomic_json(output / 'NORMALIZATION_SUMMARY.json', summary)
    LOGGER.info('Normalization finished: %s', summary)
    return summary
