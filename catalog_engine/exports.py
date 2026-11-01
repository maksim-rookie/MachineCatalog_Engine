from __future__ import annotations
from . import PROJECT_NAME
from pathlib import Path
from collections import Counter,defaultdict
import json
import re
from .storage import write_csv,write_jsonl,atomic_json
from .quality import BLOCKING_WARNINGS

IDENTITY=['manufacturer','machine_class','machine_type','model','generation','source_file']
LONG_COLUMNS=['fact_id']+IDENTITY+['model_base','configuration','category','parameter','value','base_value',
 'options','options_json','duty_values_json','cell_bbox_json','ocr_evidence_json','recovery_note','availability','option_kind','condition','condition_data','duty_cycle','unit_expected','unit_source',
 'source_page','source_table','source_row','source_row_id','source_header','header_qualifiers','source_label',
 'source_quote','value_raw','cell_bbox','method','scope','transformations','warnings','review_status','human_verified']


def colname(fact):
    return f'[{fact["category"]}] {fact["parameter"]}'


def display_value(facts,clean=False):
    available=[f for f in facts if f.get('availability')!='not_applicable']
    if not available:
        return '' if clean else '[НЕ ПРИМЕНИМО: "-" В PDF]'
    pieces=[]
    for f in available:
        if clean and f.get('availability')=='optional_only':continue
        if clean and set(f.get('warnings',[]))&BLOCKING_WARNINGS:
            continue
        value=f['value']
        if f.get('condition'): value+=f' [condition: {f["condition"]}]'
        if f.get('warnings') and not clean:
            value+=' [ПРОВЕРИТЬ: '+', '.join(f['warnings'])+']'
        if value not in pieces: pieces.append(value)
    return ' || '.join(pieces)


def typed_number(value: str, expected_unit: str) -> str:
    """Single SI scalar only; parentheses may hold a single imperial equivalent.

    Do not turn multi-value, multi-condition, ranges or dimensional strings into
    a misleading number. The raw value remains available in the long export.
    """
    from .values import unit_family
    family=unit_family(expected_unit)
    if not family or family in {'inch'}: return ''
    s=value.strip().replace('−','-')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:\s*\([+-]?\d+(?:\.\d+)?\))?',s):
        return ''
    return s.split('(',1)[0].strip()


def write_typed_measurements(output:Path, facts:list[dict]):
    from .values import range_bounds,unit_multiplier
    rows=[]
    for fact in facts:
        warning=set(fact.get('warnings',[]))
        value=typed_number(fact.get('base_value',''),fact.get('unit_expected',''))
        scale=unit_multiplier(fact.get('unit_source',fact.get('unit_expected','')),fact.get('unit_expected',''))
        bounds=range_bounds(fact.get('value',''))
        lower=upper=''
        if fact.get('availability')=='not_applicable': status='NOT_APPLICABLE'
        elif fact.get('availability')=='optional_only':status='OPTIONAL_ONLY';value=''
        elif warning & BLOCKING_WARNINGS: status='BLOCKED_REVIEW';value=''
        elif scale is None and (value or bounds):status='BLOCKED_REVIEW';value=''
        elif bounds:
            status='RANGE_UNVERIFIED';value='';lower,upper=(format(n*scale,'.12g') for n in bounds)
        elif not value: status='NOT_SCALAR'
        else: status='AUTO_CHECKED_UNVERIFIED';value=format(float(value)*scale,'.12g')
        rows.append({**fact,'numeric_value':value,'numeric_unit':fact.get('unit_expected',''),
                     'range_min':lower,'range_max':upper,'unit_conversion_factor':scale if scale is not None else '',
                     'typing_status':status})
    write_csv(output/'MEASUREMENTS_TYPED.csv',rows,
              ['fact_id','source_file','source_page','model','generation','configuration',
               'category','parameter','condition','numeric_value','numeric_unit',
               'range_min','range_max','unit_conversion_factor','typing_status','value','value_raw','unit_source','warnings','review_status','human_verified'])
    return rows

def write_outputs(output:Path,documents:list[dict],schema,qualities:list[dict]):
    facts=[f for d in documents for f in d['facts']]
    facts.sort(key=lambda f:(f.get('source_file',''),f['model'],f.get('configuration','base'),f.get('category',''),f['parameter'],f.get('condition','')))
    for f in facts:
        if 'options' not in f: f['options']=[]
        if 'generation' not in f: f['generation']=''
        f['options_json']=f.get('options',[])
        f['duty_values_json']=f.get('duty_values',{})
        f['cell_bbox_json']=f.get('cell_bbox')
        f['ocr_evidence_json']=f.get('ocr_evidence',{})
        f.setdefault('recovery_note','')
    write_csv(output/'ALL_MODELS_long.csv',facts,LONG_COLUMNS)
    typed_rows=write_typed_measurements(output,facts)
    hidden=set(schema.config['hidden_wide_parameters'])
    columns=sorted({colname(f) for f in facts})
    filtered=[c for c in columns if c.split('] ',1)[-1] not in hidden]
    fact_groups=defaultdict(list)
    for f in facts:
        fact_groups[(f['source_file'],f['model'],f.get('configuration','base'),colname(f))].append(f)
    status_groups=defaultdict(list)
    for d in documents:
        for s in d.get('cell_statuses',[]):
            if s.get('parameter'):
                c=f'[{s.get("category", "Other")}] {s["parameter"]}'
                status_groups[(d['source_file'],s['model'],s.get('configuration','base'),c)].append(s)
    base_rows=[];clean_rows=[];configs=[]
    for d in documents:
        for model,meta in sorted(d['registry'].items()):
            identity={k:meta.get(k,d.get(k,'')) for k in IDENTITY}
            identity.update(model=model,source_file=d['source_file'])
            for config in meta.get('configurations',['base']):
                row={**identity,'configuration':config}
                clean=dict(row)
                for c in columns:
                    key=(d['source_file'],model,config,c)
                    fs=fact_groups.get(key,[])
                    statuses=status_groups.get(key,[])
                    if fs:
                        row[c]=display_value(fs)
                        clean[c]=display_value(fs,clean=True)
                    elif any(s['status']=='SOURCE_BLANK' for s in statuses):
                        row[c]='[ПУСТАЯ ЯЧЕЙКА В PDF]';clean[c]=''
                    else:
                        row[c]='[НЕ ИЗВЛЕЧЕНО ИЛИ НЕ УКАЗАНО В КАТАЛОГЕ]';clean[c]=''
                configs.append(row)
                if config=='base':
                    base_rows.append(row);clean_rows.append(clean)
    write_csv(output/'ALL_MODELS_wide.csv',base_rows,IDENTITY+filtered)
    write_csv(output/'ALL_MODELS_wide_values.csv',clean_rows,IDENTITY+filtered)
    write_csv(output/'ALL_CONFIGURATIONS_wide.csv',configs,IDENTITY+['configuration']+filtered)
    # Full per-series exports include the eight fields hidden in the user-facing
    # combined wide CSV. Nothing is deleted from detailed facts or provenance.
    series=defaultdict(list)
    for row in configs: series[row['model'].split()[0]].append(row)
    for name,rows in series.items():
        own=[c for c in columns if any((r['source_file'],r['model'],r['configuration'],c) in fact_groups for r in rows)]
        write_csv(output/'by_series'/f'{re.sub(r"[^A-Z0-9_-]","_",name.upper())}_models.csv',rows,IDENTITY+['configuration']+own)
    opt=[]
    for f in facts:
        for value in f.get('options',[]):
            opt.append({**{k:f.get(k,'') for k in ['fact_id','source_file','model','generation','configuration','parameter','condition','unit_expected','source_page','source_row_id']},
                        'option_value':value,'value_raw':f.get('value_raw',''),'review_status':f.get('review_status','UNVERIFIED')})
    write_csv(output/'ALL_MODELS_options.csv',opt,['fact_id','source_file','model','generation','configuration','parameter','condition','option_value','unit_expected','source_page','source_row_id','value_raw','review_status'])
    statuses=[s for d in documents for s in d.get('cell_statuses',[])]
    for q in qualities:
        for m in q['models']:
            for key in m['missing_required']:
                statuses.append({**m,'parameter':key,'status':'REQUIRED_MISSING'})
    write_csv(output/'MODEL_FIELD_STATUS.csv',statuses,['source_file','model','generation','configuration','parameter','condition','status','value','source_page','source_row_id','source_label','unit_source','value_raw','cell_bbox'])
    reviews=[r for d in documents for r in d['review']]
    write_csv(output/'CELL_ISSUES.csv',reviews,['source_file','source_page','model','configuration','parameter','reason','source_row_id','fact_id','value_raw','cell_bbox','header_raw','models','instruction'])
    atomic_json(output/'NEEDS_REVIEW.json',reviews)
    atomic_json(output/'LOCAL_OCR_AUDIT.json',[event for d in documents for event in d.get('ocr_events',[])])
    atomic_json(output/'AI_QUEUE_SUMMARY.json',{'project_name':PROJECT_NAME,'version':'8.4.5','mode':'local','enabled':False,'api_calls':0,'note':'Native PDF engine does not call external AI services.'})
    model_quality=[m for q in qualities for m in q['models']]
    write_csv(output/'MODEL_QUALITY.csv',model_quality,['source_file','model','generation','configuration','machine_class','machine_type','facts','measurement_records','required_total','required_found','missing_required','source_blank_cells','warnings','extraction_gate_passed','quality_status','human_verified'])
    atomic_json(output/'QUALITY_REPORT.json',{'project_name':PROJECT_NAME,'project_version':'8.4.5','quality_passed':bool(qualities) and all(q['quality_passed'] for q in qualities),
                                            'extraction_gate_passed':bool(qualities) and all(q['extraction_gate_passed'] for q in qualities),'documents':qualities,'human_verified':False})
    write_jsonl(output/'RAW_TABLES.jsonl',(r for d in documents for r in d.get('raw_tables',[])))
    write_jsonl(output/'SOURCE_PAGES.jsonl',(p for d in documents for p in d.get('source_pages',[])))
    write_csv(output/'PAGE_INDEX.csv',[{k:p[k] for k in ['source_file','page','kind','width_pt','height_pt']}|{'native_word_count':len(p.get('words',[]))} for d in documents for p in d.get('source_pages',[])],['source_file','page','kind','native_word_count','width_pt','height_pt'])
    write_csv(output/'UNMAPPED_FIELDS.csv',[r for d in documents for r in d.get('unmapped_fields',[])],['source_file','source_page','source_table','source_row','source_label','unit_source','cells'])
    atomic_json(output/'MODEL_REGISTRY.json',[{'source_file':d['source_file'],'models':d['registry']} for d in documents])
    for d in documents:
        short={k:v for k,v in d.items() if k not in {'source_pages','raw_tables','source_path'}}
        atomic_json(output/'documents'/(Path(d['source_file']).stem+'_report.json'),short)
    summary={'project_name':PROJECT_NAME,'project_version':'8.4.5','files':len(documents),'models':sum(len(d['registry']) for d in documents),'configuration_rows':len(configs),
             'facts':len(facts),'measurement_records':sum(f.get('availability')!='not_applicable' for f in facts),
             'not_applicable_records':sum(f.get('availability')=='not_applicable' for f in facts),
             'source_blank_cells':sum(s['status']=='SOURCE_BLANK' for s in statuses),
             'specification_tables':sum(len(d.get('raw_tables',[])) for d in documents),
             'unmapped_rows':sum(len(d.get('unmapped_fields',[])) for d in documents),
             'review_rows':len(reviews),'review_reason_counts':dict(Counter(r['reason'] for r in reviews)),
             'extraction_gate_passed':bool(qualities) and all(q['extraction_gate_passed'] for q in qualities),
             'quality_passed':bool(qualities) and all(q['quality_passed'] for q in qualities),
             'typed_numeric_records':sum(r['typing_status']=='AUTO_CHECKED_UNVERIFIED' for r in typed_rows),
             'typed_range_records':sum(r['typing_status']=='RANGE_UNVERIFIED' for r in typed_rows),
             'optional_records':sum(f.get('availability')=='optional_only' for f in facts),
             'local_only':True,'network_calls':0,'hidden_wide_parameters':sorted(hidden),'human_verified':False}
    atomic_json(output/'RUN_SUMMARY.json',summary)
    return summary
