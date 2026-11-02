"""Rebuild wide views from an existing run; does not re-extract any PDF."""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time

PROJECT_ROOT=Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:sys.path.insert(0,str(PROJECT_ROOT))
from catalog_engine import PROJECT_NAME,PROJECT_VERSION,ENGINE_VERSION
from catalog_engine.exports import write_outputs
from catalog_engine.normalization import enrich_document
from catalog_engine.quality import check_document
from catalog_engine.schema import Schema
from catalog_engine.storage import atomic_json


def reexport(run_dir,output):
    output=output.resolve()
    if output==PROJECT_ROOT or PROJECT_ROOT in output.parents:
        raise ValueError('Re-export results must be outside the project source tree.')
    if output.exists():raise FileExistsError('Choose a new output folder; existing results are never overwritten.')
    schema=Schema();documents=[];pages=defaultdict(list);tables=defaultdict(list)
    for filename,group in [('SOURCE_PAGES.jsonl',pages),('RAW_TABLES.jsonl',tables)]:
        with (run_dir/filename).open(encoding='utf-8') as f:
            for line in f:
                record=json.loads(line);group[record['source_file']].append(record)
    for path in sorted((run_dir/'documents').glob('*_report.json')):
        document=json.loads(path.read_text(encoding='utf-8'))
        source=document['source_file'];document['source_pages']=pages[source];document['raw_tables']=tables[source]
        # Quality findings are recalculated, source/geometry warnings retained.
        document['review']=[r for r in document['review'] if r['reason'] not in {'required_field_missing','unresolved_value_conflict'}]
        manufacturer=schema.manufacturer_for(' '.join(p['text'] for p in pages[source]))
        if manufacturer:
            document['manufacturer']=manufacturer
            for meta in document['registry'].values():meta['manufacturer']=manufacturer
            for fact in document['facts']:fact['manufacturer']=manufacturer
        document.update(reexport_version=PROJECT_VERSION,reexport_only=True)
        documents.append(enrich_document(document,schema))
    if not documents:raise ValueError('No saved document reports found.')
    start=time.perf_counter();qualities=[check_document(d,schema) for d in documents]
    output.mkdir(parents=True,exist_ok=False)
    atomic_json(output/'SCHEMA_SNAPSHOT.json',schema.config)
    summary=write_outputs(output,documents,schema,qualities)
    summary.update(processing_mode='saved_run_reexport',pdf_reextracted=False,
                   original_run=run_dir.name,export_seconds=round(time.perf_counter()-start,3))
    atomic_json(output/'RUN_SUMMARY.json',summary)
    atomic_json(output/'RUN_MANIFEST.json',{'project_name':PROJECT_NAME,'project_version':PROJECT_VERSION,
                'engine_version':ENGINE_VERSION,'processing_mode':'saved_run_reexport','pdf_reextracted':False,
                'original_run':run_dir.name,'artifacts':sorted(p.name for p in output.iterdir() if p.is_file())})
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    summary=reexport(args.run,args.output)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
