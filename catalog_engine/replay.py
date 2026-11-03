"""Reprocess saved source cells/words without pretending the original PDFs exist.

This does not re-run vector detection, OCR or validate the original PDF bytes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from .extract import collect_tables, machine_identity
from .geometry import Cell, LogicalRow, LogicalTable
from .headerless import headerless_tables
from .quality import check_document
from .schema import Schema
from .validation import normalize_document
from .exports import write_outputs


class RecordedPage:
    def __init__(self, record):
        self.record = record
        self.rect = SimpleNamespace(width=record['width_pt'], height=record['height_pt'])

    def get_text(self, mode, sort=False):
        if mode == 'text':
            return self.record.get('text', '')
        words = self.record.get('words', [])
        if mode == 'words':
            return [(*w['bbox'], w['text']) for w in words]
        if mode == 'dict':
            return {'blocks': [{'lines': [{'bbox': w['bbox'], 'spans': [
                {'text': w['text'], 'size': w['bbox'][3]-w['bbox'][1]}]} for w in words]}]}
        raise ValueError(f'Unsupported recorded-page mode: {mode}')

    def get_drawings(self):
        return []


def restore_table(raw):
    rows = [LogicalRow(r['row_index'], tuple(r['bbox']), r['labels'], r['unit'],
                       [Cell(tuple(c['bbox']), c['text']) for c in r['values']],
                       recovery=r.get('recovery', ''), warnings=tuple(r.get('warnings', [])))
            for r in raw['rows']]
    return LogicalTable(raw['page'], tuple(raw['bbox']),
                        [Cell(tuple(c['bbox']), c['text']) for c in raw['headers']], rows, raw['method'])


def replay_document(document, schema):
    original = deepcopy(document)
    source_pages = original.get('source_pages', [])
    if not source_pages:
        return normalize_document(original, schema)
    maker, machine_class, machine_type = machine_identity(source_pages)
    result = {k: original[k] for k in ('source_file', 'source_sha256', 'source_legends') if k in original}
    result.update(manufacturer=maker, machine_class=machine_class, machine_type=machine_type,
                  source_pages=source_pages, facts=[], registry={}, review=[], cell_statuses=[],
                  raw_tables=[], unmapped_fields=[], ocr_events=original.get('ocr_events', []), spec_pages=[],
                  replay_basis='saved_source_cells_and_words; original PDF/vector/OCR not revalidated')
    tables_by_page = defaultdict(list)
    for raw in original.get('raw_tables', []):
        tables_by_page[raw['page']].append(restore_table(raw))
    for index, record in enumerate(source_pages):
        page = RecordedPage(record)
        tables = tables_by_page[record['page']] or headerless_tables(page, record['page'], schema)
        if tables:
            collect_tables(result, Path(result['source_file']), index, page, tables, schema, recover_tails=False)
        elif not record.get('words'):
            result['review'].append({'source_file': result['source_file'], 'source_page': record['page'], 'reason': 'no_native_text'})
    if not result['registry']:
        result['review'].append({'source_file': result['source_file'], 'reason': 'no_supported_specification_table'})
    return normalize_document(result, schema)


def load_run(directory):
    unified = directory/'CATALOG.json'
    if unified.exists():
        dataset = json.loads(unified.read_text(encoding='utf-8-sig'))
        fact_index = {f['fact_id']: f for f in dataset['facts']}
        return [{**d, 'facts': [fact_index[k] for k in d['fact_ids']]} for d in dataset['documents']]
    docs = [json.loads(p.read_text(encoding='utf-8-sig')) for p in sorted((directory/'documents').glob('*.json'))]
    if not docs:
        raise ValueError(f'No saved documents in {directory}')
    by_file = {d['source_file']: d for d in docs}
    for filename, key in [('SOURCE_PAGES.jsonl', 'source_pages'), ('RAW_TABLES.jsonl', 'raw_tables')]:
        path = directory/filename
        if path.exists():
            for doc in docs:
                doc[key] = []
            with path.open(encoding='utf-8-sig') as stream:
                for line in stream:
                    row = json.loads(line)
                    by_file[row['source_file']][key].append(row)
    return docs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--export-csv', action='store_true')
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output must be a new or empty directory; input evidence is never overwritten')
    schema = Schema()
    documents = [replay_document(d, schema) for d in load_run(args.run_dir)]
    qualities = [check_document(d, schema) for d in documents]
    summary = write_outputs(args.output, documents, schema, qualities, export_csv_enabled=args.export_csv)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
