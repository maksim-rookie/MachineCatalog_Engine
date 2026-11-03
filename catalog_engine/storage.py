from __future__ import annotations
from pathlib import Path
import csv
import hashlib
import json
import math
import os
import uuid
from . import ENGINE_VERSION


def json_safe(value):
    """Keep non-finite raw numbers as text; emit standards-compliant JSON."""
    if isinstance(value,float) and not math.isfinite(value):
        return str(value)
    if isinstance(value,dict):
        return {str(k): json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [json_safe(v) for v in value]
    return value


def atomic_json(path:Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(json_safe(value),ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')
        os.replace(temp,path)
    finally:
        if temp.exists(): temp.unlink()


def write_jsonl(path:Path,rows):
    with path.open('w',encoding='utf-8') as f:
        for row in rows: f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')


def write_csv(path:Path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for row in rows:
            w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple)) else v for k,v in row.items()})


def engine_fingerprint(schema_config):
    h=hashlib.sha256(ENGINE_VERSION.encode())
    for p in sorted(Path(__file__).parent.glob('*.py')):
        h.update(p.name.encode());h.update(p.read_bytes())
    h.update(json.dumps(schema_config,sort_keys=True).encode())
    # Library changes can alter PDF geometry. Avoid silently reusing stale cells.
    try:
        import pymupdf as fitz, pdfplumber
        h.update(str(fitz.VersionBind).encode());h.update(str(pdfplumber.__version__).encode())
    except ImportError:
        pass
    return h.hexdigest()


def cache_key(path:Path,fingerprint:str):
    h=hashlib.sha256(path.read_bytes())
    h.update(path.name.encode('utf-8'));h.update(fingerprint.encode())
    return h.hexdigest()
