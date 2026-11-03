"""Stable ordering for categories, models and heterogeneous cell values."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import re
import unicodedata

CATEGORY_ORDER = (
    'Accuracy', 'Travel', 'Capacity', 'Table', 'Feedrate', 'Spindle',
    'Sub spindle', 'Automatic tool changer (ATC)', 'Turret', 'Tailstock',
    'Machine dimensions', 'Machine', 'Motor', 'Tank capacity', 'Power source',
    'Control', 'Other',
)
MISSING = {'', '-', '!-', '–', '—', 'n/a', 'none', 'null', 'nan',
           '[не извлечено или не указано в каталоге]'}


def natural_key(value):
    """Tagged tokens avoid comparisons between numbers and strings."""
    text = unicodedata.normalize('NFKC', '' if value is None else str(value)).casefold()
    if text.strip() in MISSING:
        return ((2, ''),)
    return tuple((1, int(p)) if p.isdigit() else (0, p)
                 for p in re.split(r'(\d+)', text) if p)


def category_key(category, order=CATEGORY_ORDER):
    names = [str(name).casefold() for name in order]
    name = str(category or 'Other').casefold()
    return (names.index(name) if name in names else len(names), natural_key(name))


def value_key(value):
    """Numbers first, text second, missing last; zero is a number.

    Parse only unambiguous scalar notation (with an optional unit). Ranges,
    alternatives, axis vectors and ambiguous decimal commas stay text.
    """
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return (2,)
    text = unicodedata.normalize('NFKC', str(value)).strip()
    if text.casefold() in MISSING or (text.startswith('!-') and not text[2:].strip()):
        return (2,)
    text = re.sub(r'^!-\s*', '', text)
    match = re.fullmatch(r'([+-]?(?:(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?|\d+,\d{1,2}))\s*(?:[A-Za-zА-Яа-я°%][\w/°%·-]*)?', text)
    if match:
        try:
            token = match[1]
            if ',' in token and len(token.rsplit(',',1)[-1]) in (1,2):
                token = token.replace(',', '.')
            else:
                token = token.replace(',', '')
            number = Decimal(token)
            if number.is_finite():
                return (0, number)
        except InvalidOperation:
            pass
    return (1, natural_key(text))


def fact_key(fact, order=CATEGORY_ORDER):
    return (category_key(fact.get('category'), order),
            natural_key(fact.get('parameter')),
            natural_key(fact.get('machine_class')),
            natural_key(fact.get('machine_type')),
            natural_key(fact.get('model')),
            natural_key(fact.get('configuration')),
            value_key(fact.get('value')),
            natural_key(fact.get('source_file')),
            natural_key(fact.get('condition')))


def model_key(row):
    return tuple(natural_key(row.get(k)) for k in
                 ('machine_class', 'machine_type', 'manufacturer', 'model',
                  'configuration', 'source_file'))


def sort_model_rows(rows, sort_by=None):
    if sort_by:
        def key(row):
            cell = row.get('cells', {}).get(sort_by, {})
            numbers = cell.get('numeric_components', [])
            value = numbers[0] if len(numbers) == 1 else cell.get('value')
            return (value_key(value), model_key(row))
        return sorted(rows, key=key)
    return sorted(rows, key=model_key)
