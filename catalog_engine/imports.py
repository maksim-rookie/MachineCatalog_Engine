"""Structured catalogue import: JSON/CSV/XLSX long records or wide model rows."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .validation import DocumentValidationError, canonical_record, normalize_document, text

IDENTITY = {'model', 'manufacturer', 'machine_class', 'machine_type', 'generation',
            'configuration', 'source_file', 'model_base', 'source_sheet'}


def import_catalogue(path: Path, schema):
    suffix = path.suffix.lower()
    sheet_issues = []
    if suffix == '.json':
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(payload, dict) and payload.get('format_version') == 1 and 'documents' in payload:
            raise DocumentValidationError('CATALOG.json is an output dataset; use python -m catalog_engine.replay to reprocess it')
        if isinstance(payload, dict) and 'facts' in payload:
            payload.setdefault('registry', {})
            payload['input_kind'] = 'structured'
            payload['source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            return normalize_document(payload, schema, path.name)
        records = payload.get('records') if isinstance(payload, dict) else payload
    elif suffix == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(stream, dialect=dialect)
            headers = [next(iter(canonical_record({name: None}))) for name in (reader.fieldnames or [])]
            if len(headers) != len(set(headers)):
                raise DocumentValidationError('Duplicate or conflicting CSV column names')
            records = list(reader)
    elif suffix in {'.xlsx', '.xls'}:
        import pandas as pd
        sheets = pd.read_excel(path, sheet_name=None, dtype=object)
        records = []
        for name, frame in sheets.items():
            rows = frame.where(pd.notna(frame), None).to_dict('records')
            if rows and 'model' not in canonical_record(rows[0]):
                sheet_issues.append({'reason':'invalid_sheet','source_file':path.name,'source_sheet':name,
                                     'instruction':'Required model column is missing; sheet skipped'})
                continue
            records.extend({**row,'source_sheet':name} for row in rows)
    else:
        raise DocumentValidationError(f'Unsupported structured format: {suffix}')
    if not isinstance(records, list):
        raise DocumentValidationError('Expected a list of records or an object with records/facts')
    rules = {r['key']: r for r in reversed(schema.config['rules'])}
    facts, issues, registry = [], sheet_issues, {}
    for index, raw in enumerate(records):
        try:
            if not isinstance(raw, dict):
                raise DocumentValidationError('Record must be an object')
            row = canonical_record(raw)
            if not text(row.get('model')):
                raise DocumentValidationError('Required model column is missing or empty')
            model = text(row['model'])
            meta = registry.setdefault(model, {k: row[k] for k in IDENTITY if k in row})
            meta.setdefault('configurations', [])
            config = text(row.get('configuration')) or 'base'
            if config not in meta['configurations']:
                meta['configurations'].append(config)
            if 'parameter' in row:
                if 'value' not in row:
                    raise DocumentValidationError('Long records require a value column (null is allowed)')
                facts.append({**row, 'raw_record': raw, 'source_row': index + 1})
            else:
                for key, value in row.items():
                    if key in IDENTITY or key in {'unit_source', 'category'}:
                        continue
                    rule = rules.get(key, {})
                    facts.append({**{k: v for k, v in row.items() if k in IDENTITY},
                                  'parameter': key, 'value': value, 'value_raw': value,
                                  'unit_source': rule.get('unit', ''),
                                  'unit_expected': rule.get('unit', ''),
                                  'category': rule.get('category', 'Other'),
                                  'source_row': index + 1, 'raw_record': raw})
        except (ValueError, TypeError) as exc:
            issues.append({'reason': 'invalid_input_record', 'source_file': path.name,
                           'row_index': index, 'instruction': str(exc), 'raw_record': raw})
    document = {'source_file': path.name, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'input_kind': 'structured', 'facts': facts, 'registry': registry, 'review': issues}
    if records and not facts:
        document['review'].append({'reason': 'no_valid_records', 'source_file': path.name})
    return normalize_document(document, schema)
