"""Local PDF catalogue extraction with auditable, normalized exports."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import traceback
from catalog_engine import PROJECT_NAME, PROJECT_VERSION

BASE = Path(__file__).resolve().parent
RELEASE = 'v' + PROJECT_VERSION.replace('.', '_')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f'{PROJECT_NAME} {PROJECT_VERSION}')
    parser.add_argument('--version', action='version', version=f'{PROJECT_NAME} {PROJECT_VERSION}')
    parser.add_argument('--input', type=Path, default=BASE / 'pdf', help='Input catalogue directory')
    parser.add_argument('--output', type=Path, default=BASE / 'output' / RELEASE, help='Run history directory')
    parser.add_argument('--cache', type=Path, default=BASE / 'output' / ('cache_' + RELEASE))
    parser.add_argument('--no-cache', action='store_true', help='Disable cache reads and writes')
    parser.add_argument('--strict-quality', action='store_true', help='Exit 2 if quality needs review')
    parser.add_argument('--schema', type=Path, default=BASE / 'config' / 'schema.json')
    parser.add_argument('--overrides', type=Path, default=BASE / 'config' / 'overrides.json')
    parser.add_argument('--review-file', type=Path, default=BASE / 'config' / 'review_overrides.json')
    parser.add_argument('--legacy-fallback', action='store_true', help='Enable the explicit legacy adapter')
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from catalog_engine.runner import Settings, run
        settings = Settings(
            input_dir=args.input,
            output_root=args.output,
            cache_root=args.cache,
            schema_path=args.schema,
            overrides_path=args.overrides,
            use_cache=not args.no_cache,
            strict_quality=args.strict_quality,
            legacy_fallback=args.legacy_fallback,
            review_path=args.review_file,
        )
        _, _, exit_code = run(settings)
        return exit_code
    except KeyboardInterrupt:
        print('Interrupted. Saved extraction evidence remains in the run directory.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        directory = getattr(exc, 'run_directory', None)
        if directory:
            print(f'Details: {directory / "RUN_DEBUG.log"}', file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
