"""Compare extraction with 190 source-transcribed Kaibo cells, not full accuracy."""
from pathlib import Path
import argparse
import csv
import json

EXPECTED=Path(__file__).with_name('expected_kaibo_845.csv')
SOURCE='凯博数控电子样本2025.pdf'


def verify(run_dir, expected=EXPECTED):
    with (run_dir/'ALL_MODELS_long.csv').open(encoding='utf-8-sig',newline='') as f:
        facts=list(csv.DictReader(f))
    with expected.open(encoding='utf-8-sig',newline='') as f:
        cases=list(csv.DictReader(f))
    results=[]
    for case in cases:
        matches=[f for f in facts if f['source_file']==SOURCE and f['model']==case['model'] and f['parameter']==case['parameter']]
        passed=len(matches)==1 and all(matches[0][key]==case[target] for key,target in [('value','expected'),('unit_source','unit_source'),('source_page','source_page')])
        results.append({**case,'result':'PASS' if passed else 'FAIL',
                        'actual':[{key:f[key] for key in ['value','unit_source','source_page']} for f in matches]})
    summary={'checks':len(cases),'PASS':sum(r['result']=='PASS' for r in results),'FAIL':sum(r['result']=='FAIL' for r in results),
             'note':'Source-transcribed reference cells only; not full-catalogue accuracy or human verification of all facts.'}
    return summary,results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--save',type=Path)
    args=parser.parse_args();summary,results=verify(args.run_dir)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if args.save:
        args.save.parent.mkdir(parents=True,exist_ok=True)
        args.save.write_text(json.dumps({'summary':summary,'results':results},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 1 if not summary['checks'] or summary['FAIL'] else 0


if __name__=='__main__':raise SystemExit(main())
