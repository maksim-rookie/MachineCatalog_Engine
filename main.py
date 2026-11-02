"""MachineCatalog Engine 8.4.5.1. Local, source-grounded catalogue extraction.

Run ``python main.py --help``. The new geometry engine is the default.
Legacy v8.4.2 compatibility is isolated in compat_v842.py and opt-in for PDFs.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
from catalog_engine.runner import Settings,run
from catalog_engine.progress import RunProgress

from catalog_engine import PROJECT_NAME, PROJECT_VERSION
from catalog_engine.paths import data_paths
BASE=Path(__file__).resolve().parent


def main(argv=None):
    parser=argparse.ArgumentParser(description=f'{PROJECT_NAME} {PROJECT_VERSION}: local catalogue extraction with provenance and quality gates.')
    parser.add_argument('--version',action='version',version=f'{PROJECT_NAME} {PROJECT_VERSION}')
    parser.add_argument('--data-dir',type=Path,help='External data root; default: sibling MachineCatalog_Engine_data or MACHINECATALOG_DATA_DIR')
    parser.add_argument('--init-data',action='store_true',help='Create external input, runs and cache directories and exit')
    parser.add_argument('--input',type=Path,help='Input directory; default: external data root/input')
    parser.add_argument('--output',type=Path,help='Results parent; default: external data root/runs/version')
    parser.add_argument('--cache',type=Path,help='Cache directory; default: external data root/cache/version')
    parser.add_argument('--no-cache',action='store_true',help='Neither read nor write extraction cache')
    parser.add_argument('--strict-quality',action='store_true',help='Exit 2 when quality needs review; files are still saved')
    parser.add_argument('--schema',type=Path,default=BASE/'config'/'schema.json',help='Parameter rules and required fields')
    parser.add_argument('--overrides',type=Path,default=BASE/'config'/'overrides.json',help='Optional additional rules; take priority over defaults')
    parser.add_argument('--legacy-fallback',action='store_true',help='Opt-in v8.4.2/OCR fallback for unsupported PDFs; unverified')
    args=parser.parse_args(argv)
    try:
        paths=data_paths(args.data_dir)
        if args.init_data:
            paths.initialize()
            print(f'Input: {paths.input}\nResults: {paths.runs}\nCache: {paths.cache}')
            return 0
        settings=Settings(args.input or paths.input,args.output or paths.runs,args.cache or paths.cache,args.schema,args.overrides,not args.no_cache,args.strict_quality,args.legacy_fallback)
        _,_,code=run(settings)
        return code
    except KeyboardInterrupt:
        print('\nInterrupted. Partial progress remains in the run folder.',file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
