"""Strict, additive machine_spec projection; existing exports/API are unchanged."""
from __future__ import annotations
import argparse
import hashlib
import json
import logging
import math
from pathlib import Path
import re

CONFIG = Path(__file__).resolve().parents[1]/'config/spec_contract/fields.json'
LOG = logging.getLogger(__name__)
NUMBER = r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'


def parse_expression(raw):
    s = str(raw).strip().replace('−','-').replace('–','~').replace('—','~')
    s = re.sub(r'\s+', ' ', s)
    approx = bool(re.search(r'около|approx|~\s*\d',s,re.I))
    s = re.sub(r'^(?:около|approximately|approx\.?)\s*','',s,flags=re.I)
    number = lambda x: float(x.replace(',',''))
    patterns = [
        (rf'({NUMBER})\s*±\s*({NUMBER})', 'tolerance'),
        (rf'(?:от\s*)?({NUMBER})\s*(?:~|to|до|\.\.|-)\s*({NUMBER})','range'),
        (rf'(?:≥|>=|мин\.?|min\.?|не менее)\s*({NUMBER})','minimum'),
        (rf'(?:≤|<=|до|макс\.?|max\.?)\s*({NUMBER})','maximum'),
        (rf'±\s*({NUMBER})','symmetric'),
        (rf'({NUMBER})','scalar')]
    for pattern,kind in patterns:
        match=re.match(r'^'+pattern+r'(?=\s|$|[A-Za-zА-Яа-я°])',s,re.I)
        if not match: continue
        suffix=s[match.end():].strip()
        if suffix and not re.fullmatch(r'[A-Za-zА-Яа-я°][\w/·*°-]*',suffix): continue
        values=[number(x) for x in match.groups()]
        if not all(math.isfinite(v) for v in values): break
        low=high=None; value=None
        if kind=='scalar': value=values[0]
        elif kind=='range': low,high=values
        elif kind=='tolerance': low,high=values[0]-values[1],values[0]+values[1]
        elif kind=='symmetric': low,high=-values[0],values[0]
        elif kind=='minimum': low=values[0]
        else: high=values[0]
        if low is not None and high is not None and low>high: raise ValueError('reversed_range')
        return {'value':value,'min':low,'max':high,'unit':suffix,'approximate':approx}
    raise ValueError('unparseable_expression')


def normalize(raw, descriptor, source_unit, config):
    result={k:None for k in ('value_num','value_min','value_max','value_text','value_enum')}
    result.update(unit=descriptor['unit'],raw=str(raw),confidence=1.0)
    reasons=[]; kind=descriptor['type']
    if kind=='text': result['value_text']=str(raw)
    elif kind=='enum':
        value=str(raw).upper().replace(' ','').replace('#','')
        value=re.sub(r'^HSKA','HSK-A',value)
        permitted=descriptor.get('enum',[])
        canonical=next((item for item in permitted if item.replace(' ','').upper()==value),None)
        if canonical is None: reasons.append('enum_not_in_approved_set'); result['value_text']=str(raw)
        else: result['value_enum']=canonical
    elif kind=='bool':
        value=str(raw).strip().casefold()
        if value in ('1','true','yes','да'): result['value_num']=1
        elif value in ('0','false','no','нет'): result['value_num']=0
        else: reasons.append('invalid_bool')
    else:
        try:
            parsed=parse_expression(raw)
            clean=lambda u: str(u or '').strip().casefold()
            declared=clean(source_unit); embedded=clean(parsed['unit'])
            # Explicit metric/imperial unit labels use the primary declared unit.
            declared=declared.strip('()') if declared.startswith('(') else declared.split('(',1)[0].strip()
            aliases=config['units'].get(descriptor['unit'],{})
            unit=embedded or declared
            scale=aliases.get(unit)
            if scale is None: reasons.append('unknown_or_incompatible_unit')
            elif embedded and declared and aliases.get(embedded)!=aliases.get(declared): reasons.append('conflicting_units')
            else:
                value,low,high=(parsed[k]*scale if parsed[k] is not None else None for k in ('value','min','max'))
                if kind=='range':
                    result['value_min']=value if value is not None else low
                    result['value_max']=value if value is not None else high
                elif value is not None: result['value_num']=value
                else:
                    result.update(value_min=low,value_max=high)
                    reasons.append('interval_in_number_field')
                if parsed['approximate']: reasons.append('approximate_value')
                rules=descriptor.get('validation',{})
                for v in (value,low,high):
                    if v is None: continue
                    if ('min' in rules and v<rules['min']) or ('exclusive_min' in rules and v<=rules['exclusive_min']) or ('max' in rules and v>rules['max']): reasons.append('physical_anomaly')
        except ValueError as exc: reasons.append(str(exc))
    if reasons:
        result['confidence']=0.4
        if all(result[k] is None for k in ('value_num','value_min','value_max','value_enum','value_text')): result['value_text']=str(raw)
    return result,sorted(set(reasons))


def project(documents, machine_types, config=None):
    config=config or json.loads(CONFIG.read_text())
    candidates={}; review=[]; counters={'found':0,'normalized':0,'discarded':0,'review':0}
    def issue(machine,field,raw,reason,**extra):
        review.append({'machine_id':machine,'field_id':field,'raw':raw,'reason':reason,**extra})
    for doc in documents:
        for fact in doc['facts']:
            counters['found']+=1
            model=fact['model']; source=doc['source_file']; configuration=fact.get('configuration','base')
            identity=(source,model,configuration)
            machine=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
            machine_type=machine_types.get(source,{}).get(model)
            field=config['mapping'].get(fact['parameter'])
            raw=str(fact.get('value_raw',fact.get('value','')))
            evidence={'source_file':source,'model':model,'configuration':configuration,'page':fact.get('source_page'),'bbox':fact.get('cell_bbox'),'unit_raw':fact.get('unit_source')}
            if field is None:
                counters['discarded']+=1; issue(machine,None,raw,'unmapped_parameter',parameter=fact['parameter'],**evidence); continue
            descriptor=config['fields'][field]
            if machine_type not in config['machine_types'] or machine_type not in descriptor['applies_to']:
                counters['discarded']+=1; issue(machine,field,raw,'unknown_machine_type' if machine_type not in config['machine_types'] else 'not_applicable',**evidence); continue
            value,reasons=normalize(fact.get('value',''),descriptor,fact.get('unit_source',''),config)
            if fact.get('warnings') or fact.get('options') or fact.get('condition'):
                value['confidence']=min(value['confidence'],0.4); reasons.append('source_warning_options_or_conditions')
            row={'machine_id':machine,'field_id':field,**value,'evidence':[evidence]}
            counters['normalized']+=1
            key=(machine,field)
            previous=candidates.get(key)
            if previous:
                keys=('value_num','value_min','value_max','value_text','value_enum','unit')
                if any(previous[k]!=row[k] for k in keys):
                    previous['confidence']=0.4; row['confidence']=0.4
                    issue(machine,field,raw,'conflicting_candidates',alternatives=[previous,row])
                    previous['conflict']=True
                else:
                    previous['confidence']=min(previous['confidence'],row['confidence'])
                    previous['evidence'].extend(row['evidence'])
            else: candidates[key]=row
            if value['confidence']<config['review_threshold']: issue(machine,field,raw,';'.join(sorted(set(reasons))),**evidence)
    accepted=[row for row in candidates.values() if row['confidence']>=config['review_threshold'] and not row.get('conflict')]
    counters['review']=len(review)
    LOG.info(json.dumps({'stage':'validate','counters':counters,'accepted':len(accepted)}))
    return {'machine_spec':accepted,'candidates':list(candidates.values()),'NEEDS_REVIEW':review,'counters':counters}


def persist(connection, rows, machine_ids):
    """Upsert into an EXISTING SQLite machine_spec contract; never create/alter it.

    Caller supplies the reviewed mapping from extraction identities to DB IDs.
    A UNIQUE(machine_id, field_id) constraint is required. Transaction is owned
    by the caller so evidence/review storage can participate in the same commit.
    """
    columns=('machine_id','field_id','value_num','value_min','value_max','value_text','value_enum','unit','raw','confidence')
    prepared=[]
    for row in rows:
        if row['confidence'] < .7 or row.get('conflict'):
            raise ValueError('Only accepted candidates may be persisted')
        mapped={**row,'machine_id':machine_ids[row['machine_id']]}
        prepared.append(tuple(mapped[k] for k in columns))
    updates=','.join(f'{k}=excluded.{k}' for k in columns[2:])
    connection.executemany('INSERT INTO machine_spec('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+') ON CONFLICT(machine_id,field_id) DO UPDATE SET '+updates,prepared)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--machine-types',type=Path,required=True,help='Explicit source_file -> model -> approved type mapping')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from .replay import load_run
    from .storage import atomic_json
    result=project(load_run(args.run_dir),json.loads(args.machine_types.read_text()))
    args.output.mkdir(parents=True,exist_ok=True)
    atomic_json(args.output/'machine_spec.json',result['machine_spec'])
    atomic_json(args.output/'NEEDS_REVIEW.json',result['NEEDS_REVIEW'])
    atomic_json(args.output/'candidates.json',result['candidates'])
    print(json.dumps(result['counters']))

if __name__=='__main__': main()
