"""Rebuild all tables from a recorded run: python reexport.py --run PATH."""
from __future__ import annotations
import argparse
import logging
import json
from pathlib import Path
import sys
from catalog_engine import PROJECT_VERSION
from catalog_engine.exports import write_outputs
from catalog_engine.quality import check_document
from catalog_engine.replay import load_run, recover_unmapped, restore_source_boxes, write_checkpoint
from catalog_engine.runner import reserve_run_directory
from catalog_engine.schema import DEFAULT_CONFIG, Schema
from catalog_engine.storage import atomic_json, write_csv


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path, help='Existing extracted run directory')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'output' / 'v4_8_5_4_reexport')
    parser.add_argument('--schema', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--overrides', type=Path)
    args = parser.parse_args(argv)
    output = reserve_run_directory(args.output.resolve())
    handler = logging.FileHandler(output / 'RUN_DEBUG.log', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logger = logging.getLogger('catalog_engine')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        source = args.run.resolve()
        if not source.is_dir():
            raise ValueError(f'Run directory does not exist: {source}')
        schema = Schema(args.schema, args.overrides)
        documents = load_run(source)
        recovery = []
        for document in documents:
            restore_source_boxes(document)
            recovery.extend(recover_unmapped(document, schema))
        write_checkpoint(output, documents)
        qualities = [check_document(document, schema) for document in documents]
        summary = write_outputs(output, documents, schema, qualities)
        summary.update(reexport_of=source.name, project_version=PROJECT_VERSION,
                       pdf_extraction_repeated=False, recovered_recorded_cells=len(recovery),
                       recovered_source_cells=len({(r['source_file'], r['source_row_id'],
                           json.dumps(r['source_cell_bbox'])) for r in recovery}))
        atomic_json(output / 'RUN_SUMMARY.json', summary)
        atomic_json(output / 'SCHEMA_SNAPSHOT.json', schema.config)
        write_csv(output / 'RECOVERY_AUDIT.csv', recovery,
                  ['source_file', 'fact_id', 'source_row_id', 'model', 'parameter', 'source_value', 'source_cell_bbox', 'method'])
        print(f"Export complete: {output}\nFacts: {summary['facts']}; recovered: {len(recovery)}")
        return 0
    except Exception as exc:  # CLI boundary: record the full traceback for debugging.
        logger.exception('Re-export failed')
        atomic_json(output / 'RUN_ABORTED.json', {'error': f'{type(exc).__name__}: {exc}'})
        print(f'ERROR: {exc}\nDetails: {output / "RUN_DEBUG.log"}', file=sys.stderr)
        return 1
    finally:
        logger.removeHandler(handler)
        handler.close()


if __name__ == '__main__':
    raise SystemExit(main())
