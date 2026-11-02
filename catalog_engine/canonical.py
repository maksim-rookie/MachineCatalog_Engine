"""Pure normalization of a fact, with explicit fallbacks and no file I/O.

A display value is always preserved. A canonical value is emitted only when
units and syntax support the transformation. Conditions and options travel
with the measurement; they never become unconditional base values.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import re
import unicodedata
from .quality import BLOCKING_WARNINGS
from .units import canonical_unit, unit_multiplier

NUMBER = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
SCALAR = re.compile(NUMBER + r'\Z')
NUMERIC_CONTENT = re.compile(r'[\d\s+.,/×xX~{};*øØφΦhH−-]+\Z')


def clean_text(value: str) -> str:
    """Normalize known PDF glyphs only in the derived representation."""
    if not isinstance(value, str):
        raise TypeError(f'Expected text, got {type(value).__name__}')
    value = value.replace('\uf688', '(').replace('\uf689', ')')
    value = unicodedata.normalize('NFKC', value)
    value = value.replace('−', '-').replace('–', '-').replace('〜', '~')
    return re.sub(r'\s+', ' ', value).strip()


def number_text(value: str, factor: float) -> str:
    number = float(value) * factor
    if not math.isfinite(number):
        raise ValueError('Non-finite measurement')
    return format(number, '.12g')


def primary_expression(value: str, source_unit: str) -> str:
    """Remove numeric equivalents only when declared by the source unit."""
    if '(' not in source_unit:
        return value
    def replace(match):
        body = match.group(1).strip()
        return '' if re.search(r'\d', body) and NUMERIC_CONTENT.fullmatch(body) else match.group()
    return re.sub(r'\(([^()]*)\)', replace, value).strip()


def numeric_expression(value: str, factor: float) -> tuple[str, str] | None:
    value = clean_text(value)
    scalar = re.fullmatch(rf'([øØφΦ]?)\s*({NUMBER})', value)
    if scalar:
        prefix = 'Ø' if scalar.group(1) else ''
        return prefix + number_text(scalar.group(2), factor), 'SCALAR'
    interval = re.fullmatch(rf'({NUMBER})\s*(?:~|to|至|-)\s*({NUMBER})', value, re.I)
    if interval:
        return ' ~ '.join(number_text(n, factor) for n in interval.groups()), 'RANGE'
    vector = re.fullmatch(rf'{NUMBER}(?:\s*/\s*{NUMBER}){{1,3}}', value)
    if vector:
        return ' / '.join(number_text(n.strip(), factor) for n in value.split('/')), 'VECTOR'
    dimensions = re.fullmatch(rf'{NUMBER}(?:\s*[xX×*]\s*{NUMBER}){{1,2}}', value)
    if dimensions:
        return ' × '.join(number_text(n.strip(), factor) for n in re.split('[xX×*]', value)), 'DIMENSIONS'
    return None


@dataclass(frozen=True)
class NormalizedValue:
    value: str
    display: str
    unit: str
    status: str
    method: str
    options: tuple[str, ...] = ()


class ParameterCatalog:
    """One canonical name/unit per key, independent of matching-rule order."""
    def __init__(self, schema):
        config = schema.config.get('normalization', {})
        self.aliases = config.get('parameter_aliases', {})
        self.units = config.get('canonical_units', {})
        self.text_aliases = config.get('text_aliases', {})
        self.schema = schema

    def key(self, key: str) -> str:
        seen = set()
        while key in self.aliases:
            if key in seen:
                raise ValueError(f'Circular parameter alias: {key}')
            seen.add(key)
            key = self.aliases[key]
        return key

    def unit(self, key: str, fact: dict) -> str:
        declared = self.units.get(key, fact.get('unit_expected', ''))
        if not declared and key in {'positioning_accuracy', 'repeatability'}:
            declared = fact.get('unit_source', '')
        return canonical_unit(declared)

    def category(self, key: str, fact: dict) -> str:
        return self.schema.parameter_group(key, fact.get('category', 'Other'))

    def text(self, key: str, value: str) -> str:
        for source, target in self.text_aliases.get(key, {}).items():
            if value.casefold() == source.casefold():
                return target
        return value


def normalize_value(fact: dict, key: str, target: str, catalog: ParameterCatalog) -> NormalizedValue:
    value = clean_text(fact.get('value', ''))
    availability = fact.get('availability', 'present')
    if availability == 'not_applicable':
        return NormalizedValue('', '[НЕ ПРИМЕНИМО]', target, 'NOT_APPLICABLE', 'source_dash')
    if not value:
        return NormalizedValue('', '[ПУСТАЯ ЯЧЕЙКА В PDF]', target, 'SOURCE_BLANK', 'source_blank')
    warnings = fact.get('warnings', [])
    if not isinstance(warnings, list):
        raise TypeError('warnings must be a list, not serialized JSON')
    if set(warnings) & BLOCKING_WARNINGS:
        source_unit = fact.get('unit_source', '')
        return NormalizedValue('', f'{value} [ПРОВЕРИТЬ; исходная единица: {source_unit or "не указана"}]',
                               target, 'BLOCKED_REVIEW', 'source_preserved')
    if not target:
        text = catalog.text(key, value)
        if availability == 'optional_only':
            return NormalizedValue('', text + ' [ОПЦИЯ]', '', 'OPTIONAL_ONLY', 'text_option_preserved')
        return NormalizedValue(text, text, '', 'TEXT_PRESERVED', 'text_whitespace_and_aliases')
    source_unit = fact.get('unit_source', '')
    factor = unit_multiplier(source_unit, target)
    if factor is None:
        return NormalizedValue('', f'{value} [ИСХОДНОЕ; единица: {source_unit or "не указана"}]',
                               target, 'UNIT_REVIEW', 'source_preserved')
    primary = primary_expression(value, source_unit)
    groups = re.findall(r'\{([^{}]*)\}', primary)
    base = re.sub(r'\{[^{}]*\}', '', primary).strip().rstrip('*').strip()
    parsed = numeric_expression(base, factor)
    options = []
    for group in groups:
        parts = [part.strip().rstrip('*') for part in re.split('[,;]', group) if part.strip()]
        for part in parts:
            item = numeric_expression(part, factor)
            # Never label an unconverted option with a new unit.
            if item is None:
                return NormalizedValue('', value + ' [СЛОЖНОЕ ЗНАЧЕНИЕ; см. источник]', target,
                                       'COMPOUND_PRESERVED', 'source_preserved')
            options.append(item[0])
    if availability == 'optional_only':
        option = numeric_expression(primary.strip('{} *'), factor)
        text = option[0] if option else primary
        return NormalizedValue('', text + ' [ОПЦИЯ]', target, 'OPTIONAL_ONLY', 'option_preserved', tuple(options))
    if parsed:
        normalized, kind = parsed
        display = normalized + (' {' + '; '.join(dict.fromkeys(options)) + '}' if options else '')
        status = 'CANONICAL_WITH_OPTIONS' if options else 'CANONICAL_' + kind
        return NormalizedValue(normalized, display, target, status, 'explicit_primary_unit', tuple(options))
    # Dimensional annotations, controller names and duty cycles are not erased.
    # If syntax cannot be converted, preserve the complete source with its unit.
    return NormalizedValue('', value + f' [ИСХОДНОЕ; {source_unit}]', target,
                           'COMPOUND_PRESERVED', 'source_preserved')


def format_signature(value: str) -> str:
    text = clean_text(value)
    if not text:
        return 'empty'
    if '\uf688' in value or '\uf689' in value:
        return 'pdf_private_brackets'
    if '{' in text:
        return 'options'
    if '(' in text:
        return 'parenthetical'
    parsed = numeric_expression(text, 1)
    return parsed[1].lower() if parsed else 'text_or_compound'


def condition_suffix(fact: dict) -> str:
    conditions = [str(fact.get(name, '')).strip() for name in ('condition', 'duty_cycle')]
    return ' [' + '; '.join(dict.fromkeys(x for x in conditions if x)) + ']' if any(conditions) else ''


def source_record(fact: dict, source_hash: str) -> dict:
    return {
        'source_fact_id': fact.get('fact_id', ''), 'source_file': fact.get('source_file', ''),
        'source_sha256': source_hash, 'source_page': fact.get('source_page', ''),
        'source_table': fact.get('source_table', ''), 'source_row_id': fact.get('source_row_id', ''),
        'source_model': fact.get('model', ''), 'configuration': fact.get('configuration', 'base'),
        'source_parameter': fact.get('parameter', ''), 'source_label': fact.get('source_label', ''),
        'source_value': fact.get('value', ''), 'source_value_raw': fact.get('value_raw', fact.get('value', '')),
        'source_unit': fact.get('unit_source', ''), 'source_unit_expected': fact.get('unit_expected', ''),
        'cell_bbox': fact.get('cell_bbox', []), 'condition': fact.get('condition', ''),
        'condition_data': fact.get('condition_data', {}), 'duty_cycle': fact.get('duty_cycle', ''),
        'availability': fact.get('availability', ''), 'options': fact.get('options', []),
        'warnings': fact.get('warnings', []), 'human_verified': fact.get('human_verified', False),
    }


def normalize_fact(fact: dict, catalog: ParameterCatalog, source_hash: str = '') -> list[dict]:
    """Retain the parent fact and add components only for explicit axis groups."""
    for name in ('fact_id', 'source_file', 'model', 'parameter', 'value'):
        if not isinstance(fact.get(name), str) or not fact[name]:
            raise ValueError(f'Missing/invalid fact field: {name}')
    key = catalog.key(fact['parameter'])
    target = catalog.unit(key, fact)
    result = normalize_value(fact, key, target, catalog)
    common = source_record(fact, source_hash)
    parent = {**common, 'canonical_parameter': key, 'canonical_unit': target,
              'category': catalog.category(key, fact), 'normalized_value': result.value,
              'display_value': result.display + condition_suffix(fact),
              'normalization_status': result.status, 'normalization_method': result.method,
              'normalized_options': list(result.options), 'is_component': False}
    records = [parent]
    if not result.status.startswith('CANONICAL_'):
        return records
    axes = ''
    stem = ''
    suffix = ''
    if '_xyz_' in key:
        stem, suffix = key.split('_xyz_', 1)
        axes = 'xyz'
    elif key in {'positioning_accuracy', 'repeatability'}:
        axes = re.sub('[^xyzabc]', '', str(fact.get('condition_data', {}).get('axes', '')).lower())
        stem, suffix = key, 'mm' if target == 'mm' else 'arcsec' if target == 'arcsec' else ''
    elif key == 'rotary_axis_speed_rpm':
        match = re.search(r'[ABC](?:\s*/\s*[ABC])+', fact.get('source_label', ''), re.I)
        if match:
            axes = re.sub('[^abc]', '', match.group().lower())
            stem, suffix = 'rotary_axis_speed', 'rpm'
    if not axes or not suffix:
        return records
    values = [p.strip() for p in result.value.split('/')]
    if len(values) == 1:
        values *= len(axes)  # One source value explicitly covers the named axes.
    if len(values) != len(axes):
        return records
    option_vectors = []
    for option in result.options:
        parts = [part.strip() for part in option.split('/')]
        if len(parts) == 1:
            parts *= len(axes)
        if len(parts) != len(axes):
            return records
        option_vectors.append(parts)
    for index, (axis, value) in enumerate(zip(axes, values)):
        options = [parts[index] for parts in option_vectors]
        display = value + (' {' + '; '.join(options) + '}' if options else '')
        records.append({**parent, 'canonical_parameter': f'{stem}_{axis}_{suffix}',
                        'normalized_value': value, 'display_value': display + condition_suffix(fact),
                        'normalized_options': options,
                        'normalization_method': 'explicit_axis_group', 'is_component': True})
    return records
