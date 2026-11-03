"""Lossless fact-to-table projection. Never feed display strings back to extraction.

Statuses and numeric components live outside the rendered value. Audit exports
retain every input fact, including alternatives that cannot safely be merged.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging
import re

from .values import metric_text, unit_multiplier

LOG = logging.getLogger(__name__)
LEGEND = '- — не указано в каталоге; !- — требуется подтверждение'
REMOVED = frozenset({
    'rapid_traverse_c_rpm', 'tool_magazine_type', 'tool_max_diameter_full_mm',
    'tool_max_diameter_empty_mm', 'travel_b_deg', 'travel_a_deg',
    'travel_c_deg', 'spindle_nose_to_table_center_mm',
})
GROUPS = {
    'machine_dimensions_mm': ('machine_dimensions_mm',) + tuple(
        f'machine_dimension_{i}_mm' for i in (1, 2, 3)),
    'rapid_traverse_xyz_m_min': ('rapid_traverse_xyz_m_min',) + tuple(
        f'rapid_traverse_{a}_m_min' for a in 'xyz'),
    'cutting_feed_xyz_mm_min': ('cutting_feed_xyz_mm_min', 'cutting_feed_xyz_m_min') + tuple(
        f'cutting_feed_{a}_m_min' for a in 'xyz'),
    'table_size_mm': ('table_size_mm', 'pallet_size_mm'),
}
TARGET = {source: target for target, sources in GROUPS.items() for source in sources}
NUMBER = r'[+-]?\d+(?:[.,]\d+)?'


@dataclass(eq=True)
class Cell:
    value: str
    status: str
    numbers: tuple[Decimal, ...] = ()
    audit: tuple[dict, ...] = ()
    issues: tuple[str, ...] = ()

    def render(self):
        if self.status == 'ABSENT':
            return '-'
        if self.status == 'REVIEW':
            return '!- ' + self.value if self.value else '!-'
        return self.value


def target_parameter(parameter):
    return TARGET.get(parameter, parameter)


def hidden_parameters(facts, configured=(), machines=None):
    """Only B rotation is conditional; never apply a global sparsity threshold."""
    hidden = set(configured) | set(REMOVED)
    if machines is None:
        machines = {(f.get('source_file'), f.get('model'), f.get('configuration', 'base')) for f in facts}
    populated = {(f.get('source_file'), f.get('model'), f.get('configuration', 'base'))
                 for f in facts if f['parameter'] == 'rapid_traverse_b_rpm'
                 and f.get('value') not in (None, '')
                 and f.get('availability') not in {'missing', 'not_applicable'}}
    if machines and len(populated) / len(machines) < .25:
        hidden.add('rapid_traverse_b_rpm')
    return hidden


def _text(fact):
    value = fact.get('value')
    text = '' if value is None else str(value).strip()
    return '' if text in {'-', '!-', '[НЕ ИЗВЛЕЧЕНО ИЛИ НЕ УКАЗАНО В КАТАЛОГЕ]'} else text


def _scalar(fact, unit):
    text = metric_text(_text(fact), fact.get('unit_source'))
    suffix = re.search(r'\s*(mm/min|m/min|мм/мин|м/мин|mm|мм)\s*$', text, re.I)
    if suffix:
        text = text[:suffix.start()].strip()
    if not re.fullmatch(NUMBER, text):
        return None
    # A comma followed by three digits may be a thousands separator.
    if re.fullmatch(r'[+-]?\d{1,3},\d{3}', text):
        return None
    source = fact.get('unit_source', fact.get('unit_expected', unit))
    if not isinstance(source, str):
        return None
    if suffix:
        embedded = suffix.group(1).lower()
        if source and unit_multiplier(embedded, source) != 1:
            return None
        source = embedded
    scale = unit_multiplier(source, unit)
    if scale is None:
        return None
    try:
        return Decimal(text.replace(',', '.')) * Decimal(str(scale))
    except InvalidOperation:
        return None


def _fmt(number):
    return format(number.normalize(), 'f')


def _missing(statuses):
    # Only explicit source evidence justifies absence. Unknown and failed
    # extraction must not be relabelled as "not in catalogue".
    known = {'SOURCE_NOT_LISTED', 'ABSENT'}
    confirmed = lambda s: s.get('status') in known or (
        s.get('status') == 'SOURCE_BLANK' and s.get('source_absence_confirmed') is True)
    return 'ABSENT' if statuses and all(confirmed(s) for s in statuses) else 'REVIEW'


def _group_missing(target, statuses):
    sources = GROUPS[target]
    combined = [k for k in sources if k == 'machine_dimensions_mm' or '_xyz_' in k]
    if any(_missing(statuses[k]) == 'ABSENT' for k in combined):
        return 'ABSENT'
    separate = [k for k in sources if k not in combined]
    return 'ABSENT' if separate and all(_missing(statuses[k]) == 'ABSENT' for k in separate) else 'REVIEW'


def _part(facts, statuses, unit):
    if not facts or not any(_text(f) for f in facts):
        return Cell('', _missing(statuses))
    numbers = [_scalar(f, unit) for f in facts]
    distinct = tuple(dict.fromkeys(n for n in numbers if n is not None))
    review = (any(n is None for n in numbers) or len(distinct) != 1
              or any(f.get('warnings') or f.get('condition') or f.get('options')
                     or f.get('availability') in {'optional_only', 'missing', 'not_applicable'}
                     or f.get('human_verified') is False for f in facts))
    value = ' || '.join(dict.fromkeys(_fmt(n) if n is not None else f'[не преобразовано: {_text(f)}; {f.get("unit_source") or "единица не указана"}]'
                                     for f, n in zip(facts, numbers)))
    issues = []
    if any(n is None for n in numbers): issues.append('unparseable_or_unconvertible_value')
    if len(distinct) > 1: issues.append('value_conflict')
    return Cell(value, 'REVIEW' if review else 'PRESENT', distinct, issues=tuple(issues))


def _dimensions(groups, statuses):
    if not any(groups[k] for k in GROUPS['machine_dimensions_mm']):
        return Cell('', _group_missing('machine_dimensions_mm', statuses))
    parts = [_part(groups[k], statuses[k], 'mm') for k in GROUPS['machine_dimensions_mm'][1:]]
    explicit = groups['machine_dimensions_mm']
    variants = []
    numbers = ()
    review = False
    issues = []
    for fact in explicit:
        if not _text(fact):
            review = True
            continue
        primary = metric_text(_text(fact), fact.get('unit_source'))
        text = re.sub(r'\s*(?:mm|мм)$', '', primary, flags=re.I).strip()
        suffix = primary[len(text):]
        tokens = re.split(r'\s*[x×*]\s*', text)
        parsed = [_scalar({**fact, 'value': token + suffix}, 'mm') for token in tokens]
        if len(parsed) == 3 and all(n is not None for n in parsed):
            variants.append(' × '.join(_fmt(n) for n in parsed) + ' мм')
            numbers = tuple(parsed)
        else:
            variants.append(_text(fact))
            review = True
            issues.append('unparseable_dimensions')
        review |= bool(fact.get('warnings') or fact.get('condition') or fact.get('options')
                       or fact.get('human_verified') is False)
    if any(groups[k] or statuses[k] for k in GROUPS['machine_dimensions_mm'][1:]):
        variants.append(' × '.join(p.value or p.render() for p in parts) + ' мм')
        if not numbers:
            numbers = tuple(n for p in parts for n in p.numbers)
        review |= any(p.status != 'PRESENT' for p in parts)
        if any(not p.value for p in parts): issues.append('partial_dimensions')
        issues.extend(issue for p in parts for issue in p.issues)
    variants = list(dict.fromkeys(variants))
    if not variants:
        return Cell('', _missing(statuses['machine_dimensions_mm']))
    if len(variants) > 1: issues.append('dimension_variants_require_confirmation')
    return Cell(' || '.join(variants),
                'REVIEW' if review or len(variants) > 1 else 'PRESENT', numbers, issues=tuple(dict.fromkeys(issues)))


def _axes(target, groups, statuses):
    cutting = target.startswith('cutting')
    unit, label = ('mm/min', 'мм/мин') if cutting else ('m/min', 'м/мин')
    prefix = 'cutting_feed' if cutting else 'rapid_traverse'
    axis_facts = {a: list(groups[f'{prefix}_{a.lower()}_m_min']) for a in 'XYZ'}
    unresolved = []
    shared = False
    for key in GROUPS[target]:
        if '_xyz_' not in key:
            continue
        for fact in groups[key]:
            if not _text(fact):
                continue
            # A scalar in an XYZ-named field alone is insufficient evidence.
            axes = str((fact.get('condition_data') or {}).get('axes', ''))
            proof = axes + ' ' + str(fact.get('source_label', ''))
            ordered = bool(re.search(r'X\s*[/,]\s*Y\s*[/,]\s*Z', proof, re.I))
            tokens = re.split(r'\s*/\s*', metric_text(_text(fact), fact.get('unit_source')))
            if ordered and len(tokens) in (1, 3):
                shared |= len(tokens) == 1
                for i, a in enumerate('XYZ'):
                    axis_facts[a].append({**fact, 'value': tokens[0 if len(tokens) == 1 else i]})
            else:
                unresolved.append(f'{_text(fact)} [{fact.get("unit_source") or "единица не указана"}]')
    if not any(axis_facts.values()) and not unresolved:
        return Cell('', _group_missing(target, statuses))
    parts = [_part(axis_facts[a], statuses[f'{prefix}_{a.lower()}_m_min'], unit) for a in 'XYZ']
    review = bool(unresolved) or any(p.status != 'PRESENT' for p in parts)
    if shared and all(p.numbers == parts[0].numbers and len(p.numbers) == 1 for p in parts):
        value = 'X/Y/Z: ' + parts[0].value
    else:
        separator = '/' if cutting else '; '
        value = separator.join(f'{a}: {p.value or p.render()}' for a, p in zip('XYZ', parts))
    value += ' ' + label
    if unresolved:
        value += ' || исходная запись: ' + ' || '.join(dict.fromkeys(unresolved))
    issues = [issue for p in parts for issue in p.issues]
    if unresolved: issues.append('ambiguous_axis_notation')
    if any(not p.value for p in parts): issues.append('partial_axes')
    return Cell(value, 'REVIEW' if review else 'PRESENT', tuple(n for p in parts for n in p.numbers),
                issues=tuple(dict.fromkeys(issues)))


def _table(groups, statuses):
    pieces = []
    review = False
    issues = []
    for source, name in [('table_size_mm', 'Стол'), ('pallet_size_mm', 'Паллета')]:
        for fact in groups[source]:
            if not _text(fact):
                review = True
                continue
            primary = metric_text(_text(fact), fact.get('unit_source'))
            text = re.sub(r'\s*(?:mm|мм)$', '', primary, flags=re.I).strip()
            suffix = primary[len(text):]
            # Unsupported size/unit or conditioned alternatives need
            # review; raw text remains available in the audit.
            tokens = re.split(r'\s*[x×*]\s*', text)
            parsed = [_scalar({**fact, 'value': t + suffix}, 'mm') for t in tokens]
            valid = len(parsed) in (2, 3) and all(n is not None for n in parsed)
            rendered = ' × '.join(_fmt(n) for n in parsed) + ' мм' if valid else _text(fact)
            pieces.append(f'{name}: {rendered}')
            if not valid: issues.append('unparseable_table_size')
            review |= (len(tokens) not in (2, 3)
                       or not valid
                       or bool(fact.get('warnings') or fact.get('condition') or fact.get('options'))
                       or fact.get('human_verified') is False)
        conflict = len(set(_text(f) for f in groups[source])) > 1
        review |= conflict
        if conflict: issues.append('table_variants_require_confirmation')
    return Cell('; '.join(dict.fromkeys(pieces)), 'REVIEW' if review else 'PRESENT', issues=tuple(dict.fromkeys(issues))) if pieces else Cell('', _missing(statuses))


def _general(facts, statuses):
    if not any(_text(f) for f in facts):
        return Cell('', _missing(statuses))
    values = list(dict.fromkeys(
        _text(f) + (f' [condition: {f["condition"]}]' if f.get('condition') else '')
        for f in facts))
    review = any(f.get('warnings') or f.get('human_verified') is False
                 or f.get('availability') in {'optional_only', 'missing', 'not_applicable'}
                 or _text(f) in {'', '-', '!-', '[НЕ ИЗВЛЕЧЕНО ИЛИ НЕ УКАЗАНО В КАТАЛОГЕ]'}
                 for f in facts)
    return Cell(' || '.join(values), 'REVIEW' if review or len(values) > 1 else 'PRESENT',
                issues=('multiple_values_require_confirmation',) if len(values) > 1 else ()) if values else Cell('', _missing(statuses))


def project_cells(facts, statuses=(), parameters=()):
    """Project immutable source facts; projecting an existing projection is a no-op.

    Callers group by source/model/configuration first. Keeping this boundary
    prevents measurements of different machines from being merged accidentally.
    """
    if isinstance(facts, dict):
        if not all(isinstance(cell, Cell) for cell in facts.values()):
            raise ValueError('Expected a Cell projection or a sequence of facts')
        return deepcopy(facts)
    groups, states = defaultdict(list), defaultdict(list)
    for fact in facts:
        if not isinstance(fact, dict) or not fact.get('parameter'):
            raise ValueError('Each fact must have a parameter')
        groups[fact['parameter']].append(fact)
    for status in statuses:
        if status.get('parameter'):
            states[status['parameter']].append(status)
    keys = {target_parameter(k) for k in set(parameters) | set(groups) | set(states)}
    result = {}
    for key in sorted(keys):
        sources = GROUPS.get(key, (key,))
        audit = tuple(deepcopy(f) for source in sources for f in groups[source])
        if key == 'machine_dimensions_mm':
            cell = _dimensions(groups, states)
        elif key in {'rapid_traverse_xyz_m_min', 'cutting_feed_xyz_mm_min'}:
            cell = _axes(key, groups, states)
        elif key == 'table_size_mm':
            cell = _table(groups, [s for source in GROUPS[key] for s in states[source]])
            if not any(groups[source] for source in GROUPS[key]):
                cell.status = _group_missing(key, states)
        else:
            cell = _general(groups[key], states[key])
        cell.audit = audit
        result[key] = cell
    LOG.debug('Projected %d parameters; %d require confirmation (includes human verification and unknown absence)', len(result), sum(c.status == 'REVIEW' for c in result.values()))
    return result
