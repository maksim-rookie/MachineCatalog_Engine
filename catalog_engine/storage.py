"""Atomic artifact writes and deterministic extraction cache identities."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import csv
import hashlib
import json
import logging
import os
import uuid
from . import ENGINE_VERSION

LOGGER = logging.getLogger(__name__)


class ArtifactWriteError(RuntimeError):
    """An artifact could not be fully written; the previous file is preserved."""


@contextmanager
def atomic_text(path: Path, encoding: str = 'utf-8', newline=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding=encoding, newline=newline) as stream:
            yield stream
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError, csv.Error) as exc:
        raise ArtifactWriteError(f'Cannot write {path}: {type(exc).__name__}: {exc}') from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning('Cannot remove temporary artifact %s', temporary, exc_info=True)


def atomic_json(path: Path, value):
    with atomic_text(path) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def write_jsonl(path: Path, rows):
    with atomic_text(path) as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')


def write_csv(path: Path, rows, fields):
    if len(fields) != len(set(fields)):
        raise ValueError(f'Duplicate CSV column names in {path}')
    with atomic_text(path, encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list, tuple)) else value for key, value in row.items()})


def engine_fingerprint(schema_config):
    fingerprint = hashlib.sha256(ENGINE_VERSION.encode())
    for path in sorted(Path(__file__).parent.glob('*.py')):
        fingerprint.update(path.name.encode())
        fingerprint.update(path.read_bytes())
    fingerprint.update(json.dumps(schema_config, sort_keys=True).encode())
    try:
        import pymupdf as fitz
        import pdfplumber
        fingerprint.update(str(fitz.VersionBind).encode())
        fingerprint.update(str(pdfplumber.__version__).encode())
    except ImportError:
        pass
    return fingerprint.hexdigest()


def cache_key(path: Path, fingerprint: str):
    digest = hashlib.sha256(path.read_bytes())
    digest.update(path.name.encode('utf-8'))
    digest.update(fingerprint.encode())
    return digest.hexdigest()
