#!/usr/bin/env python3
"""Measure *presence* of reference model+parameter+condition entries in ALL_MODELS_long.csv.

Usage: python verify_coverage.py expected_photo_160.csv output/ALL_MODELS_long.csv
This checks coverage, NOT whether numbers are correct. Human auditing is still required.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

def read_csv(path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def main():
    if len(sys.argv) != 3:
        print('Usage: python verify_coverage.py expected_photo_160.csv path/to/ALL_MODELS_long.csv')
        return 2
    expected, observed = read_csv(sys.argv[1]), read_csv(sys.argv[2])
    keys = {(r['model'], r['parameter'], r.get('condition','').strip()) for r in observed}
    per=defaultdict(lambda: [0,0])
    missing=[]
    for row in expected:
        model=row['model']; per[model][0]+=1
        k=(model,row['parameter'],row.get('condition','').strip())
        if k in keys:per[model][1]+=1
        else:missing.append(row)
    for model,(total,present) in sorted(per.items()):
        print(f'{model:12}: {present}/{total} ({present/total:.1%}) present')
    print(f'ALL: {len(expected)-len(missing)}/{len(expected)} present, {len(missing)} missing.')
    if missing:
        print('MISSING:')
        for r in missing:
            print(f"  {r['model']} | {r['parameter']} | {r.get('condition','')} | reference={r['reference_value']}")
    print('Note: semantic accuracy, OCR quality, and options must be reviewed separately.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
