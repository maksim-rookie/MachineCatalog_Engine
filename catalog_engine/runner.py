from __future__ import annotations
from . import PROJECT_NAME, PROJECT_VERSION
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
import hashlib
import json
import logging
import os
import time
import uuid
from . import ENGINE_VERSION
from .extract import extract_pdf
from .exports import write_outputs
from .schema import Schema, DEFAULT_CONFIG
from .quality import check_document
from .progress import RunProgress
from .imports import import_catalogue
from .validation import normalize_document
from .sorting import natural_key
from .storage import atomic_json,engine_fingerprint,cache_key

LOG = logging.getLogger(__name__)

SUPPORTED={'.pdf','.txt','.md','.docx','.xlsx','.xls','.json','.csv'}

@dataclass
class Settings:
    input_dir: Path
    output_root: Path
    cache_root: Path
    schema_path: Path = DEFAULT_CONFIG
    overrides_path: Path | None = None
    use_cache: bool = True
    strict_quality: bool = False
    legacy_fallback: bool = False
    export_csv: bool = False
    sort_by: str | None = None


def reserve_run_directory(root:Path):
    root.mkdir(parents=True,exist_ok=True)
    name='run_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    run=root/name
    while True:
        try: run.mkdir(exist_ok=False);return run
        except FileExistsError: run=root/(name+'_'+uuid.uuid4().hex[:8])


def legacy_adapter(path, original=None):
    """Compatibility is explicit; it never supersedes the new PDF table engine."""
    import compat_v842 as old
    from .values import option_parts
    source=old.source_rows(path)
    facts,review,registry=old.local_extract(path,source)
    for f in facts:
        maker,kind=old.model_info(f['model'])
        f.update(manufacturer=maker,machine_type=kind,generation='',model_base=f['model'],configuration='base',
                 category=old.category(f['parameter']),condition_data={},human_verified=False)
        f['availability']=option_parts(f['value'],f['parameter'])[2]
        f.setdefault('warnings',[])
        f['warnings'].append('legacy_fallback_unverified')
        f['fact_id']=hashlib.sha256(json.dumps(f,sort_keys=True,default=str).encode()).hexdigest()[:20]
    machine_class='turning' if facts and 'Turning' in facts[0]['machine_type'] else 'machining' if facts else 'unknown'
    for name,meta in registry.items():
        maker,kind=old.model_info(name)
        meta.update(model=name,manufacturer=maker,machine_type=kind,generation='',configurations=['base'])
    result=original or {'source_pages':[],'raw_tables':[],'unmapped_fields':[],'cell_statuses':[],'ocr_events':[]}
    result.update(source_file=path.name,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),facts=facts,review=review,
                  registry=registry,machine_class=machine_class,spec_pages=[],engine_version='legacy_fallback')
    result['review'].append({'source_file':path.name,'source_page':0,'reason':'legacy_fallback_unverified',
                            'instruction':'Legacy compatibility result. Main-specification geometry was not established by v4.8.7.'})
    return result


def run(settings:Settings):
    settings.input_dir=settings.input_dir.resolve()
    settings.output_root=settings.output_root.resolve()
    settings.cache_root=settings.cache_root.resolve()
    if not settings.input_dir.is_dir():
        raise ValueError(f'Input directory does not exist: {settings.input_dir}')
    paths=sorted((p for p in settings.input_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED),key=lambda p:natural_key(p.name))
    if not paths:
        raise ValueError(f'No supported files in {settings.input_dir}. Put PDF catalogues in pdf/.')
    schema=Schema(settings.schema_path,settings.overrides_path)
    fingerprint=engine_fingerprint(schema.config)
    # Parser mode is part of the cache identity, not an invisible switch.
    mode_signature=str(settings.legacy_fallback)
    if settings.legacy_fallback or any(p.suffix.lower() in {'.txt','.md','.docx'} for p in paths):
        compat=Path(__file__).resolve().parents[1]/'compat_v842.py'
        mode_signature+=hashlib.sha256(compat.read_bytes()).hexdigest()
        mode_signature+=json.dumps({k:os.getenv(k,'') for k in ['OCR_LANG','LOCAL_CELL_OCR','LOCAL_CELL_OCR_MAX','LOCAL_CELL_OCR_DPI']},sort_keys=True)
    fingerprint=hashlib.sha256((fingerprint+mode_signature).encode()).hexdigest()
    output=reserve_run_directory(settings.output_root)
    atomic_json(output/'SCHEMA_SNAPSHOT.json',schema.config)
    tracker=RunProgress(output,len(paths))
    documents=[];failed=False
    print(f'MachineCatalog Engine {PROJECT_VERSION} | files: {len(paths)} | cache: {settings.use_cache} | local only',flush=True)
    try:
        for index,path in enumerate(paths):
            start=time.perf_counter()
            record={'source_file':path.name,'cache_hit':False,'read_seconds':0.0,'extract_seconds':0.0,'validation_seconds':0.0,'status':'ok'}
            tracker.file_timings.append(record)
            tracker.file_work(index,0,'Opening file',path.name)
            try:
                key=cache_key(path,fingerprint)
                cached=settings.cache_root/(key+'.json')
                document=None
                if settings.use_cache and cached.exists():
                    try:
                        start_cache=time.perf_counter();envelope=json.loads(cached.read_text(encoding='utf-8'))
                        if not isinstance(envelope,dict):
                            raise ValueError('Cache envelope must be an object')
                        if envelope.get('fingerprint')!=fingerprint or envelope.get('key')!=key:
                            raise ValueError('Cache signature mismatch')
                        candidate=envelope['document']
                        if not isinstance(candidate,dict) or candidate.get('source_file')!=path.name or not isinstance(candidate.get('facts'),list) or not isinstance(candidate.get('registry'),dict):
                            raise ValueError('Cache schema mismatch')
                        document=normalize_document(candidate,schema,path.name)
                        if sum(r.get('reason') == 'invalid_input_record' for r in document['review']) > sum(r.get('reason') == 'invalid_input_record' for r in candidate.get('review',[]) if isinstance(r,dict)):
                            raise ValueError('Cache contains invalid fact records')
                        record['cache_hit']=True
                        record['cache_load_seconds']=round(time.perf_counter()-start_cache,4)
                        tracker.file_work(index,1,'Cache hit',path.name)
                    except (OSError,ValueError,KeyError,TypeError) as exc:
                        record['cache_rebuilt_reason']=str(exc)
                        LOG.warning('Rebuilding cache for %s: %s',path.name,exc)
                        document=None
                if document is None:
                    extraction_start=time.perf_counter()
                    if path.suffix.lower()=='.pdf':
                        document=extract_pdf(path,schema,progress=lambda fraction,stage,detail:
                            tracker.file_work(index,fraction,stage,path.name,detail))
                        if not document['registry'] and settings.legacy_fallback:
                            document=legacy_adapter(path,document)
                    elif path.suffix.lower() in {'.json', '.csv', '.xlsx', '.xls'}:
                        document=import_catalogue(path,schema)
                    else:
                        document=legacy_adapter(path)
                    document=normalize_document(document,schema,path.name)
                    record['extract_seconds']=round(time.perf_counter()-extraction_start,4)
                    if document.get('native_timings'):
                        record.update(document['native_timings'])
                    # Cache extraction only, before quality mutations. Never cache
                    # exceptions or an unsupported empty model registry.
                    if settings.use_cache and document['registry']:
                        atomic_json(cached,{'key':key,'fingerprint':fingerprint,'engine_version':ENGINE_VERSION,'document':document})
                    tracker.file_work(index,1,'File extracted',path.name)
                documents.append(document)
                record['model_count']=len(document['registry']);record['fact_records']=len(document['facts'])
                if not document['registry']:
                    record['status']='needs_review'
                    LOG.warning('No supported models in %s; original evidence retained in CATALOG.json',path.name)
            except Exception as exc:
                failed=True;record.update(status='failed',error=f'{type(exc).__name__}: {exc}')
                LOG.error('Catalogue failed: %s: %s',path.name,record['error'])
                LOG.debug('Catalogue failure traceback',exc_info=True)
                try: failed_sha=hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError: failed_sha=''
                documents.append({'source_file':path.name,'source_sha256':failed_sha,
                    'facts':[],'registry':{},'machine_class':'unknown','spec_pages':[],'raw_tables':[],
                    'source_pages':[],'unmapped_fields':[],'cell_statuses':[],
                    'review':[{'source_file':path.name,'source_page':0,'reason':'processing_error','instruction':record['error']}]})
                tracker.file_work(index,1,'File failed',path.name,record['error'])
            finally:
                record['processing_seconds']=round(time.perf_counter()-start,4)
        qualities=[]
        for index,document in enumerate(documents):
            start=time.perf_counter();q=check_document(document,schema);qualities.append(q)
            tracker.file_timings[index]['validation_seconds']=round(time.perf_counter()-start,4)
            tracker.update(88+4*(index+1)/len(documents),'Checking extraction quality',document['source_file'])
        tracker.update(92,'Writing JSON dataset and optional CSV',force=True)
        start_export=time.perf_counter();summary=write_outputs(output,documents,schema,qualities,export_csv_enabled=settings.export_csv,sort_by=settings.sort_by)
        export_seconds=time.perf_counter()-start_export
        tracker.update(98,'Writing run manifest',force=True)
        timing=tracker.finish(errors=failed,quality_passed=summary['quality_passed'])
        timing['export_seconds']=round(export_seconds,4)
        timing['cold_run']=not timing['cache_hits']
        atomic_json(output/'RUN_TIMING.json',timing)
        summary.update(elapsed_seconds=timing['elapsed_seconds'],duration_hms=timing['duration_hms'],
                       completed_with_errors=failed,cache_hits=timing['cache_hits'],
                       timing_report='RUN_TIMING.json',progress_log='RUN_PROGRESS.jsonl',engine_version=ENGINE_VERSION)
        atomic_json(output/'RUN_SUMMARY.json',summary)
        atomic_json(output/'RUN_MANIFEST.json',{'project_name':PROJECT_NAME,'project_version':PROJECT_VERSION,'engine_version':ENGINE_VERSION,
            'engine_fingerprint':fingerprint,'run_id':output.name,'completed_with_errors':failed,
            'quality_passed':summary['quality_passed'],'extraction_gate_passed':summary['extraction_gate_passed'],
            'elapsed_seconds':timing['elapsed_seconds'],
            'source_files':[{'name':d['source_file'],'sha256':d.get('source_sha256','')} for d in documents],
            'artifacts':sorted(str(p.relative_to(output)) for p in output.rglob('*') if p.is_file())})
        # Pointer contains a portable name relative to output/v4_8_7/.
        (settings.output_root/'LATEST_RUN.txt').write_text(output.name+'\n',encoding='utf-8')
        print(f'\nModels: {summary["models"]}; measurements: {summary["measurement_records"]}; explicit N/A: {summary["not_applicable_records"]}')
        print(f'Elapsed: {timing["duration_hms"]} ({timing["elapsed_seconds"]:.3f} s); cache hits: {timing["cache_hits"]}/{len(paths)}')
        print(f'Extraction gate passed: {summary["extraction_gate_passed"]}; quality passed: {summary["quality_passed"]}')
        print('All extracted values remain UNVERIFIED until human review.')
        print('Results:',output,flush=True)
        exit_code=1 if failed else 2 if settings.strict_quality and not summary['quality_passed'] else 0
        return output,summary,exit_code
    except BaseException as exc:
        atomic_json(output/'RUN_ABORTED.json',{'project_name':PROJECT_NAME,'project_version':PROJECT_VERSION,'error':type(exc).__name__+': '+str(exc),
            'elapsed_seconds':round(time.perf_counter()-tracker.start,3),'progress_percent':tracker.last_percent,
            'file_timings':tracker.file_timings,'completed':False})
        raise
    finally:
        tracker.stop()
