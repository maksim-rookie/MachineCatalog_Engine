"""MachineCatalog Engine 4.8.7. Local, source-grounded catalogue extraction.

Run ``python main.py --help``. The new geometry engine is the default.
Legacy v8.4.2 compatibility is isolated in compat_v842.py and opt-in for PDFs.
"""
from __future__ import annotations
import argparse
import logging
from pathlib import Path
import sys
from catalog_engine.runner import Settings,run
from catalog_engine.progress import RunProgress

from catalog_engine import PROJECT_NAME, PROJECT_VERSION
BASE=Path(__file__).resolve().parent


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    parser=argparse.ArgumentParser(description='MachineCatalog Engine 4.8.7: local catalogue extraction with provenance and quality gates.')
    parser.add_argument('--version',action='version',version=f'{PROJECT_NAME} {PROJECT_VERSION}')
    parser.add_argument('--input',type=Path,default=BASE/'pdf',help='Input directory; defaults to project/pdf')
    parser.add_argument('--output',type=Path,default=BASE/'output'/'v4_8_7',help='Parent directory for unique dated runs')
    parser.add_argument('--cache',type=Path,default=BASE/'output'/'cache_v4_8_7',help='Versioned extraction cache')
    parser.add_argument('--no-cache',action='store_true',help='Neither read nor write extraction cache')
    parser.add_argument('--strict-quality',action='store_true',help='Exit 2 when quality needs review; files are still saved')
    parser.add_argument('--schema',type=Path,default=BASE/'config'/'schema.json',help='Parameter rules and required fields')
    parser.add_argument('--overrides',type=Path,default=BASE/'config'/'overrides.json',help='Optional additional rules; take priority over defaults')
    parser.add_argument('--legacy-fallback',action='store_true',help='Opt-in v8.4.2/OCR fallback for unsupported PDFs; unverified')
    parser.add_argument('--export-csv',action='store_true',help='Also write one final CATALOG.csv; JSON is always saved')
    parser.add_argument('--sort-by',help='Sort model rows by a canonical parameter key; missing values last')
    parser.add_argument('--log-level',choices=['DEBUG','INFO','WARNING','ERROR'],default='INFO',help='Logging detail')
    args=parser.parse_args(argv)
    logging.getLogger().setLevel(args.log_level)
    settings=Settings(input_dir=args.input,output_root=args.output,cache_root=args.cache,
                      schema_path=args.schema,overrides_path=args.overrides,use_cache=not args.no_cache,
                      strict_quality=args.strict_quality,legacy_fallback=args.legacy_fallback,
                      export_csv=args.export_csv,sort_by=args.sort_by)
    try:
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
