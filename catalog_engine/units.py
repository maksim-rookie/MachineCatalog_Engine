"""Shared unit vocabulary. Scales are relative to one unit per physical family.

PDF primary units are authoritative; a parenthetical unit is an equivalent,
not another measurement. Unknown units are never dimensionally guessed.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata

@dataclass(frozen=True)
class Unit:
    symbol: str
    family: str
    scale: float


def unit_token(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).split('(', 1)[0]
    return re.sub(r'\s+', '', text).casefold().replace('·', '*')


_UNITS: dict[str, Unit] = {}


def _register(symbol: str, family: str, scale: float, *aliases: str) -> None:
    unit = Unit(symbol, family, scale)
    for alias in (symbol, *aliases):
        _UNITS[unit_token(alias)] = unit


_register('mm', 'length', 1, '毫米')
_register('cm', 'length', 10, '厘米')
_register('m', 'length', 1000, '米')
_register('inch', 'length', 25.4, 'in', 'inches')
_register('m/min', 'linear_speed', 1)
_register('mm/min', 'linear_speed', .001)
_register('ipm', 'linear_speed', .0254, 'in/min')
_register('ft/min', 'linear_speed', .3048)
_register('rpm', 'rotation_speed', 1, 'r/min', 'rev/min', 'min-1')
_register('kg', 'mass', 1)
_register('t', 'mass', 1000, 'ton', 'tonne', '吨', '噸')
_register('g', 'mass', .001)
_register('lb', 'mass', .45359237, 'lbs')
_register('kW', 'power', 1)
_register('W', 'power', .001)
_register('MW', 'power', 1000)
_register('hp', 'power', .745699872)
_register('N·m', 'torque', 1, 'N*m', 'N.m', 'Nm')
_register('lbf-ft', 'torque', 1.355817948, 'ft-lbs', 'ft-lb')
_register('N·m²', 'inertia', 1, 'N*m2', 'N.m2')
_register('kVA', 'apparent_power', 1)
_register('MPa', 'pressure', 1)
_register('bar', 'pressure', .1)
_register('psi', 'pressure', .006894757293)
_register('s', 'time', 1, 'sec', 'second')
_register('ms', 'time', .001)
_register('deg', 'angle', 1, '°', '度')
_register('arcsec', 'angle', 1 / 3600, '″', '′′')
_register('arcmin', 'angle', 1 / 60, '′')
_register('pcs', 'count', 1, 'ea', '个', '個', '把', '支', 'axes', 'st')
_register('L', 'volume', 1, 'litre', 'liter')
_register('V', 'voltage', 1)


def describe_unit(text: str) -> Unit | None:
    # Uppercase G in the supplied schema is gravitational acceleration;
    # lowercase g is the mass unit gram. Do not collapse those symbols.
    primary = unicodedata.normalize('NFKC', text).split('(', 1)[0].strip()
    if primary in {'G', 'g0'}:
        return Unit('G', 'acceleration', 1)
    return _UNITS.get(unit_token(text))


def canonical_unit(text: str) -> str:
    if text.strip() in {'', '-', '–', '—'}:
        return ''
    unit = describe_unit(text)
    return unit.symbol if unit else text.strip()


def unit_family(text: str) -> str:
    unit = describe_unit(text)
    return unit.family if unit else ''


def unit_multiplier(source: str, target: str) -> float | None:
    left, right = describe_unit(source), describe_unit(target)
    if left and right:
        return left.scale / right.scale if left.family == right.family else None
    if source.strip() and unit_token(source) == unit_token(target):
        return 1.0
    return None
