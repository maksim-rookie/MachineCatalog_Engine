"""Apply dimensions, Feedrate and missing-value presentation rules to a saved run, preserving the input."""
from pathlib import Path
import argparse
import json
import shutil
from catalog_engine.dimensions_view import finalize_wide_tables


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Choose a new output directory; existing runs are not overwritten.')
    documents=[json.loads(p.read_text(encoding='utf-8')) for p in (args.run/'documents').glob('*_report.json')]
    if not documents:raise ValueError('Saved document reports are required to verify dimensions.')
    shutil.copytree(args.run,args.output)
    finalize_wide_tables(args.output,documents)
    print(args.output)


if __name__=='__main__':main()
