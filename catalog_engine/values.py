"""Conservative values: raw strings stay intact, units are validated, not fixed."""
from __future__ import annotations
import re
import unicodedata
from .geometry import norm
from .models import split_top_level_commas


def condition_from_labels(labels):
    text=' / '.join(labels)
    result={}
    if re.search(r'精度|accuracy|repeatability',text,re.I):
        axes=re.search(r'[XYZABC](?:\s*/\s*[XYZABC])+',text,re.I)
        if axes:result['axes']=re.sub(r'\s+','',axes.group()).upper()
    nc=re.search(r'\((FANUC|SIEMENS|HEIDENHAIN)\)',text,re.I)
    if nc: result['controller']=nc.group(1).upper()
    if labels and re.fullmatch(r'Direct|Belt|Gear|Built[- ]in',labels[-1],re.I):
        result['drive']=labels[-1].lower().replace('-',' ')
    if re.search('without adjacent',text,re.I): result['tool_arrangement']='without_adjacent_tools'
    elif re.search(r'contin(?:u?ous)',text,re.I): result['tool_arrangement']='continuous'
    if re.search(r'tool-to-tool|t-t-t',text,re.I): result['change']='tool_to_tool'
    if re.search(r'chip-to-chip|c-t-c',text,re.I): result['change']='chip_to_chip'
    return result


def split_embedded_conditions(value: str, labels: list[str], key: str):
    """Controller blocks such as 'TNC7: 15K Direct ... 20K Built-in ...'."""
    default=condition_from_labels(labels)
    lines=[norm(line) for line in value.splitlines() if norm(line)]
    if lines and re.fullmatch(r'[A-Za-z][A-Za-z0-9 /-]{1,30}\s*:',lines[0]):
        controller=lines[0].rstrip(' :')
        output=[]
        for line in lines[1:]:
            match=re.match(r'(\d+(?:\.\d+)?)\s*(K|r/min|rpm)\s+(Direct(?:-coupled)?|Built[- ]in|Belt|Gear)\s+(.+)',line,re.I)
            if match:
                n,u,drive,v=match.groups()
                rpm=int(float(n)*(1000 if u.upper()=='K' else 1))
                output.append({'value':v,'condition':{**default,'controller':controller,'spindle_rpm':rpm,'drive':drive.lower()},'duty_cycle':''})
            elif output and re.search(r'\bS\d|cont\.',line,re.I):
                output[-1]['duty_cycle']=line
            elif line:
                # An unparsed condition must be kept as a single raw value,
                # not silently dropped when only some of its lines matched.
                return [{'value':norm(value),'condition':default,'duty_cycle':'','warning':'embedded_condition_unresolved'}]
        if output: return output
    # Physical measurements can be conditioned on magazine capacity. Never
    # combine masses of two machine configurations into one canonical scalar.
    # A suffix such as (30, 40, 60T) means that the preceding mass applies
    # to three different magazine sizes; the value itself is not multiplied.
    if key=='machine_weight_kg' and re.search(r'\(\s*\d+(?:\s*,\s*\d+)*\s*T\s*\)',value,re.I):
        pieces=split_top_level_commas(norm(value))
        parsed=[]
        for piece in pieces:
            match=re.fullmatch(r'([-+]?\d+(?:\.\d+)?(?:\s*\([-+]?\d+(?:\.\d+)?\))?)\s*\(\s*((?:\d+\s*,\s*)*\d+)\s*T\s*\)\s*',piece,re.I)
            if not match:
                return [{'value':norm(value),'condition':default,'duty_cycle':'',
                         'warning':'magazine_configuration_unresolved'}]
            measurement,count_text=match.groups()
            for count in re.findall(r'\d+',count_text):
                parsed.append({'value':norm(measurement),
                               'condition':{**default,'magazine_tools':int(count)},
                               'duty_cycle':''})
        if parsed:return parsed
    # Supply currents for controllers may be an explicit parallel pair.
    if key=='power_supply_kva':
        nc=re.search(r'\((FANUC\s*/\s*SIEMENS)\)', ' '.join(labels),re.I)
        parts=[p.strip() for p in norm(value).split('/')]
        if nc and len(parts)==2:
            return [{'value':v,'condition':{**default,'controller':c},'duty_cycle':''} for c,v in zip(['FANUC','SIEMENS'],parts)]
    hz=re.fullmatch(r'\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\s*\(60Hz/50Hz\)\s*',norm(value),re.I)
    if hz:
        return [{'value':v,'condition':{**default,'frequency_hz':h},'duty_cycle':''} for h,v in zip([60,50],hz.groups())]
    duty='; '.join(re.findall(r'\([^)]*(?:S[1236]\s|cont\.)[^)]*\)',norm(value),re.I))
    return [{'value':norm(value),'condition':default,'duty_cycle':duty}]


def option_parts(value: str, key: str):
    s=norm(value)
    # Option contents AFTER an imperial parenthesis are NOT separate SI options.
    primary=s.split('(',1)[0]
    groups=re.findall(r'\{([^{}]+)\}',primary)
    options=[]
    for g in groups:
        if key in {'tool_capacity','turret_station_count','spindle_speed_rpm','chuck_size_inch'}:
            options.extend(norm(x) for x in re.split(r'[,;]',g) if norm(x))
        else:
            options.append(norm(g))
    base=norm(re.sub(r'\{[^{}]*\}','',s).replace('*',''))
    base=norm(re.sub(r'\(\s*\)','',base))
    if s in {'-','\u2013','\u2014','n/a','N/A'}: availability='not_applicable'
    elif not s: availability='missing'
    elif re.fullmatch(r'(?:\{[^}]*\}|\s|\*|\(|\))*',s): availability='optional_only'
    else: availability='present'
    if availability in {'not_applicable','optional_only'}: base=''
    return base,options,availability


# Public compatibility exports; all unit logic lives in units.py.
from .units import unit_family, unit_multiplier


def range_bounds(value):
    s=unicodedata.normalize('NFKC',norm(value)).replace('−','-').replace('–','-').replace('—','-').replace('〜','~')
    match=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(?:~|-|至|to)\s*([+-]?\d+(?:\.\d+)?)',s,re.I)
    return tuple(map(float,match.groups())) if match else None


def value_warnings(value: str, expected: str, source_unit: str, key: str):
    out=[]
    if '\ufffd' in value or '(cid:' in value: out.append('damaged_text')
    if '//' in value: out.append('source_repeated_separator')
    if re.search(r'T\.?B\.?D',value,re.I): out.append('source_value_tbd')
    a,b=unit_family(expected),unit_family(source_unit)
    if a and b and a!=b: out.append('source_unit_conflict')
    if a and source_unit.strip() not in {'','-','–','—'} and not b: out.append('source_unit_unrecognized')
    if not source_unit and expected and value.strip() not in {'','-'}:
        out.append('source_unit_not_specified')
    if re.fullmatch(r'\s*[-+]?\d+(?:\.\d+)?\s*\([-+]?\d+(?:\.\d+)?\)\s*',value) and '(' not in source_unit:
        out.append('parenthesized_value_semantics_unresolved')
    bounds=range_bounds(value) if key not in {'table_t_slot','table_indexing'} else None
    if bounds and bounds[0]>bounds[1]:out.append('reversed_range')
    if bounds and a not in {'angle',''} and bounds[0]<0:out.append('negative_physical_value')
    scalar=re.fullmatch(r'-\d+(?:\.\d+)?',norm(unicodedata.normalize('NFKC',value)))
    if scalar and a not in {'angle',''}:out.append('negative_physical_value')
    # Validate only scalar metric/imperial pairs. Compound expressions and duty
    # cycles are retained, not reinterpreted by broad numerical heuristics.
    pairs=list(re.finditer(r'(?<![A-Za-z0-9./])[\u00d8\u00f8]?([-+]?\d+(?:\.\d+)?)\s*\([\u00d8\u00f8]?([-+]?\d+(?:\.\d+)?)\)',value))
    for pair in pairs:
        ratios={'length':1/25.4,'mass':2.2046226218,'linear_speed':1000/25.4,'power':1.34102209,'torque':0.737562149,'volume':0.264172052}
        factor=ratios.get(b)
        if factor and '(' in source_unit:
            primary,secondary=map(float,pair.groups());target=primary*factor
            # Catalogue rounding often uses a single decimal place.
            if abs(target-secondary)>max(.15,abs(target)*.015): out.append('source_metric_imperial_mismatch')
    if key in {'machine_weight_kg','machine_dimensions_mm'}:
        # Flag compound cells for human verification even if one pair looks fine.
        # No guessed division into installed magazine or attachment variants.
        if re.search(r'\b\d+T\b',value): out.append('configuration_compound_value')
    return sorted(set(out))
