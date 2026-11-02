"""Explicit human corrections with immutable raw evidence and an audit trail."""
from __future__ import annotations
import json
from pathlib import Path
from .values import option_parts


def load_corrections(path: Path|None):
    if not path or not path.is_file():return []
    data=json.loads(path.read_text(encoding='utf-8'))
    corrections=data.get('corrections',[])
    if not isinstance(corrections,list):raise ValueError('corrections must be an array')
    return corrections


def apply_corrections(document: dict, corrections: list[dict]):
    """Never alter value_raw, source_quote, coordinates or fact_id.

    A correction is accepted only with an exact physical fact ID, original
    document hash, reviewer and rationale. The corrected value uses the same
    source unit; unit changes require a schema rule, not a silent override.
    """
    audit=[];indexed={f['fact_id']:f for f in document['facts']}
    for item in corrections:
        if item.get('source_file')!=document['source_file']:continue
        status='NOT_APPLIED'; reason=''
        key=item.get('fact_id','');f=indexed.get(key)
        if not f:reason='fact_id_not_found'
        elif item.get('source_sha256')!=document.get('source_sha256'):reason='document_hash_mismatch'
        elif not str(item.get('reviewer','')).strip():reason='reviewer_missing'
        elif not str(item.get('reason','')).strip():reason='reason_missing'
        elif not str(item.get('corrected_value','')).strip():reason='corrected_value_missing'
        elif item.get('source_unit',f.get('unit_source',''))!=f.get('unit_source',''):reason='unit_changed_use_schema_override'
        else:
            original=f['value']
            corrected=str(item['corrected_value']).strip()
            f['value_extracted']=original
            f['value']=corrected
            f['base_value'],f['options'],f['availability']=option_parts(corrected,f['parameter'])
            f['manual_reviewer']=item['reviewer']
            f['manual_reason']=item['reason']
            f['review_status']='HUMAN_CORRECTED'
            f['human_verified']=True
            f.setdefault('transformations',[]).append('manual_correction_with_provenance')
            status='APPLIED';reason='verified_by_named_reviewer'
        audit.append({'source_file':document['source_file'],'source_sha256':document.get('source_sha256'),
                      'fact_id':key,'model':f.get('model','') if f else '',
                      'parameter':f.get('parameter','') if f else '',
                      'original_value':f.get('value_extracted',f.get('value','')) if f else '',
                      'corrected_value':item.get('corrected_value',''),
                      'source_unit':f.get('unit_source','') if f else '',
                      'reviewer':item.get('reviewer',''),'reason':item.get('reason',''),
                      'status':status,'message':reason})
        if status!='APPLIED':
            document['review'].append({'source_file':document['source_file'],'source_page':0,
                         'reason':'manual_correction_not_applied','fact_id':key,'instruction':reason})
    document['manual_correction_audit']=audit
    return audit
