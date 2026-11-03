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
    """Annotate quality idempotently; runtime success is a separate outcome."""
    from .sorting import natural_key
    generated = {'required_field_missing', 'unresolved_value_conflict'}
    doc['review'] = [r for r in doc['review'] if r.get('reason') not in generated]
    groups, by_model, cell_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    for fact in doc['facts']:
        # This warning belongs to this stage and must be recomputed on reruns.
        fact['warnings'] = [w for w in fact.get('warnings', []) if w != 'unresolved_value_conflict']
        identity = (fact['model'], fact.get('configuration', 'base'))
        by_model[identity].append(fact)
        groups[(*identity, fact['parameter'], fact.get('condition', ''))].append(fact)
    for cell in doc.get('cell_statuses', []):
        cell_groups[(cell['model'], cell.get('configuration', 'base'))].append(cell)
    for (model, config, parameter, condition), facts in groups.items():
        distinct = {str(f['value']) for f in facts if f.get('availability') != 'not_applicable'}
        if len(distinct) > 1:
            for fact in facts:
                fact['warnings'].append('unresolved_value_conflict')
                fact['review_status'] = 'NEEDS_REVIEW'
            doc['review'].append({'source_file': doc['source_file'], 'source_page': 0,
                'model': model, 'configuration': config, 'parameter': parameter,
                'condition': condition, 'reason': 'unresolved_value_conflict',
                'values': sorted(distinct, key=natural_key), 'stage': 'quality',
                'instruction': 'Conflicting source facts retained; confirmation required.'})
    collisions = {r.get('model') for r in doc['review'] if r.get('reason') == 'model_identity_collision'}
    models = []
    for model, meta in doc['registry'].items():
        machine_class = meta.get('machine_class') or doc.get('machine_class', 'unknown')
        required = schema.required(machine_class)
        for config in meta.get('configurations', ['base']):
            facts = by_model[(model, config)]
            if meta.get('class_evidence') == 'class_evidence_conflict':
                for fact in facts:
                    fact.update(machine_class='unknown', machine_type='Unclassified machine', review_status='NEEDS_REVIEW')
                    if 'model_class_conflict' not in fact['warnings']:
                        fact['warnings'].append('model_class_conflict')
            available = {f['parameter'] for f in facts if f.get('availability') not in {'not_applicable', 'missing', 'optional_only'}}
            missing = [p for p in required if p not in available]
            warnings = sorted({w for f in facts for w in f.get('warnings', [])}
                              | ({'model_identity_collision'} if model in collisions else set()))
            empty = sum(s.get('status') == 'SOURCE_BLANK' for s in cell_groups[(model, config)])
            source_supported = bool(doc.get('spec_pages')) or doc.get('input_kind') == 'structured'
            passed = bool(facts) and not missing and source_supported and model not in collisions
            for key in missing:
                doc['review'].append({'source_file': doc['source_file'], 'source_page': 0,
                    'model': model, 'configuration': config, 'parameter': key,
                    'reason': 'required_field_missing', 'stage': 'quality'})
            models.append({'source_file': doc['source_file'], 'model': model,
                'generation': meta.get('generation', ''), 'configuration': config,
                'machine_class': machine_class, 'machine_type': meta.get('machine_type', ''),
                'facts': len(facts), 'measurement_records': sum(f.get('availability') != 'not_applicable' for f in facts),
                'required_total': len(required), 'required_found': len(required)-len(missing),
                'missing_required': missing, 'source_blank_cells': empty, 'warnings': warnings,
                'extraction_gate_passed': passed,
                'quality_status': 'FAILED_EXTRACTION' if not passed else 'REVIEW_REQUIRED' if warnings or empty or doc['review'] else 'AUTOMATED_CHECKS_PASSED',
                'human_verified': False})
    extraction_passed = bool(models) and all(m['extraction_gate_passed'] for m in models)
    return {'source_file': doc['source_file'], 'machine_class': doc.get('machine_class', 'unknown'),
        'model_count': len(doc['registry']), 'configuration_count': len(models),
        'specification_pages': doc.get('spec_pages', []), 'facts': len(doc['facts']),
        'measurement_records': sum(f.get('availability') != 'not_applicable' for f in doc['facts']),
        'not_applicable_records': sum(f.get('availability') == 'not_applicable' for f in doc['facts']),
        'unmapped_rows': len(doc.get('unmapped_fields', [])),
        'review_counts': dict(Counter(r.get('reason', 'unknown') for r in doc['review'])),
        'extraction_gate_passed': extraction_passed,
        'quality_passed': extraction_passed and not doc['review'] and not doc.get('unmapped_fields')
                          and not any(m['warnings'] for m in models),
        'models': models, 'human_verified': False}
