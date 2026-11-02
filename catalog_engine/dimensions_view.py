"""Evidence-aware final dimensions view; original facts are never changed."""
from __future__ import annotations
import csv
import json
import re
from pathlib import Path
from .canonical import clean_text, primary_expression
from .units import unit_multiplier
from .storage import write_csv, atomic_json

KEY='machine_dimensions_mm'
PARTS={f'machine_dimension_{i}_mm' for i in (1,2,3)}
NAMED={'machine_length_mm':0,'machine_width_mm':1,'machine_height_mm':2}
COLUMN='[Machine dimensions] '+KEY
NOTE=COLUMN+'__note'
MISSING='[НЕ ИЗВЛЕЧЕНО ИЛИ НЕ УКАЗАНО В КАТАЛОГЕ]'
NUMBER=r'\d+(?:\.\d+)?'
EXCLUDED=re.compile(r'pack(?:ing|age)|shipping|transport|work(?:ing)?\s*(?:area|zone|envelope)|упаков|транспорт|рабоч.*(?:зон|област)|包装|运输|運輸|加工范围|工作台',re.I)


def absence_marker(doc,model,key,config=''):
    for proof in doc.get('absence_verifications',[])+doc.get('cell_statuses',[]):
        if (proof.get('model')==model and proof.get('parameter')==key
                and (not config or proof.get('configuration','base')==config)
                and proof.get('source_absence_verified') is True and proof.get('evidence')):
            return '-'
    return '!-'


def role(text):
    patterns=[r'\blength\b|длин|(?<![\u4e00-\u9fff])[长長](?:度)?',
              r'\bwidth\b|ширин|[宽寬](?:度)?',r'\bheight\b|высот|高(?:度)?']
    found=[i for i,p in enumerate(patterns) if re.search(p,text,re.I)]
    return found[0] if len(found)==1 else None


def order(fact):
    declared=fact.get('dimension_order')
    if declared:
        names={'length':0,'width':1,'height':2,'l':0,'w':1,'h':2}
        seq=declared if isinstance(declared,list) else re.split(r'\s*[xX×/,]\s*',declared)
        result=[names.get(str(s).lower()) for s in seq]
        if result and None not in result and len(set(result))==len(result):return result
    text=clean_text(str(fact.get('source_label','') or ''))
    match=re.search(r'\b([LWH])\s*[xX×/]\s*([LWH])\s*[xX×/]\s*([LWH])\b',text,re.I)
    if match and len(set(s.upper() for s in match.groups()))==3:return ['LWH'.index(s.upper()) for s in match.groups()]
    labels=[(m.start(),role(m.group())) for m in re.finditer(r'length|width|height|длина|ширина|высота|[长長宽寬高]',text,re.I)]
    result=[r for _,r in labels]
    return result if result and len(result)<=3 and len(set(result))==len(result) else None


def numbers(fact,count):
    unit=str(fact.get('unit_source','') or '')
    raw=primary_expression(clean_text(str(fact.get('value',''))),unit)
    suffix=re.search(r'\s*(mm|cm|m|毫米|厘米|米)\s*$',raw,re.I)
    if suffix:unit=suffix.group(1);raw=raw[:suffix.start()].strip()
    factor=unit_multiplier(unit,'mm')
    if factor is None:return None
    token=rf'(?:{NUMBER}|!-|-)' if count>=2 else NUMBER
    if not re.fullmatch(token+(r'\s*[xX×*]\s*'+token)*(count-1),raw):return None
    return tuple(n if n in {'-','!-'} else format(float(n)*factor,'.12g') for n in re.split(r'\s*[xX×*]\s*',raw))


def merge_dimensions(facts,missing=None):
    missing=missing or (lambda index:'!-')
    relevant=[f for f in facts if f.get('parameter') in PARTS|{KEY}|set(NAMED)]
    declared_orders=[order(f) for f in relevant if f.get('parameter')==KEY and order(f)]
    source_order=declared_orders[0] if declared_orders and all(o==declared_orders[0] for o in declared_orders) else None
    notes=[];generals=[];components={};bad=False
    for f in relevant:
        label=' / '.join(str(f.get(k,'')) for k in ('source_label','source_group','measurement_scope'))
        ref=f'{f.get("model", "")}/{f.get("configuration", "base")}, page {f.get("source_page", "?")}, {f.get("fact_id", "")}'
        notes.append(f'{f["parameter"]}={f.get("value", "")} [{f.get("unit_source", "")}] ({label}; {ref})')
        if EXCLUDED.search(label):
            notes[-1]='ИСКЛЮЧЕНО (упаковка/транспорт/рабочая зона): '+notes[-1];continue
        if f.get('availability') in {'not_applicable','missing'} or str(f.get('value','')).strip() in {'','-','—'}:continue
        if f.get('warnings') or f.get('availability')=='optional_only' or f.get('condition') or f.get('duty_cycle'):
            bad=True;continue
        if f['parameter']==KEY:
            ns=numbers(f,3) or numbers(f,2)
            if ns is None:bad=True;continue
            ns=tuple(missing(i) if v=='-' and not (f.get('source_absence_verified') is True and f.get('evidence')) else v for i,v in enumerate(ns))
            # Component positions are the source sequence, not inferred L/W/H.
            if len(ns)==2:ns=(*ns,missing(2))
            if not order(f):notes.append('Порядок источника сохранён; длина/ширина/высота компонентам не назначены.')
            generals.append(ns)
        else:
            ns=numbers(f,1)
            axis=int(f['parameter'].split('_')[-2])-1 if f['parameter'] in PARTS else NAMED.get(f['parameter'])
            if f['parameter'] in NAMED and source_order:
                axis=source_order.index(axis) if axis in source_order else len(source_order)
            if ns is None or axis is None:bad=True;continue
            components.setdefault(axis,set()).add(ns[0])
    if not relevant:return '!-','Нет извлечённых габаритов; отсутствие в каталоге не подтверждено.'
    general_values=[{g[i] for g in generals if g[i] not in {'-','!-'}} for i in range(3)]
    if bad or any(len(v)>1 for v in general_values) or any(len(v)>1 for v in components.values()):
        return '!-','Неоднозначность или конфликт габаритов. '+'; '.join(notes)
    values=[next(iter(v)) if v else (generals[0][i] if generals else missing(i)) for i,v in enumerate(general_values)]
    for axis,options in components.items():
        value=next(iter(options))
        if generals and values[axis] not in {'-','!-',value}:return '!-','Конфликт общего поля и отдельных размеров. '+'; '.join(notes)
        values[axis]=value
    if not generals and not components:return '!-','Габариты не подтверждены. '+'; '.join(notes)
    return ' × '.join(values),'; '.join(notes)


def finalize_wide_tables(output,documents,schema=None):
    """Change only dimensions columns and the explicit missing placeholder."""
    from .logical_fields import FEED,AXES,FEED_COLUMN,FEED_NOTE,LEGEND,merge_feedrate,normalize_logical_records
    from .presentation_policy import prepare,fill,archive_hidden,parameter
    from .report_layout import ordered_columns
    from .schema import Schema
    schema=schema or Schema()
    docs={d['source_file']:d for d in documents};audit=[];feed_audit=[]
    paths=list(output.glob('*wide*.csv'))+list(output.glob('ALL_MODELS_NORMALIZED_*.csv'))+[output/'NORMALIZED_CELL_STATUS.csv']+list((output/'by_series').glob('*.csv'))
    for path in sorted(set(paths)):
        if path.name not in {'ALL_MODELS_NORMALIZED_with_sources.csv','ALL_MODELS_NORMALIZED_values.csv','NORMALIZED_CELL_STATUS.csv'} and 'wide' not in path.name and path.parent.name!='by_series':continue
        if not path.exists():continue
        with path.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);fields=list(reader.fieldnames or []);rows=list(reader)
        dimension_cols=[c for c in fields if c.split('] ',1)[-1].split('__',1)[0] in PARTS|{KEY}]
        has_dimensions=bool(dimension_cols)
        feed_cols=[c for c in fields if c.split('] ',1)[-1].split('__',1)[0] in set(AXES)|{FEED}]
        has_feed=bool(feed_cols)
        removed=[c for c in fields if c.split('] ',1)[-1].split('__',1)[0] in PARTS]
        removed += [c for c in fields if c.split('] ',1)[-1].split('__',1)[0] in AXES]
        fields=[c for c in fields if c not in removed]
        if has_feed:
            if FEED_COLUMN not in fields:fields.append(FEED_COLUMN)
            if FEED_NOTE not in fields:fields.append(FEED_NOTE)
        if has_dimensions:
            if COLUMN not in fields:fields.append(COLUMN)
            if NOTE not in fields:fields.append(NOTE)
        fields,has_cut,has_table=prepare(fields)
        for row in rows:
            doc=docs.get(row.get('source_file'),{});model=row.get('model','');config=row.get('configuration','')
            family=path.name in {'ALL_MODELS_wide.csv','ALL_MODELS_wide_values.csv'}
            models={model}
            if family:
                models|={m for m,meta in doc.get('registry',{}).items() if meta.get('base_model')==model}
            facts=[f for f in doc.get('facts',[]) if f.get('model') in models and (not config or f.get('configuration','base')==config)]
            for key,value in list(row.items()):
                if value==MISSING:
                    parameter_key=key.split('] ',1)[-1]
                    row[key]=absence_marker(doc,model,parameter_key,config)
            if has_dimensions:
                original={c:row.get(c,'') for c in dimension_cols if row.get(c,'') and '__' not in c}
                value,note=merge_dimensions(facts,lambda i:absence_marker(doc,model,f'machine_dimension_{i+1}_mm',config))
                if not any(f.get('parameter') in PARTS|{KEY}|set(NAMED) for f in facts) and absence_marker(doc,model,KEY,config)=='-':
                    value='-';note='Отсутствие габаритов подтверждено проверкой источника.'
                if value=='!-':
                    previous=row.get(NOTE,'').split('; Исходные ячейки таблицы: ',1)
                    note+='; Исходные ячейки таблицы: '+(previous[1] if len(previous)==2 else json.dumps(original,ensure_ascii=False))
                row[COLUMN]=value+(' мм' if value not in {'!-','-'} else '');row[NOTE]=note
                # Provenance of removed components remains in the note and raw facts.
                if COLUMN+'__source_value' in fields:
                    row[COLUMN+'__source_value']=' || '.join(str(f.get('value_raw',f.get('value',''))) for f in facts if f.get('parameter') in PARTS|{KEY})
                    row[COLUMN+'__unit']='mm' if value!='!-' else ''
                    row[COLUMN+'__source_unit']=' || '.join(dict.fromkeys(f.get('unit_source','') for f in facts if f.get('parameter') in PARTS|{KEY}))
                    row[COLUMN+'__source_ref']=' || '.join(f"{f.get('source_file','')}:page={f.get('source_page','')}:fact={f.get('fact_id','')}" for f in facts if f.get('parameter') in PARTS|{KEY})
                    row[COLUMN+'__status']='REVIEW_REQUIRED' if value=='!-' or '!-' in value else 'DIMENSIONS_MERGED_UNVERIFIED'
                if path.name=='NORMALIZED_CELL_STATUS.csv':row[COLUMN]='REVIEW_REQUIRED' if '!-' in value else 'NOT_SPECIFIED' if value=='-' else 'MERGED_UNVERIFIED'
                audit.append({'table':path.name,'source_file':row.get('source_file',''),'model':model,'configuration':config,'value':value,'note':note})
            if has_feed:
                value,note=merge_feedrate(facts,lambda a:absence_marker(doc,model,f'rapid_traverse_{a.lower()}_m_min',config))
                if not any(f.get('parameter') in set(AXES)|{FEED} for f in facts) and absence_marker(doc,model,FEED,config)=='-':value='-'
                row[FEED_COLUMN]=value;row[FEED_NOTE]=note
                if FEED_COLUMN+'__source_value' in fields:
                    fs=[f for f in facts if f.get('parameter') in set(AXES)|{FEED}]
                    row[FEED_COLUMN+'__unit']='m/min' if value not in {'!-','-'} else ''
                    row[FEED_COLUMN+'__source_value']=' || '.join(str(f.get('value_raw',f.get('value',''))) for f in fs)
                    row[FEED_COLUMN+'__source_unit']=' || '.join(dict.fromkeys(f.get('unit_source','') for f in fs))
                    row[FEED_COLUMN+'__source_ref']=' || '.join(f"{f.get('source_file','')}:page={f.get('source_page','')}:fact={f.get('fact_id','')}" for f in fs)
                    row[FEED_COLUMN+'__status']='REVIEW_REQUIRED' if '!-' in value else 'MERGED_UNVERIFIED'
                if path.name=='NORMALIZED_CELL_STATUS.csv':row[FEED_COLUMN]='REVIEW_REQUIRED' if '!-' in value else 'NOT_SPECIFIED' if value=='-' else 'MERGED_UNVERIFIED'
                feed_audit.append({'table':path.name,'source_file':row.get('source_file',''),'model':model,'value':value,'note':note})
            fill(row,fields,facts,has_cut,has_table,path.name=='NORMALIZED_CELL_STATUS.csv',lambda a:'-' if any(absence_marker(doc,model,f'cutting_feed_{a.lower()}_{suffix}',config)=='-' for suffix in ('m_min','mm_min')) else '!-')
        identity=[c for c in fields if not c.startswith('[')]
        fields=identity+ordered_columns([c for c in fields if c.startswith('[')],schema)
        write_csv(path,rows,fields)
    write_csv(output/'DIMENSIONS_MERGE_AUDIT.csv',audit,['table','source_file','model','configuration','value','note'])
    atomic_json(output/'DIMENSIONS_MERGE_SUMMARY.json',{'rows_checked':len(audit),'review_rows':sum('!-' in r['value'] for r in audit),
                'absence_policy':'- requires source_absence_verified=true and evidence; otherwise !-'})

    write_csv(output/'FEEDRATE_MERGE_AUDIT.csv',feed_audit,['table','source_file','model','value','note'])
    logical=normalize_logical_records([f for d in documents for f in d.get('facts',[])],schema.config['normalization']['logical_fields'])
    atomic_json(output/'LOGICAL_PARAMETERS.json',logical)
    write_csv(output/'LOGICAL_PARAMETERS.csv',[r for r in logical if r.get('logical_canonical')],['source_file','model','configuration','parameter','value','unit','status','note','source_records'])
    from .schema import Schema
    definitions=schema.config['normalization']['logical_fields']
    atomic_json(output/'FIELD_DISPLAY.json',{'legend':LEGEND,'fields':{k:{'label':v['label'],'unit':v['unit']} for k,v in definitions.items()}})
    (output/'DATA_LEGEND.txt').write_text(LEGEND+'\n',encoding='utf-8')

    dictionary=output/'NORMALIZED_PARAMETER_DICTIONARY.csv'
    if dictionary.exists():
        with dictionary.open(encoding='utf-8-sig',newline='') as stream: entries=list(csv.DictReader(stream))
        from .logical_fields import CUT,CUT_AXES,CUT_KEYS,TABLE,TABLE_KEYS,TABLE_COMPONENTS,REMOVED
        excluded=PARTS|set(AXES)|{KEY,FEED}|set(CUT_AXES)|CUT_KEYS|{TABLE}|set(TABLE_KEYS)|set(TABLE_COMPONENTS)|REMOVED
        entries=[r for r in entries if parameter(r['column']) not in excluded]
        entries += [{'column':f'[{"Machine dimensions" if key==KEY else "Table" if key==TABLE else "Feedrate"}] '+key,'canonical_unit':spec['unit'],'display_name':spec['label']} for key,spec in definitions.items()]
        write_csv(dictionary,entries,['column','canonical_unit','display_name'])

    archive_hidden(output,documents)
