"""One lossless JSON dataset; one optional final CSV, with shared ordering."""
from __future__ import annotations

from collections import Counter, defaultdict
import logging
import math
from pathlib import Path
import re

from . import ENGINE_VERSION, PROJECT_NAME, PROJECT_VERSION
from .presentation import LEGEND, hidden_parameters, project_cells, target_parameter
from .quality import BLOCKING_WARNINGS
from .sorting import CATEGORY_ORDER, category_key, fact_key, natural_key, sort_model_rows
from .storage import atomic_json, write_csv
from .validation import normalize_document
from .values import range_bounds, unit_multiplier

LOG = logging.getLogger(__name__)
IDENTITY = ['manufacturer', 'machine_class', 'machine_type', 'model',
            'generation', 'source_file', 'configuration']


def typed_number(value, expected_unit):
    """A source scalar; reject vectors, conditions and unsupported notation."""
    from .values import unit_family
    if not unit_family(expected_unit) or unit_family(expected_unit) == 'inch':
        return None
    match = re.fullmatch(
        r'([+-]?(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?)'
        r'(?:\s*\([+-]?(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?\))?',
        ('' if value is None else str(value)).strip().replace('−', '-'))
    if not match:
        return None
    number = float(match[1].replace(',', ''))
    return number if math.isfinite(number) else None


def typed_measurements(facts):
    """Numeric JSON values are numbers or null, never display markers."""
    rows = []
    for fact in facts:
        value = typed_number(fact.get('base_value'), fact.get('unit_expected', ''))
        scale = unit_multiplier(fact.get('unit_source', ''), fact.get('unit_expected', ''))
        bounds = range_bounds(fact.get('value', ''))
        lower = upper = None
        availability = fact.get('availability')
        if availability == 'not_applicable':
            status = 'NOT_APPLICABLE'; value = None
        elif availability == 'optional_only':
            status = 'OPTIONAL_ONLY'; value = None
        elif set(fact.get('warnings', [])) & BLOCKING_WARNINGS:
            status = 'BLOCKED_REVIEW'; value = None
        elif scale is None and (value is not None or bounds):
            status = 'BLOCKED_REVIEW'; value = None
        elif bounds:
            status = 'RANGE_UNVERIFIED'; value = None
            lower, upper = (n * scale for n in bounds)
        elif value is None:
            status = 'NOT_SCALAR'
        else:
            status = 'AUTO_CHECKED_UNVERIFIED'; value *= scale
            if not math.isfinite(value):
                status = 'BLOCKED_REVIEW'; value = None
        rows.append({'fact_id': fact['fact_id'], 'numeric_value': value,
                     'numeric_unit': fact.get('unit_expected', ''), 'range_min': lower,
                     'range_max': upper, 'unit_conversion_factor': scale, 'typing_status': status})
    return rows


def column_definitions(documents, schema, facts):
    order = schema.config.get('category_order', CATEGORY_ORDER)
    categories, units = {}, {}
    # Earlier rules win, matching the schema's documented precedence.
    for rule in reversed(schema.config['rules']):
        key = target_parameter(rule['key'])
        categories[key], units[key] = rule['category'], rule['unit']
    for doc in documents:
        for row in doc['facts'] + doc.get('cell_statuses', []):
            if row.get('parameter'):
                key = target_parameter(row['parameter'])
                categories.setdefault(key, row.get('category') or 'Other')
                units.setdefault(key, row.get('unit_expected', ''))
    units['cutting_feed_xyz_mm_min'] = 'mm/min'
    machines = {(d['source_file'], model, config) for d in documents
                for model, meta in d['registry'].items() for config in meta['configurations']}
    hidden = hidden_parameters(facts, schema.config.get('hidden_wide_parameters', []), machines)
    if not any(f['parameter'] == 'travel_xyz_mm' for f in facts):
        categories.pop('travel_xyz_mm', None)
    keys = sorted((k for k in categories if k not in hidden),
                  key=lambda k: (category_key(categories[k], order), natural_key(k)))
    columns = [{'key': k, 'category': categories[k], 'unit': units[k],
                'label': f'[{categories[k]}] {k}'} for k in keys]
    b_populated = {(f['source_file'], f['model'], f['configuration']) for f in facts
                   if f['parameter'] == 'rapid_traverse_b_rpm' and f['value']
                   and f.get('availability') not in {'missing', 'not_applicable'}}
    audit = {'hidden_parameters': sorted(hidden), 'registered_configurations': len(machines),
             'rapid_traverse_b_rpm': {'populated': len(b_populated),
                                      'fill_ratio': len(b_populated)/len(machines) if machines else None,
                                      'hidden': 'rapid_traverse_b_rpm' in hidden},
             'raw_facts_retained': True}
    return columns, audit


def project_rows(documents, columns, sort_by=None):
    parameters = [c['key'] for c in columns]
    rows = []
    for doc in documents:
        groups, statuses = defaultdict(list), defaultdict(list)
        for fact in doc['facts']:
            groups[(fact['model'], fact['configuration'])].append(fact)
        for status in doc['cell_statuses']:
            statuses[(status['model'], status['configuration'])].append(status)
        for model, meta in doc['registry'].items():
            for configuration in meta['configurations']:
                identity = {k: meta.get(k, doc.get(k, '')) for k in IDENTITY}
                identity.update(model=model, configuration=configuration, source_file=doc['source_file'])
                cells = project_cells(groups[(model, configuration)], statuses[(model, configuration)], parameters)
                projected = {}
                for key in parameters:
                    cell = cells[key]
                    sources = cell.audit
                    reasons = sorted({w for f in sources for w in f.get('warnings', [])} | set(cell.issues))
                    if not sources and cell.status == 'REVIEW':
                        reasons.append('missing_source_evidence')
                    pending = bool(sources) and any(not f.get('human_verified', False) for f in sources)
                    numbers = [float(n) for n in cell.numbers]
                    if not all(math.isfinite(n) for n in numbers):
                        numbers = []
                        reasons.append('numeric_out_of_range')
                        cell.status = 'REVIEW'
                    projected[key] = {'value': cell.value or None, 'display': cell.render(),
                                      'status': cell.status, 'numeric_components': numbers,
                                      'review_reasons': reasons, 'human_verification_pending': pending,
                                      'source_fact_ids': list(dict.fromkeys(f['fact_id'] for f in sources))}
                rows.append({**identity, 'cells': projected})
    return sort_model_rows(rows, sort_by)


def build_dataset(documents, schema, qualities, sort_by=None):
    """Pure assembly: never mutate source documents or reuse rendered cells."""
    docs = [normalize_document(d, schema) for d in documents]
    order = schema.config.get('category_order', CATEGORY_ORDER)
    facts = sorted((f for d in docs for f in d['facts']), key=lambda f: fact_key(f, order))
    columns, column_audit = column_definitions(docs, schema, facts)
    if sort_by and sort_by not in {c['key'] for c in columns}:
        raise ValueError(f'Unknown or hidden sort parameter: {sort_by}')
    rows = project_rows(docs, columns, sort_by)
    typed = typed_measurements(facts)
    reviews = [r for d in docs for r in d['review']]
    cells = [cell for row in rows for cell in row['cells'].values()]
    populated = sum(bool(cell['source_fact_ids']) for cell in cells)
    with_issues = sum(bool(cell['review_reasons']) and bool(cell['source_fact_ids']) for cell in cells)
    summary = {
        'project_name': PROJECT_NAME, 'project_version': PROJECT_VERSION,
        'files': len(docs), 'models': sum(len(d['registry']) for d in docs),
        'configuration_rows': len(rows), 'facts': len(facts),
        'measurement_records': sum(f.get('availability') != 'not_applicable' for f in facts),
        'not_applicable_records': sum(f.get('availability') == 'not_applicable' for f in facts),
        'source_blank_cells': sum(s.get('status') == 'SOURCE_BLANK' for d in docs for s in d['cell_statuses']),
        'specification_tables': sum(len(d['raw_tables']) for d in docs),
        'unmapped_rows': sum(len(d['unmapped_fields']) for d in docs),
        'unsupported_files': [d['source_file'] for d in docs if not d['registry']],
        'review_rows': len(reviews), 'review_reason_counts': dict(Counter(r.get('reason', 'unknown') for r in reviews)),
        'extraction_gate_passed': bool(qualities) and all(q['extraction_gate_passed'] for q in qualities),
        'quality_passed': bool(qualities) and all(q['quality_passed'] for q in qualities),
        'typed_numeric_records': sum(r['typing_status'] == 'AUTO_CHECKED_UNVERIFIED' for r in typed),
        'typed_range_records': sum(r['typing_status'] == 'RANGE_UNVERIFIED' for r in typed),
        'optional_records': sum(f.get('availability') == 'optional_only' for f in facts),
        'hidden_wide_parameters': column_audit['hidden_parameters'],
        'populated_cells': populated, 'cells_with_source_issues': with_issues,
        'cells_without_source_evidence': len(cells) - populated,
        'human_verification_pending': sum(c['human_verification_pending'] for c in cells),
        'local_only': True, 'network_calls': 0, 'human_verified': False,
    }
    return {'format_version': 1, 'project_version': PROJECT_VERSION, 'engine_version': ENGINE_VERSION,
            'schema': schema.config, 'sort': {'category_order': list(order), 'row_parameter': sort_by}, 'legend': LEGEND,
            'columns': columns, 'rows': rows, 'facts': facts, 'measurements': typed,
            'documents': [{**{k: v for k, v in d.items() if k != 'facts'},
                           'fact_ids': [f['fact_id'] for f in d['facts']]} for d in docs],
            'quality': qualities, 'column_audit': column_audit, 'summary': summary}


def export_csv(output, dataset):
    columns = dataset['columns']
    rows = [{**{k: row.get(k, '') for k in IDENTITY},
             **{c['label']: row['cells'][c['key']]['display'] for c in columns}}
            for row in dataset['rows']]
    write_csv(output/'CATALOG.csv', rows, IDENTITY + [c['label'] for c in columns])


def write_outputs(output: Path, documents, schema, qualities, *, export_csv_enabled=False, sort_by=None):
    dataset = build_dataset(documents, schema, qualities, sort_by)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output/'CATALOG.json', dataset)
    if export_csv_enabled:
        export_csv(output, dataset)
    summary = dataset['summary']
    LOG.info('Table: models=%d, populated cells=%d, cells with source issues=%d, '
             'cells without source evidence=%d; human verification pending=%d (not extraction errors)',
             summary['configuration_rows'], summary['populated_cells'], summary['cells_with_source_issues'],
             summary['cells_without_source_evidence'], summary['human_verification_pending'])
    return summary
