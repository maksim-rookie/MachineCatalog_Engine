"""Rebuild exports from recorded extraction, without re-reading PDF files.

Recovery is limited to previously assigned UNMAPPED_LABEL cells recognized by
an updated schema. This module never guesses model assignments or PDF text.
"""
from __future__ import annotations
import hashlib
import json
import logging
from pathlib import Path
from .geometry import norm
from .storage import atomic_json, write_jsonl
from .values import option_parts, split_embedded_conditions, value_warnings

LOGGER = logging.getLogger(__name__)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot read JSON file {path}: {exc}') from exc


def load_run(directory: Path) -> list[dict]:
    reports = sorted((directory / 'documents').glob('*_report.json'))
    if not reports:
        raise ValueError(f'No document reports in {directory / "documents"}')
    documents = [load_json(path) for path in reports]
    index = {}
    for document in documents:
        name = document.get('source_file')
        if not name or name in index:
            raise ValueError(f'Empty or duplicate source_file in document reports: {name}')
        if not isinstance(document.get('facts'), list) or not isinstance(document.get('registry'), dict):
            raise ValueError(f'Invalid facts or registry for {name}')
        index[name] = document
        document['raw_tables'] = []
        document['source_pages'] = []
    for filename, key in [('RAW_TABLES.jsonl', 'raw_tables'), ('SOURCE_PAGES.jsonl', 'source_pages')]:
        path = directory / filename
        if not path.is_file():
            raise ValueError(f'Replay requires provenance file {path}')
        with path.open(encoding='utf-8') as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    index[item['source_file']][key].append(item)
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f'Invalid provenance at {path}:{line_number}: {exc}') from exc
    return documents


def restore_source_boxes(document: dict) -> None:
    evidence = {(r.get('source_page'), r.get('source_row_id')): r['source_cell_bbox']
                for r in document.get('review', [])
                if r.get('reason') == 'shared_text_span_inferred' and len(r.get('source_cell_bbox', [])) == 4}
    for fact in document['facts']:
        if 'shared_text_span_inferred' in fact.get('warnings', []):
            box = evidence.get((fact.get('source_page'), fact.get('source_row_id')))
            if box:
                fact.setdefault('source_cell_bbox', box)


def recover_unmapped(document: dict, schema) -> list[dict]:
    """Rematch recorded cell statuses; preserve their explicit model and box."""
    recovered = []
    known_ids = {fact['fact_id'] for fact in document['facts']}
    for cell in document.get('cell_statuses', []):
        if cell.get('status') != 'UNMAPPED_LABEL' or not cell.get('value_raw', '').strip():
            continue
        labels = cell['source_label'].split(' / ')
        rule = schema.match(labels, cell.get('unit_source', ''))
        if rule is None:
            continue
        parameter = rule['key']
        for part in split_embedded_conditions(cell['value_raw'], labels, parameter):
            value = norm(part['value'])
            base, options, availability = option_parts(value, parameter)
            warnings = value_warnings(value, rule['unit'], cell.get('unit_source', ''), parameter)
            if part.get('warning'):
                warnings.append(part['warning'])
            if rule.get('_schema_warning'):
                warnings.append(rule['_schema_warning'])
            if schema.is_optional(cell['source_label']):
                base, options, availability = '', list(dict.fromkeys(options + [value])), 'optional_only'
                warnings.append('optional_equipment')
            condition = part['condition']
            fact_id = hashlib.sha256(json.dumps([
                document.get('source_sha256', ''), cell['source_row_id'], cell['model'],
                cell.get('configuration', 'base'), parameter, condition, value], sort_keys=True).encode()).hexdigest()[:20]
            if fact_id in known_ids:
                continue
            fact = {**cell, 'fact_id': fact_id, 'parameter': parameter, 'parameter_normalized': parameter,
                'category': schema.parameter_group(parameter, rule['category']), 'unit_expected': rule['unit'],
                'source_group': ' / '.join(labels[:-1]), 'matched_synonym': rule.get('_matched_synonym', ''),
                'value': value, 'base_value': base, 'options': options, 'availability': availability,
                'option_kind': 'brace_options' if options else '', 'condition_data': condition,
                'condition': '; '.join(f'{key}={value}' for key, value in sorted(condition.items())),
                'duty_cycle': part['duty_cycle'], 'transformations': ['schema_rematch_unmapped_label'],
                'warnings': sorted(set(warnings)), 'review_status': 'NEEDS_REVIEW' if warnings else 'UNVERIFIED',
                'human_verified': False,
                'source_quote': ' | '.join(labels + [cell.get('unit_source', ''), cell['value_raw']])}
            fact.pop('status', None)
            document['facts'].append(fact)
            known_ids.add(fact_id)
            recovered.append({'source_file': document['source_file'], 'fact_id': fact_id,
                'source_row_id': cell['source_row_id'], 'model': cell['model'], 'parameter': parameter,
                'source_value': cell['value_raw'], 'source_cell_bbox': cell.get('cell_bbox', []), 'method': 'schema_rematch_unmapped_label'})
        cell.update(parameter=parameter, category=rule['category'], status='EXTRACTED_SCHEMA_REMATCH')
    remaining = []
    for row in document.get('unmapped_fields', []):
        row_id = f"{row['source_page']}:{row['source_table']}:{row['source_row']}"
        source_cells = [cell for cell in row.get('cells', []) if cell.get('value_raw', '').strip()]
        def captured(cell):
            return any(f.get('source_row_id') == row_id and f.get('value_raw') == cell['value_raw']
                       and f.get('cell_bbox') == cell.get('bbox') for f in document['facts'])
        if not source_cells or not all(captured(cell) for cell in source_cells):
            remaining.append(row)
    document['unmapped_fields'] = remaining
    if recovered:
        LOGGER.info('Recovered %d recorded model cells from %s', len(recovered), document['source_file'])
    return recovered


def write_checkpoint(output: Path, documents: list[dict]) -> None:
    """Save extraction evidence before value conversion and table assembly."""
    for document in documents:
        report = {key: value for key, value in document.items() if key not in {'source_pages', 'raw_tables', 'source_path'}}
        atomic_json(output / 'documents' / (Path(document['source_file']).stem + '_report.json'), report)
    write_jsonl(output / 'RAW_TABLES.jsonl', (table for d in documents for table in d.get('raw_tables', [])))
    write_jsonl(output / 'SOURCE_PAGES.jsonl', (page for d in documents for page in d.get('source_pages', [])))
    atomic_json(output / 'EXTRACTION_CHECKPOINT.json', {'documents': len(documents),
        'facts': sum(len(d['facts']) for d in documents), 'status': 'EXTRACTION_SAVED_BEFORE_EXPORT'})
