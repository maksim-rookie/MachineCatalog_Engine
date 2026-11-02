from __future__ import annotations
from collections import Counter, defaultdict
import json
from .schema import Schema

BLOCKING_WARNINGS={'damaged_text','source_unit_conflict','source_value_tbd','unresolved_value_conflict',
                   'source_label_unit_context_disagreement','geometric_tail_row_recovered',
                   'source_metric_imperial_mismatch','model_identity_collision',
                   'magazine_configuration_unresolved','source_unit_unrecognized','source_unit_not_specified',
                   'reversed_range','negative_physical_value','ambiguous_column_alignment','model_class_conflict',
                   'parenthesized_value_semantics_unresolved'}


def check_document(doc: dict, schema: Schema) -> dict:
    """Runtime success, extraction coverage, and data quality are separate gates."""
    # Generated aggregate issues are recomputed; replay must be idempotent.
    doc['review'] = [r for r in doc.get('review', [])
                     if not (r.get('source_page') == 0 and r.get('reason') in
                             {'required_field_missing', 'unresolved_value_conflict'})]
    groups=defaultdict(list)
    entity_facts=defaultdict(list)
    entity_cells=defaultdict(list)
    for cell in doc.get('cell_statuses', []):
        entity_cells[(cell['model'], cell.get('configuration', 'base'))].append(cell)
    for f in doc['facts']:
        entity_facts[(f['model'], f.get('configuration', 'base'))].append(f)
        groups[(f['model'],f.get('configuration','base'),f['parameter'],f.get('condition',''))].append(f)
    for (model,config,param,condition),facts in groups.items():
        distinct={f['value'] for f in facts if f.get('availability')!='not_applicable'}
        if len(distinct)>1:
            for f in facts:
                f.setdefault('warnings',[])
                if 'unresolved_value_conflict' not in f['warnings']: f['warnings'].append('unresolved_value_conflict')
                f['review_status']='NEEDS_REVIEW'
            doc['review'].append({'source_file':doc['source_file'],'source_page':0,'model':model,
                'configuration':config,'parameter':param,'condition':condition,'reason':'unresolved_value_conflict',
                'values':sorted(distinct),'instruction':'Conflicting facts retained in long CSV; clean wide cell left blank.'})
    # A model-name collision is a data-integrity failure even if required fields exist.
    collisions={r.get('model') for r in doc['review'] if r['reason']=='model_identity_collision'}
    models=[]
    for model,meta in doc['registry'].items():
        machine_class=meta.get('machine_class',doc.get('machine_class','unknown'))
        required=schema.required(machine_class)
        alternatives=schema.required_alternatives(machine_class)
        if meta.get('class_evidence')=='class_evidence_conflict':
            for f in doc['facts']:
                if f['model']==model:
                    f.update(machine_class='unknown',machine_type='Unclassified machine',review_status='NEEDS_REVIEW')
                    if 'model_class_conflict' not in f.setdefault('warnings',[]):f['warnings'].append('model_class_conflict')
        for config in meta.get('configurations',['base']):
            facts=entity_facts[(model, config)]
            # Optional equipment *is documented* by the PDF even if not part
            # of the base configuration. Count disclosure toward presence,
            # but keep its optional status and human review requirement.
            available={f['parameter'] for f in facts if f.get('availability') not in {'not_applicable','missing'}}
            optional_disclosed=sorted({f['parameter'] for f in facts if f.get('availability')=='optional_only'})
            missing=[p for p in required if p not in available]
            missing.extend('any_of('+','.join(g)+')' for g in alternatives if not any(k in available for k in g))
            warnings=sorted({w for f in facts for w in f.get('warnings',[])} | ({'model_identity_collision'} if model in collisions else set()))
            cells=entity_cells[(model, config)]
            empty=sum(s['status']=='SOURCE_BLANK' for s in cells)
            passed=bool(required or alternatives) and not missing and bool(doc.get('spec_pages')) and model not in collisions
            for key in missing:
                doc['review'].append({'source_file':doc['source_file'],'source_page':0,'model':model,
                    'configuration':config,'parameter':key,'reason':'required_field_missing'})
            models.append({'source_file':doc['source_file'],'model':model,
                'base_model':meta.get('base_model',model),'model_variant':meta.get('model_variant','standard'),
                'generation':meta.get('generation',''),
                'configuration':config,'machine_class':machine_class,'machine_type':meta.get('machine_type',''),
                'facts':len(facts),'measurement_records':sum(f.get('availability')!='not_applicable' for f in facts),
                'required_total':len(required)+len(alternatives),'required_found':len(required)+len(alternatives)-len(missing),
                'missing_required':missing,'optional_disclosed':optional_disclosed,'source_blank_cells':empty,'warnings':warnings,
                'extraction_gate_passed':passed,
                'quality_status':'FAILED_EXTRACTION' if not passed else 'REVIEW_REQUIRED' if warnings or empty or doc.get('review') else 'AUTOMATED_CHECKS_PASSED',
                'human_verified':False})
    reasons=Counter(r['reason'] for r in doc['review'])
    extraction_passed=bool(models) and all(m['extraction_gate_passed'] for m in models)
    return {'source_file':doc['source_file'],'machine_class':doc.get('machine_class','unknown'),
            'model_count':len(doc['registry']),'configuration_count':len(models),
            'specification_pages':doc.get('spec_pages',[]),'facts':len(doc['facts']),
            'measurement_records':sum(f.get('availability')!='not_applicable' for f in doc['facts']),
            'not_applicable_records':sum(f.get('availability')=='not_applicable' for f in doc['facts']),
            'unmapped_rows':len(doc.get('unmapped_fields',[])),'review_counts':dict(reasons),
            'extraction_gate_passed':extraction_passed,
            'quality_passed':extraction_passed and not doc['review'] and not doc.get('unmapped_fields'),
            'models':models,'human_verified':False}
