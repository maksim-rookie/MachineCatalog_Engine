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


def primary_unit(unit):
    """Normalize a unit token without guessing a different physical dimension."""
    s = norm(unicodedata.normalize('NFKC', '' if unit is None else str(unit))).lower().replace(' ', '')
    if s.startswith('(') and s.endswith(')') and s.count('(') == 1:
        s = s[1:-1]
    return s.split('(', 1)[0]


def metric_text(value, source_unit):
    """Remove an explicitly labelled imperial equivalent; keep the raw elsewhere."""
    value = norm(unicodedata.normalize('NFKC', '' if value is None else str(value)))
    unit = str(source_unit or '').lower()
    if re.search(r'\((?:in\b|inch|in/min|lb|ib|hp|ft)', unit):
        match = re.fullmatch(r'(.*?)\s*\([^()]*\)\s*', value)
        if match:
            return match[1].strip()
    return value


def unit_family(unit: str):
    s=primary_unit(unit)
    if not s or s in {'-','asa','mt'}: return ''
    if s in {'t','ton','tonne','吨','噸','g'} or s.startswith('kg'): return 'mass'
    if s in {'w','mw'} or s.startswith('kw'): return 'power'
    if s in {'m/min','mm/min','м/мин','мм/мин'}: return 'linear_speed'
    if s in {'cm','m','мм','毫米','厘米','米'} or s.startswith('mm'): return 'length'
    if s.startswith('inch'): return 'inch'
    if s.startswith('kg'): return 'mass'
    if s.startswith('m/min'): return 'linear_speed'
    if s.startswith(('r/min','rpm')): return 'rotation_speed'
    if s.startswith('kva'): return 'apparent_power'
    if s.startswith('kw'): return 'power'
    if s.startswith(('n\u00b7m','n*m','n.m','nm')): return 'torque'
    if s.startswith('mpa'): return 'pressure'
    if s.startswith('sec') or s=='s': return 'time'
    if s in {'″','′','\u2032\u2032','arcsec','arcmin','度'} or s.startswith(('deg','\u00b0')): return 'angle'
    if s in {'个','個','把','支','axes'} or s.startswith(('ea','pcs','st')): return 'count'
    if s.startswith('l'): return 'volume'
    return ''


def range_bounds(value):
    s=unicodedata.normalize('NFKC',norm(value)).replace('−','-').replace('–','-').replace('—','-').replace('〜','~')
    match=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(?:~|-|至|to)\s*([+-]?\d+(?:\.\d+)?)',s,re.I)
    return tuple(map(float,match.groups())) if match else None


def unit_multiplier(source,expected):
    """Convert only explicitly supported, dimensionally compatible units."""
    clean=primary_unit
    a,b=clean(source),clean(expected)
    if not a or not b:return None
    if a==b:return 1.0
    scales={'kg':1.,'t':1000.,'ton':1000.,'tonne':1000.,'吨':1000.,'噸':1000.,'g':.001,
            'mm':1.,'cm':10.,'m':1000.,'毫米':1.,'厘米':10.,'米':1000.,
            'kw':1.,'w':.001,'mw':1000.,'deg':1.,'°':1.,'度':1.,'′′':1/3600,'arcsec':1/3600,
            'rpm':1.,'r/min':1.,'pcs':1.,'ea':1.,'个':1.,'個':1.,'把':1.,'支':1.,
            'm/min':1000.,'mm/min':1.,'м/мин':1000.,'мм/мин':1.,'мм':1.}
    # The parenthesis in a printed unit specifies an imperial equivalent, not
    # a scaling of the primary metric value.
    a=a.split('(',1)[0];b=b.split('(',1)[0]
    if a==b:return 1.0
    if unit_family(a)!=unit_family(b):return None
    if a in scales and b in scales:return scales[a]/scales[b]
    return None


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
        if b == 'linear_speed' and primary_unit(source_unit) == 'mm/min': factor=1/25.4
        if factor and '(' in source_unit:
            primary,secondary=map(float,pair.groups());target=primary*factor
            # Catalogue rounding often uses a single decimal place.
            if abs(target-secondary)>max(.15,abs(target)*.015): out.append('source_metric_imperial_mismatch')
    if key in {'machine_weight_kg','machine_dimensions_mm'}:
        # Flag compound cells for human verification even if one pair looks fine.
        # No guessed division into installed magazine or attachment variants.
        if re.search(r'\b\d+T\b',value): out.append('configuration_compound_value')
    return sorted(set(out))
