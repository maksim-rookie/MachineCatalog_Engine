from __future__ import annotations
from . import PROJECT_NAME
from pathlib import Path
from collections import Counter,defaultdict
import json
import re
from .storage import write_csv,write_jsonl,atomic_json
from .quality import BLOCKING_WARNINGS
from .normalization import canonical_scalar, projection
from .normalized_exports import write_normalized_outputs
from .report_layout import ordered_columns, aggregate_families
from .classification import category_tree
from . import PROJECT_VERSION

IDENTITY=['manufacturer','machine_class','machine_type','model','base_model','model_variant','generation','source_file']
LONG_COLUMNS=['fact_id']+IDENTITY+['model_base','model_family_rule','configuration','category','parameter','parameter_normalized','value','value_extracted','base_value',
 'options','canonical_value','canonical_unit','conversion_factor','options_json','duty_values_json','cell_bbox_json','ocr_evidence_json','recovery_note','availability','option_kind','condition','condition_data','duty_cycle','unit_expected','unit_source',
 'source_cell_bbox','source_page','source_table','source_row','source_row_id','source_header','header_qualifiers','source_label','source_group','matched_synonym',
 'source_quote','value_raw','cell_bbox','method','scope','transformations','warnings','review_status','human_verified','manual_reviewer','manual_reason']


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
        # Scalar display respects the unit declared by the column name. The
        # original text stays in ALL_MODELS_long.csv and provenance reports.
        value=(f.get('value') if f.get('human_verified') else (f.get('canonical_value') if not f.get('options') else '') or f['value'])
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

def table_cell_coverage(documents):
    """Compare nonempty original source cells with provenance-preserving facts.

    A source cell shared across models is counted once even when it generates
    several model facts. A truly blank source cell remains explicitly blank.
    """
    rows=[]
    for doc in documents:
        indexed=defaultdict(list)
        for fact in doc.get('facts',[]):
            indexed[(fact.get('source_page'),fact.get('source_table'),fact.get('source_row'))].append(fact)
        for t in doc.get('raw_tables',[]):
            page=t['page']; table=t.get('source_table',1)
            total=filled=matched=empty=0
            for r in t.get('rows',[]):
                for cell in r.get('values',[]):
                    total+=1
                    raw=cell.get('text','').strip()
                    if not raw:
                        empty+=1;continue
                    filled+=1
                    # Match coordinates too; a repeated value in another model
                    # column does not prove this physical cell was extracted.
                    found=False
                    for fact in indexed[(page,table,r['row_index'])]:
                        source_box = fact.get('source_cell_bbox', fact.get('cell_bbox', ()))
                        same_box = (len(source_box) == len(cell['bbox']) == 4
                                    and all(abs(a-b)<0.06 for a,b in zip(source_box,cell['bbox'])))
                        if same_box and fact.get('value_raw','').strip()==raw:
                            found=True;break
                    if found:matched+=1
            rows.append({'source_file':doc['source_file'],'source_page':page,'source_table':table,
                         'source_cells_total':total,'source_cells_nonempty':filled,
                         'source_cells_with_facts':matched,'source_cells_not_extracted':filled-matched,
                         'source_cells_blank_in_pdf':empty,
                         'status':'ALL_NONEMPTY_CELLS_CAPTURED' if matched==filled else 'REVIEW_MISSING_SOURCE_CELLS'})
    return rows


def control_mentions(documents):
    """Unassigned narrative evidence; never invent a model's CNC system."""
    from .models import parse_header
    vendor=re.compile(r'FANUC|SIEMENS|SYNTEC|MITSUBISHI|HEIDENHAIN|西门子|发那科|三菱|新代|铼钠克',re.I)
    out=[]
    for doc in documents:
        by_page=defaultdict(set)
        for table in doc.get('raw_tables',[]):
            for header in table['headers']:
                by_page[table['page']].update(ref.model for ref in parse_header(header['text']))
        for page in doc.get('source_pages',[]):
            if page['page'] not in by_page:continue
            for line in page.get('text','').splitlines():
                line=line.strip()
                if len(line)>350 or not vendor.search(line):continue
                if not re.search(r'系统|CNC|control|system|FANUC|SIEMENS|SYNTEC|HEIDENHAIN',line,re.I):continue
                out.append({'source_file':doc['source_file'],'source_page':page['page'],
                            'candidate_models_same_page':sorted(by_page[page['page']]),
                            'source_quote':line,'status':'NARRATIVE_ONLY_REQUIRES_REVIEW',
                            'automatic_model_assignment':False})
    return out



def write_outputs(output:Path,documents:list[dict],schema,qualities:list[dict]):
    facts=[f for d in documents for f in d['facts']]
    categories=schema.config.get('parameter_groups',[])
    group_rank={g:i for i,g in enumerate(categories)}
    param_orders=schema.config.get('parameter_order',{})
    def fact_order(f):
        c=f.get('category','Other');keys=param_orders.get(c,[])
        return (f.get('source_file',''),f.get('base_model',f['model']),
                group_rank.get(c,len(categories)),
                0 if f['parameter'] in keys else 1,
                keys.index(f['parameter']) if f['parameter'] in keys else f.get('source_page',0)*10000+f.get('source_row',0),
                f['model'],f.get('configuration','base'),f.get('condition',''))
    facts.sort(key=fact_order)
    for f in facts:
        if 'options' not in f: f['options']=[]
        if 'generation' not in f: f['generation']=''
        f['options_json']=f.get('options',[])
        f['duty_values_json']=f.get('duty_values',{})
        f['cell_bbox_json']=f.get('cell_bbox')
        f['ocr_evidence_json']=f.get('ocr_evidence',{})
        f.setdefault('recovery_note','')
        canonical=canonical_scalar(f)
        f['canonical_value']=canonical[0] if canonical else ''
        f['canonical_unit']=f.get('unit_expected','') if canonical else ''
        f['conversion_factor']=canonical[1] if canonical else ''
    normalization_summary = write_normalized_outputs(output, documents, schema)
    write_csv(output/'ALL_MODELS_long.csv',facts,LONG_COLUMNS)
    conversions=[{'fact_id':f['fact_id'],'source_file':f['source_file'],
                  'source_page':f['source_page'],'model':f['model'],
                  'configuration':f.get('configuration','base'),
                  'parameter':f['parameter'],'source_value':f['value'],
                  'source_unit':f.get('unit_source',''),
                  'canonical_value':f['canonical_value'],'canonical_unit':f['canonical_unit'],
                  'multiplier':f['conversion_factor'],'human_verified':False}
                 for f in facts if f['canonical_value'] and f['conversion_factor']!=1]
    write_csv(output/'UNIT_CONVERSIONS.csv',conversions,
              ['fact_id','source_file','source_page','model','configuration','parameter',
               'source_value','source_unit','canonical_value','canonical_unit','multiplier','human_verified'])
    derived=[item for f in facts for item in projection(f)]
    write_csv(output/'DERIVED_COMPONENTS.csv',derived,
              ['source_fact_id','source_file','source_page','model','configuration',
               'category','parameter','value','unit','method','source_label','source_value',
               'availability','human_verified'])
    typed_rows=write_typed_measurements(output,facts)
    coverage=table_cell_coverage(documents)
    write_csv(output/'TABLE_CELL_COVERAGE.csv',coverage,
              ['source_file','source_page','source_table','source_cells_total',
               'source_cells_nonempty','source_cells_with_facts',
               'source_cells_not_extracted','source_cells_blank_in_pdf','status'])
    controller_evidence=control_mentions(documents)
    write_csv(output/'CONTROL_EVIDENCE.csv',controller_evidence,
              ['source_file','source_page','candidate_models_same_page','source_quote',
               'status','automatic_model_assignment'])
    hidden=set(schema.config['hidden_wide_parameters'])
    columns=ordered_columns([colname(f) for f in facts],schema)
    filtered=[c for c in columns if c.split('] ',1)[-1] not in hidden]
    derived_columns=ordered_columns([f'[{d["category"]}] {d["parameter"]}' for d in derived],schema)
    derived_groups=defaultdict(list)
    for item in derived:
        derived_groups[(item['source_file'],item['model'],item['configuration'],
                        f'[{item["category"]}] {item["parameter"]}')].append(item)
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
                for col in derived_columns:
                    items=derived_groups.get((d['source_file'],model,config,col),[])
                    if not items:
                        # This column can already hold an axis-specific source fact.
                        # A derived value from another model must never erase it.
                        continue
                    displayed=[];base_only=[]
                    for item in items:
                        value=item['value']
                        if item['availability']=='optional_only':
                            if value+' [ОПЦИЯ]' not in displayed: displayed.append(value+' [ОПЦИЯ]')
                        else:
                            if value not in displayed: displayed.append(value)
                            if value not in base_only:base_only.append(value)
                    original_key = (d['source_file'], model, config, col)
                    if fact_groups.get(original_key):
                        if row.get(col) and row[col] not in displayed:
                            displayed.insert(0, row[col])
                        if clean.get(col) and clean[col] not in base_only:
                            base_only.insert(0, clean[col])
                    row[col]=' || '.join(displayed)
                    clean[col]=' || '.join(base_only) if len(base_only)<2 else ''
                configs.append(row)
                if config=='base':
                    base_rows.append(row);clean_rows.append(clean)
    # The legacy physical-machine base view is retained under a new explicit
    # name; the main wide view contains one row per base family.
    export_columns=ordered_columns(filtered+derived_columns,schema)
    write_csv(output/'ALL_MODEL_VARIANTS_wide.csv',base_rows,IDENTITY+export_columns)
    families,clean_families,family_metadata=aggregate_families(configs,IDENTITY,export_columns)
    write_csv(output/'ALL_MODELS_wide.csv',families,IDENTITY+export_columns)
    write_csv(output/'ALL_MODELS_wide_values.csv',clean_families,IDENTITY+export_columns)
    write_csv(output/'ALL_CONFIGURATIONS_wide.csv',configs,IDENTITY+['configuration']+export_columns)
    write_csv(output/'MODEL_FAMILIES.csv',family_metadata,
              ['source_file','manufacturer','base_model','machine_class','machine_type','physical_models',
               'physical_models_count','configurations','configuration_rows','merge_status','human_verified'])
    write_csv(output/'CLASSIFICATION_AUDIT.csv',[
      {'source_file':d['source_file'],'manufacturer':d.get('manufacturer',''),
       **{k:meta.get(k,'') for k in ('model','base_model','model_variant','machine_class','machine_type','class_evidence','model_family_rule')},
       'source_headers':meta.get('headers',[])}
      for d in documents for meta in d['registry'].values()],
      ['source_file','manufacturer','model','base_model','model_variant','machine_class','machine_type',
       'class_evidence','model_family_rule','source_headers'])
    atomic_json(output/'CLASSIFICATION_TREE.json',category_tree(schema))
    atomic_json(output/'PARAMETER_GROUPS.json',{'ordered_groups':categories,'parameter_order':param_orders})
    write_csv(output/'PARAMETER_SYNONYMS.csv',[
      {'parameter':rule['key'],'category':rule['category'],'canonical_unit':rule.get('unit',''),
       'source_label_alias':label,'section_scope':rule.get('section',''),'dictionary_source':rule.get('source','')}
      for rule in schema.config.get('parameter_synonyms',[]) for label in rule.get('labels',[])],
      ['parameter','category','canonical_unit','source_label_alias','section_scope','dictionary_source'])
    write_csv(output/'REVIEW_CORRECTIONS_AUDIT.csv',[
      item for doc in documents for item in doc.get('manual_correction_audit',[])],
      ['source_file','source_sha256','fact_id','model','parameter','original_value','corrected_value',
       'source_unit','reviewer','reason','status','message'])
    # Full per-series exports include the eight fields hidden in the user-facing
    # combined wide CSV. Nothing is deleted from detailed facts or provenance.
    series=defaultdict(list)
    for row in configs: series[row['model'].split()[0]].append(row)
    for name,rows in series.items():
        own=[c for c in columns if any((r['source_file'],r['model'],r['configuration'],c) in fact_groups for r in rows)]
        own_derived=[c for c in derived_columns if any(r.get(c,'') for r in rows)]
        write_csv(output/'by_series'/f'{re.sub(r"[^A-Z0-9_-]","_",name.upper())}_models.csv',rows,IDENTITY+['configuration']+ordered_columns(own+own_derived,schema))
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
    write_csv(output/'MODEL_FIELD_STATUS.csv',statuses,['source_file','model','base_model','model_variant','generation','configuration','parameter','condition','status','value','source_page','source_row_id','source_label','unit_source','value_raw','cell_bbox'])
    reviews=[r for d in documents for r in d['review']]
    write_csv(output/'CELL_ISSUES.csv',reviews,['source_file','source_page','model','base_model','model_variant','configuration','parameter','reason','source_row_id','fact_id','value_raw','cell_bbox','header_raw','models','instruction'])
    atomic_json(output/'NEEDS_REVIEW.json',reviews)
    atomic_json(output/'LOCAL_OCR_AUDIT.json',[event for d in documents for event in d.get('ocr_events',[])])
    atomic_json(output/'AI_QUEUE_SUMMARY.json',{'project_name':PROJECT_NAME,'version':PROJECT_VERSION,'mode':'local','enabled':False,'api_calls':0,'note':'Native PDF engine does not call external AI services.'})
    model_quality=[m for q in qualities for m in q['models']]
    write_csv(output/'MODEL_QUALITY.csv',model_quality,['source_file','model','base_model','model_variant','generation','configuration','machine_class','machine_type','facts','measurement_records','required_total','required_found','missing_required','optional_disclosed','source_blank_cells','warnings','extraction_gate_passed','quality_status','human_verified'])
    atomic_json(output/'QUALITY_REPORT.json',{'project_name':PROJECT_NAME,'project_version':PROJECT_VERSION,'quality_passed':bool(qualities) and all(q['quality_passed'] for q in qualities),
                                            'extraction_gate_passed':bool(qualities) and all(q['extraction_gate_passed'] for q in qualities),'documents':qualities,'human_verified':False})
    write_jsonl(output/'RAW_TABLES.jsonl',(r for d in documents for r in d.get('raw_tables',[])))
    write_jsonl(output/'SOURCE_PAGES.jsonl',(p for d in documents for p in d.get('source_pages',[])))
    write_csv(output/'PAGE_INDEX.csv',[{k:p[k] for k in ['source_file','page','kind','width_pt','height_pt']}|{'native_word_count':len(p.get('words',[]))} for d in documents for p in d.get('source_pages',[])],['source_file','page','kind','native_word_count','width_pt','height_pt'])
    write_csv(output/'UNMAPPED_FIELDS.csv',[r for d in documents for r in d.get('unmapped_fields',[])],['source_file','source_page','source_table','source_row','source_label','unit_source','cells'])
    atomic_json(output/'MODEL_REGISTRY.json',[{'source_file':d['source_file'],'models':d['registry']} for d in documents])
    for d in documents:
        short={k:v for k,v in d.items() if k not in {'source_pages','raw_tables','source_path'}}
        atomic_json(output/'documents'/(Path(d['source_file']).stem+'_report.json'),short)
    summary={'project_name':PROJECT_NAME,'project_version':PROJECT_VERSION,'files':len(documents),'models':len(families),'identified_model_designations':sum(len(d['registry']) for d in documents),
             'base_model_rows':len(families),'configuration_rows':len(configs),
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
             'unit_conversions':len(conversions),'derived_components':len(derived),
             'source_cells_nonempty':sum(c['source_cells_nonempty'] for c in coverage),
             'source_cells_not_extracted':sum(c['source_cells_not_extracted'] for c in coverage),
             'source_cells_blank_in_pdf':sum(c['source_cells_blank_in_pdf'] for c in coverage),
             'controller_narrative_evidence':len(controller_evidence),
             'normalization': normalization_summary,
             'local_only':True,'network_calls':0,'hidden_wide_parameters':sorted(hidden),'human_verified':False}
    atomic_json(output/'RUN_SUMMARY.json',summary)
    return summary
