"""Replay only table header/field interpretation from a prior RUN folder.

This cannot replace full PDF re-extraction, but is a transparent offline
regression check when original PDF files are not mounted.
Usage: python benchmarks/audit_saved_geometry_v844.py --run PATH --out PATH
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from catalog_engine.models import parse_header
from catalog_engine.schema import Schema
from catalog_engine.values import unit_family


def audit(run_dir:Path) -> dict:
    schema=Schema()
    tables=run_dir/'RAW_TABLES.jsonl'
    if not tables.is_file(): raise FileNotFoundError(tables)
    model_sets=defaultdict(set)
    findings=[];row_count=0;table_count=0
    for line in tables.open(encoding='utf-8'):
        table=json.loads(line);table_count+=1
        source=table['source_file'];page=table['page'];seen={}
        for h in table['headers']:
            for ref in parse_header(h['text']):
                key=(ref.model,ref.configuration)
                if key in seen:
                    findings.append({'source_file':source,'source_page':page,
                       'kind':'model_identity_collision','model':ref.model,'headers':[seen[key],h['text']]})
                seen[key]=h['text']
                model_sets[source].add(ref.model)
        for row in table['rows']:
            row_count+=1
            rule=schema.match(row.get('labels',[]),row.get('unit',''))
            labels=' / '.join(row.get('labels',[]))
            if not rule:
                findings.append({'source_file':source,'source_page':page,
                                 'kind':'unmapped_label','source_label':labels,'unit':row.get('unit','')})
                continue
            if rule.get('_schema_warning'):
                findings.append({'source_file':source,'source_page':page,'kind':rule['_schema_warning'],
                                 'source_label':labels,'unit':row.get('unit',''),
                                 'mapped_parameter':rule['key']})
    return {'status':'GEOMETRY_REPLAY_ONLY_NOT_PDF_RECOGNITION',
            'tables':table_count,'rows':row_count,
            'models':sum(len(v) for v in model_sets.values()),
            'models_by_source':{k:sorted(v) for k,v in sorted(model_sets.items())},
            'findings':findings,'finding_counts':dict(Counter(f['kind'] for f in findings))}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    result=audit(args.run)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('table replay',result['tables'],'tables',result['models'],'model names',result['finding_counts'])

if __name__=='__main__':main()
