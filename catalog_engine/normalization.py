"""Canonical parameter categories and explicit, auditable vector components."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from .values import value_warnings


def enrich_document(document, schema):
    facts=document['facts']
    existing={f['fact_id'] for f in facts}
    added=[]
    for fact in list(facts):
        fact['category']=schema.category_for(fact['parameter'],fact.get('category','Other'))
        if fact.get('parent_fact_id'):continue
        for rule in schema.config.get('compound_parameters',[]):
            if fact['parameter']!=rule['source_key']:continue
            text=unicodedata.normalize('NFKC',fact['value']).replace('−','-')
            values=[v.strip() for v in text.split('/')]
            embedded=None
            if rule.get('value_pattern'):
                match=re.fullmatch(rule['value_pattern'],text,re.I)
                if not match:continue
                embedded=rule['components']
                axes=[c['group'] for c in embedded];keys=[c['key'] for c in embedded]
                values=[match.group(c['group']) for c in embedded]
                unit=''
            elif rule.get('label_axes'):
                label=fact.get('source_label','')
                match=re.search(r'[XYZABC](?:\s*/\s*[XYZABC])+',label,re.I)
                if not match:continue
                axes=re.sub(r'\s+','',match.group()).upper().split('/')
                if len(set(axes))!=len(axes):continue
                if rule.get('units'):
                    suffix=rule['units'].get(fact.get('unit_source',''))
                    if suffix is None:continue
                    unit=fact['unit_source']
                else:suffix='';unit=rule['unit']
                keys=[rule['key_template'].format(axis=a.lower(),unit=suffix) for a in axes]
            else:
                axes=list(rule['axes']);keys=rule['keys'];unit=rule['unit']
                # Order must be printed in the source label, not inferred from
                # a model's name or the conventional meaning of a dimension.
                printed='/'.join(axes)
                if printed not in re.sub(r'\s+','',fact.get('source_label','')).upper():continue
            if len(values)!=len(axes) or not all(re.fullmatch(r'[+-]?\d+(?:\.\d+)?',v) for v in values):continue
            if fact.get('availability') in {'not_applicable','missing'}:continue
            for index,(axis,key,value) in enumerate(zip(axes,keys,values)):
                condition=dict(fact.get('condition_data',{}))
                if not embedded:condition.pop('axes',None);condition['axis']=axis
                expected=embedded[index]['unit'] if embedded else unit
                source_unit=expected if embedded else fact.get('unit_source','')
                method='explicit_embedded_units' if embedded else 'explicit_axis_vector_component'
                identity=json.dumps([fact['fact_id'],key,condition,value],sort_keys=True)
                fid=hashlib.sha256(identity.encode()).hexdigest()[:20]
                if fid in existing:continue
                optional=fact.get('availability')=='optional_only'
                component={**fact,'fact_id':fid,'parent_fact_id':fact['fact_id'],'parameter':key,
                    'category':rule['category'],'unit_expected':expected,'unit_source':source_unit,'value':value,
                    'base_value':'' if optional else value,'options':[value] if optional else [],
                    'condition_data':condition,'condition':'; '.join(f'{k}={v}' for k,v in sorted(condition.items())),
                    'transformations':list(fact.get('transformations',[]))+[method],
                    'derivation':method,
                    'warnings':sorted(set(fact.get('warnings',[]))|set(value_warnings(value,expected,source_unit,key)))}
                # The original full-cell text/bbox and parent fact are retained.
                added.append(component);existing.add(fid)
    facts.extend(added)
    for status in document.get('cell_statuses',[]):
        if status.get('parameter'):status['category']=schema.category_for(status['parameter'],status.get('category','Other'))
    return document
