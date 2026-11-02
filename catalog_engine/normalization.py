"""Source-grounded, auditable projections for wide exports.

Raw facts are never overwritten; derived items keep source fact IDs and a
transformation method. Invalid/ambiguous values are left untouched.
"""
from __future__ import annotations
import re
import unicodedata
from .values import unit_multiplier
from .quality import BLOCKING_WARNINGS

SCALAR=re.compile(r'^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$')
NUMBER=r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'


def format_number(number: float) -> str:
    return f'{number:.12g}'


def canonical_scalar(fact: dict):
    """(canonical value, multiplier) or None. Only explicitly supported units."""
    raw=fact.get('base_value',fact.get('value','')).strip()
    if not SCALAR.fullmatch(raw):
        return None
    if set(fact.get('warnings',[])) & BLOCKING_WARNINGS:
        return None
    src=fact.get('unit_source','')
    expected=fact.get('unit_expected','')
    factor=unit_multiplier(src,expected)
    if factor is None:
        return None
    return (format_number(float(raw)*factor),factor)


def projection(fact: dict):
    """Yield axis/value components only when the source states each component.

    Never assign implied dimensions such as length/width/height if the PDF did
    not identify that order. Preserve components as dimension_1/2/3.
    """
    if fact.get('availability') in {'not_applicable','missing'}:
        return []
    if set(fact.get('warnings',[])) & BLOCKING_WARNINGS:
        return []
    v=unicodedata.normalize('NFKC',fact.get('value','')).strip()
    key=fact.get('parameter','')
    output=[]

    def add(name,val,unit,method):
        output.append({'parameter':name,'value':val,'unit':unit,
                       'category':fact.get('category','Other'),
                       'method':method,'source_fact_id':fact['fact_id'],
                       'source_file':fact['source_file'],
                       'source_page':fact['source_page'],
                       'model':fact['model'],
                       'configuration':fact.get('configuration','base'),
                       'source_label':fact.get('source_label',''),
                       'source_value':fact.get('value',''),
                       'availability':fact.get('availability',''),
                       'human_verified':False})

    triplet={
        'rapid_traverse_xyz_m_min':('rapid_traverse','m_min'),
        'cutting_feed_xyz_m_min':('cutting_feed','m_min'),
        'axis_motor_power_xyz_kw':('axis_motor_power','kw'),
    }
    if key in triplet:
        parts=re.fullmatch(rf'\s*({NUMBER})\s*/\s*({NUMBER})\s*/\s*({NUMBER})\s*',v)
        if parts:
            stem,suffix=triplet[key]
            unit={'m_min':'m/min','kw':'kW'}[suffix]
            for axis,number in zip('xyz',parts.groups()):
                add(f'{stem}_{axis}_{suffix}',number,unit,'explicit_xyz_triplet')
    elif key in {'positioning_accuracy','repeatability'}:
        axes=fact.get('condition_data',{}).get('axes','').replace('/','')
        parts=v.split('/')
        numbers=[p.strip() for p in parts]
        if len(axes) in (2,3) and len(numbers)==len(axes) and all(SCALAR.fullmatch(p) for p in numbers):
            # Use the source's angular unit, never convert arcseconds to mm.
            source_unit=unicodedata.normalize('NFKC',fact.get('unit_source','')).strip()
            suffix='mm' if source_unit=='mm' else 'arcsec' if source_unit in {'′′','″','arcsec'} else ''
            if suffix:
                for axis,number in zip(axes.lower(),numbers):
                    add(f'{key}_{axis}_{suffix}',number,'mm' if suffix=='mm' else 'arcsec','explicit_axis_accuracy')
    elif key in {'machine_dimensions_mm','table_size_mm'}:
        parts=re.fullmatch(rf'\s*({NUMBER})\s*[xX×*]\s*({NUMBER})\s*[xX×*]\s*({NUMBER})\s*',v) if key=='machine_dimensions_mm' else re.fullmatch(rf'\s*({NUMBER})\s*[xX×*]\s*({NUMBER})\s*',v)
        if parts:
            stem='machine_dimension' if key=='machine_dimensions_mm' else 'table_dimension'
            for i,number in enumerate(parts.groups(),1):
                add(f'{stem}_{i}_mm',number,'mm','explicit_dimension_component')
    elif key=='tool_max_diameter_mm' and ('满刀' in fact.get('source_label','') or 'full' in fact.get('source_label','').lower()):
        match=re.fullmatch(rf'\s*[φΦøØ]?({NUMBER})\s*/\s*[φΦøØ]?({NUMBER})\s*',v)
        if match:
            add('tool_max_diameter_full_mm',match.group(1),'mm','tool_diameter_full_empty')
            add('tool_max_diameter_empty_mm',match.group(2),'mm','tool_diameter_full_empty')
    elif key=='table_t_slot':
        match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+)\s*',v)
        if match:
            for name,value,unit in zip(('table_t_slot_width_mm','table_t_slot_pitch_mm','table_t_slot_count'),match.groups(),('mm','mm','pcs')):
                add(name,value,unit,'explicit_t_slot_width_pitch_count')
    elif key=='power_supply_specification':
        match=re.fullmatch(rf'\s*({NUMBER})\s*V\s*/\s*({NUMBER})\s*kVA\s*',v,re.I)
        if match:
            add('power_supply_voltage_v',match.group(1),'V','explicit_voltage_capacity_pair')
            add('power_supply_kva',match.group(2),'kVA','explicit_voltage_capacity_pair')
    return output
