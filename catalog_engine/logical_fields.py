"""Lossless adapter for historical field names and grouped presentation."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import re
from .canonical import clean_text, primary_expression, numeric_expression
from .units import unit_multiplier

FEED='rapid_traverse_xyz_m_min'
AXES={f'rapid_traverse_{a}_m_min':a.upper() for a in 'xyz'}
FEED_COLUMN='[Feedrate] '+FEED
FEED_NOTE=FEED_COLUMN+'__note'
LEGEND='- — не указано в каталоге; !- — требуется подтверждение'


def merge_axis_group(facts, general_keys, axis_keys, unit, rapid=False, missing=None):
    missing=missing or (lambda axis:'!-')
    display_unit={'m/min':'м/мин','mm/min':'мм/мин'}[unit]
    sets={a:set() for a in 'XYZ'};general=set();notes=[];uncertain={};bad_general=False;explicit_group=False
    for f in facts:
        key=f.get('parameter')
        if key not in set(general_keys)|set(axis_keys):continue
        raw=str(f.get('value',''))
        notes.append(f"{key}={raw} [{f.get('unit_source','')}]; page={f.get('source_page','')}; fact={f.get('fact_id','')}; model={f.get('model','')}; config={f.get('configuration','base')}")
        axis=axis_keys.get(key)
        if raw.strip() in {'','-','—'} or f.get('availability') in {'missing','not_applicable'}:
            if axis:uncertain[axis]=missing(axis)
            continue
        source_unit=str(f.get('unit_source','') or '');factor=unit_multiplier(source_unit,unit)
        parsed=numeric_expression(primary_expression(clean_text(raw),source_unit),factor) if factor is not None else None
        condition=str(f.get('condition','') or '')
        blocked=f.get('warnings') or (condition and not re.fullmatch(r'axes?=[XYZ/]+',condition,re.I)) or f.get('duty_cycle') or f.get('availability')=='optional_only'
        if not parsed or blocked:
            if axis:uncertain[axis]='!- ('+raw+' '+source_unit+')'
            else:bad_general=True;general.add(raw+' '+source_unit)
            continue
        value,kind=parsed
        if axis:sets[axis].add(value);continue
        labelled=bool(re.search(r'X\s*/\s*Y\s*/\s*Z',str(f.get('source_label','') or ''),re.I) or f.get('applies_to_all_axes') is True)
        if labelled and kind in {'SCALAR','RANGE','VECTOR'}:
            parts=[v.strip() for v in value.split('/')]
            if len(parts)==1:parts*=3
            if len(parts)!=3:bad_general=True;general.add(value);continue
            explicit_group=True
            for a,v in zip('XYZ',parts):sets[a].add(v)
        else:general.add(value)
    note='; '.join(notes)
    if not notes:return '!-','Наличие параметра в источнике не подтверждено.'
    known={a:next(iter(v)) for a,v in sets.items() if len(v)==1}
    common=next(iter(general)) if len(general)==1 else ''
    # A compact unqualified group scalar has no verified axis applicability.
    if general:
        if len(known)==3 and len(set(known.values()))==1 and common==next(iter(known.values())) and not uncertain and not bad_general:
            return 'X/Y/Z: '+common+' '+display_unit,note
        pieces=['общий: '+v for v in sorted(general)] + [f'{a}: {v}' for a,v in known.items()]
        pieces += [f'{a}: {v}' for a,v in uncertain.items()]
        return '!- ('+'; '.join(pieces)+') '+display_unit,'Неоднозначная применимость или конфликт. '+note
    if len(known)==3 and len(set(known.values()))==1 and not uncertain:
        return 'X/Y/Z: '+next(iter(known.values()))+' '+display_unit,note
    pieces=[]
    for a,values in sets.items():
        if len(values)>1:display='!- ('+' | '.join(sorted(values))+')'
        elif a in uncertain:display=uncertain[a]+(' [также: '+known[a]+']' if a in known else '')
        elif a in known:display=known[a]
        else:display=missing(a)
        pieces.append(f'{a}: {display}')
    return ('/ ' if rapid else '; ').join(pieces)+' '+display_unit,note


def merge_feedrate(facts,missing=None):
    return merge_axis_group(facts,{FEED},AXES,'m/min',True,missing)

CUT='cutting_feed_xyz_mm_min'
CUT_AXES={f'cutting_feed_{a}_{suffix}':a.upper() for a in 'xyz' for suffix in ('m_min','mm_min')}
CUT_KEYS={CUT,'cutting_feed_xyz_m_min'}
TABLE='table_pallet_size_mm'
TABLE_KEYS={'table_size_mm':'Стол','pallet_size_mm':'Паллета'}
TABLE_COMPONENTS={'table_dimension_1_mm':0,'table_dimension_2_mm':1}
REMOVED={'rapid_traverse_c_rpm','tool_magazine_type','tool_max_diameter_full_mm','tool_max_diameter_empty_mm',
         'travel_b_deg','travel_a_deg','travel_c_deg','spindle_nose_to_table_center_mm'}


def merge_cutting_feed(facts,missing=None):
    return merge_axis_group(facts,CUT_KEYS,CUT_AXES,'mm/min',False,missing)


def merge_table_size(facts):
    values={};notes=[];components={}
    for f in facts:
        key=f.get('parameter')
        if key not in set(TABLE_KEYS)|set(TABLE_COMPONENTS):continue
        raw=str(f.get('value',''));unit=str(f.get('unit_source','') or '')
        notes.append(f"{key}={raw} [{unit}]; page={f.get('source_page','')}; fact={f.get('fact_id','')}")
        parsed=numeric_expression(primary_expression(clean_text(raw),unit),unit_multiplier(unit,'mm')) if unit_multiplier(unit,'mm') is not None else None
        if not parsed:
            from .dimensions_view import numbers
            partial=numbers(f,2) or numbers(f,3)
            if partial:parsed=(' × '.join('!-' if v=='-' and not (f.get('source_absence_verified') is True and f.get('evidence')) else v for v in partial),'DIMENSIONS')
        display=parsed[0] if parsed and not f.get('warnings') and not f.get('condition') and f.get('availability')!='optional_only' else '!- ('+raw+' '+unit+')'
        if raw in {'','-'}:display='-' if f.get('source_absence_verified') is True and f.get('evidence') else '!-'
        if key in TABLE_COMPONENTS:components.setdefault(TABLE_COMPONENTS[key],set()).add(display)
        else:values.setdefault(key,set()).add(display)
    if components:
        dimensions=[]
        for i in (0,1):
            entries=components.get(i,set())
            dimensions.append(next(iter(entries)) if len(entries)==1 else '!- ('+' | '.join(sorted(entries))+')' if entries else '!-')
        values.setdefault('table_size_mm',set()).add(' × '.join(dimensions))
    parts=[]
    for key,label in TABLE_KEYS.items():
        if key not in values:continue
        entries=sorted(values[key]);display=entries[0] if len(entries)==1 else '!- ('+' | '.join(entries)+')'
        parts.append(label+': '+display+(' мм' if display not in {'-','!-'} else ''))
    return '; '.join(parts) or '!-','; '.join(notes)


def normalize_logical_records(records, definitions=None):
    """Return canonical logical records with immutable historical source records.

    Inputs can be extracted facts, normalized records, or this function's output.
    Repeating the adapter is idempotent; original provenance is retained verbatim.
    """
    from .dimensions_view import KEY,PARTS,merge_dimensions
    if definitions is None:
        from .schema import Schema
        definitions=Schema().config['normalization']['logical_fields']
    aliases={alias:target for target,spec in definitions.items() for alias in [target]+spec.get('aliases',[])}
    groups={};others=[]
    for source in records:
        key=source.get('parameter',source.get('canonical_parameter','')).split('] ',1)[-1]
        target=aliases.get(key)
        if target is None:others.append(deepcopy(source));continue
        entity=(source.get('source_file',''),source.get('model',source.get('source_model','')),source.get('configuration','base'),target)
        groups.setdefault(entity,[]).append(source)
    result=list(others)
    for entity,items in groups.items():
        if len(items)==1 and items[0].get('logical_canonical') and items[0].get('logical_revision')==2 and items[0].get('parameter')==entity[-1] and items[0].get('unit')==definitions[entity[-1]]['unit']:
            result.append(deepcopy(items[0]));continue
        originals=[];seen=set()
        for item in items:
            for original in item.get('source_records',[item]):
                signature=json.dumps(original,sort_keys=True,ensure_ascii=False)
                if signature not in seen:seen.add(signature);originals.append(deepcopy(original))
        facts=[]
        for r in originals:
            f=deepcopy(r)
            f['parameter']=r.get('parameter',r.get('canonical_parameter','')).split('] ',1)[-1]
            f['value']=r.get('value',r.get('source_value',r.get('normalized_value','')))
            f['unit_source']=r.get('unit_source',r.get('source_unit',''))
            if r.get('is_component'):
                f['value']=r.get('normalized_value','');f['unit_source']=r.get('canonical_unit','')
            facts.append(f)
        target=entity[-1]
        value,note=merge_dimensions(facts) if target==KEY else merge_cutting_feed(facts) if target==CUT else merge_table_size(facts) if target==TABLE else merge_feedrate(facts)
        if originals and all(r.get('source_absence_verified') is True and r.get('evidence') for r in originals):value='-'
        identity=json.dumps([entity,originals],sort_keys=True,ensure_ascii=False)
        result.append({'fact_id':hashlib.sha256(identity.encode()).hexdigest()[:20],
            'source_file':entity[0],'model':entity[1],'configuration':entity[2],
            'parameter':entity[3],'canonical_parameter':entity[3],'value':value,'display_value':value+(' мм' if entity[-1]==KEY and value not in {'-','!-'} else ''),
            'unit':definitions[entity[-1]]['unit'],'value_kind':'text','note':note,'logical_canonical':True,'logical_revision':2,
            'status':'REVIEW_REQUIRED' if '!-' in value else 'NOT_SPECIFIED' if value=='-' else 'MERGED_UNVERIFIED',
            'source_records':originals})
    return result
