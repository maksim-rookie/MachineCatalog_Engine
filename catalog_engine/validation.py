"""Normalize imported records without discarding raw input or inventing values."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata


class DocumentValidationError(ValueError):
    pass


def field_name(value):
    text = unicodedata.normalize('NFKC', '' if value is None else str(value)).strip()
    text = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', text)
    return re.sub(r'[^\w]+', '_', text.casefold()).strip('_')


ALIASES = {'model_name': 'model', 'модель': 'model', 'parameter_name': 'parameter',
           'param': 'parameter', 'параметр': 'parameter', 'значение': 'value',
           'unit': 'unit_source', 'units': 'unit_source', 'единица': 'unit_source',
           'group': 'category', 'категория': 'category', 'configuration_name': 'configuration'}


def canonical_record(raw):
    result = {}
    for key, value in raw.items():
        name = ALIASES.get(field_name(key), field_name(key))
        if name in result and result[name] != value:
            raise DocumentValidationError(f'Conflicting aliases for {name}')
        result[name] = value
    return result


def text(value):
    if value is None or (isinstance(value,float) and not math.isfinite(value)):
        return ''
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def expand_explicit_travel(fact):
    """Split an explicitly ordered XYZ travel vector; preserve its source record."""
    from .values import metric_text
    if fact['parameter'] != 'travel_xyz_mm' or not re.search(r'X\s*/\s*Y\s*/\s*Z', fact.get('source_label', ''), re.I):
        return [fact]
    tokens = re.split(r'\s*/\s*', metric_text(fact['value'], fact['unit_source']))
    if len(tokens) != 3 or not all(re.fullmatch(r'[+-]?(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?', t) for t in tokens):
        if 'ambiguous_axis_vector' not in fact['warnings']:
            fact['warnings'].append('ambiguous_axis_vector')
        return [fact]
    result = []
    for axis, token in zip('xyz', tokens):
        item = deepcopy(fact)
        item.update(parameter=f'travel_{axis}_mm', value=token.replace(',', ''),
                    base_value=token.replace(',', ''), source_parameter='travel_xyz_mm')
        item.setdefault('raw_record', deepcopy(fact))
        item['transformations'] = list(dict.fromkeys(item['transformations'] + ['split_explicit_xyz_travel']))
        item['fact_id'] = hashlib.sha256((fact['fact_id'] + ':' + axis).encode()).hexdigest()[:20]
        result.append(item)
    return result


def normalize_document(document, schema, source_file=None):
    """Invalid rows become audit issues; invalid containers fail this file only.

    Normalizing already normalized documents is idempotent. The full original
    record is retained when normalization changes its representation.
    """
    if not isinstance(document, dict):
        raise DocumentValidationError('Document must be a JSON object')
    doc = deepcopy(document)
    if not isinstance(doc.get('facts'), list):
        raise DocumentValidationError('Document.facts must be a list')
    if not isinstance(doc.get('registry', {}), dict):
        raise DocumentValidationError('Document.registry must be an object')
    for key in ('review', 'cell_statuses', 'source_pages', 'raw_tables', 'unmapped_fields', 'ocr_events', 'spec_pages'):
        if doc.get(key) is None:
            doc[key] = []
        if not isinstance(doc[key], list):
            raise DocumentValidationError(f'Document.{key} must be a list')
    doc['review'] = [r if isinstance(r,dict) else {'reason':'invalid_review_record','raw_record':r} for r in doc['review']]
    doc['source_file'] = source_file or text(doc.get('source_file')) or 'input'
    doc['machine_class'] = text(doc.get('machine_class')) or 'unknown'
    known = {r['key']: r for r in reversed(schema.config['rules'])}
    registry = {}
    for name, meta in doc.get('registry', {}).items():
        if not isinstance(meta, dict):
            raise DocumentValidationError(f'Registry metadata must be an object: {name}')
        item = canonical_record(meta)
        configs = item.get('configurations') or ['base']
        if isinstance(configs, str):
            configs = [configs]
        if not isinstance(configs, list):
            raise DocumentValidationError(f'Configurations must be a list: {name}')
        item['configurations'] = list(dict.fromkeys(text(c) or 'base' for c in configs))
        registry[text(name)] = item
    facts = []
    for index, raw in enumerate(doc['facts']):
        try:
            if not isinstance(raw, dict):
                raise DocumentValidationError('Fact must be an object')
            fact = canonical_record(raw)
            for key in ('model', 'parameter'):
                fact[key] = text(fact.get(key))
                if not fact[key]:
                    raise DocumentValidationError(f'Missing required field: {key}')
            fact['parameter'] = field_name(fact['parameter'])
            rule = known.get(fact['parameter'])
            if rule is None:
                labels = [text(fact.get('category')), text(fact.get('source_label')) or fact['parameter'].replace('_', ' ')]
                rule = schema.match(labels, text(fact.get('unit_source')))
                if rule:
                    fact['parameter'] = rule['key']
            fact['value'] = text(fact.get('value'))
            fact.setdefault('value_raw', raw.get('value', raw.get('Value', fact['value'])))
            fact.setdefault('base_value', fact['value'])
            for key in ('base_value', 'unit_source', 'condition', 'source_label', 'generation'):
                fact[key] = text(fact.get(key))
            condition_data = fact.get('condition_data')
            fact['condition_data'] = condition_data if isinstance(condition_data, dict) else {}
            if isinstance(fact.get('condition'), (dict, list)):
                fact['condition'] = text(fact['condition'])
            fact['source_file'] = doc['source_file']
            fact['configuration'] = text(fact.get('configuration')) or 'base'
            fact['category'] = text(fact.get('category')) or (rule['category'] if rule else 'Other')
            fact['unit_expected'] = text(fact.get('unit_expected')) or (rule['unit'] if rule else '')
            for key in ('warnings', 'options', 'transformations'):
                val = fact.get(key)
                fact[key] = val if isinstance(val, list) else ([] if val is None else [text(val)])
            fact['warnings'] = list(dict.fromkeys(text(w) for w in fact['warnings']))
            if rule is None and 'unknown_parameter' not in fact['warnings']:
                fact['warnings'].append('unknown_parameter')
            if 'raw_record' not in fact and fact != raw:
                fact['raw_record'] = deepcopy(raw)
            fact.setdefault('human_verified', False)
            fact.setdefault('availability', 'present' if fact['value'] else 'missing')
            if fact['value'] in {'-', '!-', ''}:
                fact['availability'] = 'missing'
                if doc.get('input_kind') == 'structured' and fact['value'] == '-':
                    status = {'source_file':doc['source_file'],'model':fact['model'],
                              'configuration':fact['configuration'],'parameter':fact['parameter'],
                              'status':'ABSENT'}
                    if status not in doc['cell_statuses']:
                        doc['cell_statuses'].append(status)
            if not fact.get('fact_id'):
                seed = {k: v for k, v in fact.items() if k not in {'raw_record', 'fact_id'}}
                fact['fact_id'] = hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:20]
            meta = registry.setdefault(fact['model'], {'model': fact['model'], 'configurations': []})
            if fact['configuration'] not in meta['configurations']:
                meta['configurations'].append(fact['configuration'])
            facts.extend(expand_explicit_travel(fact))
        except (ValueError, TypeError) as exc:
            issue = {'source_file': doc['source_file'], 'reason': 'invalid_input_record',
                     'row_index': index, 'instruction': str(exc), 'raw_record': deepcopy(raw)}
            if issue not in doc['review']:
                doc['review'].append(issue)
    doc.update(facts=facts, registry=registry)
    statuses = []
    for raw in doc['cell_statuses']:
        if not isinstance(raw, dict):
            continue
        status = canonical_record(raw)
        if not status.get('model'):
            continue
        status.update(model=text(status['model']), parameter=field_name(status.get('parameter', '')),
                      source_file=doc['source_file'], configuration=text(status.get('configuration')) or 'base')
        statuses.append(status)
    doc['cell_statuses'] = statuses
    return doc
