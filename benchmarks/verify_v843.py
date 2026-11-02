"""Check source-transcribed reference values, not an accuracy estimate."""
from __future__ import annotations
import argparse
import csv
import json
import re
from pathlib import Path

DEFAULT_EXPECTED=Path(__file__).with_name('expected_v843.csv')


def evaluate(rows,expected):
    results=[]
    available_sources={r['source_file'] for r in rows}
    for case in expected:
        if case['source_file'] not in available_sources:
            results.append({**case,'result':'SOURCE_NOT_IN_RUN','actual':[]});continue
        candidates=[r for r in rows if all(r.get(k,'')==case[k] for k in ['source_file','model','configuration','parameter']) and case['condition_contains'] in r.get('condition','')]
        values=[r['value'] for r in candidates]
        comparison=case['comparison'];want=case['expected']
        if comparison=='missing': passed=not candidates
        elif comparison=='exact': passed=want in values
        elif comparison=='contains': passed=any(want in v for v in values)
        elif comparison=='primary_number':
            numbers=[]
            for value in values:
                m=re.match(r'\s*[\u00d8\u00f8\u3141]?([-+]?\d+(?:\.\d+)?)\b',value)
                if m: numbers.append(float(m.group(1)))
            passed=any(abs(n-float(want))<1e-9 for n in numbers)
        else: raise ValueError('Unknown comparator: '+comparison)
        results.append({**case,'result':'PASS' if passed else 'FAIL','actual':values})
    return results


def verify(run_dir:Path,expected_path:Path=DEFAULT_EXPECTED):
    with (run_dir/'ALL_MODELS_long.csv').open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    with expected_path.open(encoding='utf-8-sig',newline='') as f: expected=list(csv.DictReader(f))
    results=evaluate(rows,expected)
    summary={name:sum(r['result']==name for r in results) for name in ['PASS','FAIL','SOURCE_NOT_IN_RUN']}
    summary['checks']=len(results)
    summary['note']='Reference checks only. Not full-catalogue accuracy and not human verification of all extracted facts.'
    return summary,results


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--expected',type=Path,default=DEFAULT_EXPECTED)
    p.add_argument('--save',type=Path)
    a=p.parse_args();summary,results=verify(a.run_dir,a.expected)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for r in results:
        if r['result']=='FAIL': print('FAIL',r['model'],r['parameter'],r['expected'],r['actual'])
    if a.save:
        a.save.mkdir(parents=True,exist_ok=True)
        (a.save/'REFERENCE_CHECKS.json').write_text(json.dumps({'summary':summary,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    return 1 if summary['FAIL'] else 0

if __name__=='__main__': raise SystemExit(main())
