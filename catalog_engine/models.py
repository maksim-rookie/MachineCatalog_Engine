"""Models and their header-level configurations, not filenames or page profiles."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import re
import unicodedata
from .geometry import norm

# /50 is part of a spindle-interface variant; /8000ST is another model.
# Both DVF 8000/50 and DVF 8000T/50 are distinct machine identities.
# Capture a spindle-interface variant before OR after an alphabetic model suffix.
MODEL=re.compile(r'\b([A-Za-z](?:[A-Za-z-]{0,13}[A-Za-z])?)(\s*-?\s*)(\d{3,6})(/\d{2,3})?([A-Za-z]{0,12})(/\d{2,3})?((?:-[A-Za-z0-9]+)*)(?![A-Za-z0-9-])')
EXCLUDE={'ISO','HSK','DIN','CAT','FANUC','TNC','SIEMENS','BT','PS','S828D','S840D','DIRECT','BUILT','SINUMERIK'}

@dataclass(frozen=True)
class ModelRef:
    model: str
    model_base: str
    generation: str
    configuration: str
    header_raw: str
    header_qualifiers: tuple[str,...] = ()
    square_variant: str = ''

    def to_dict(self):
        return asdict(self)


def normalize_model(prefix, number, numeric_variant='', suffix=''):
    return prefix.upper()+' '+number+numeric_variant+suffix.upper()


def parse_header(text: str) -> list[ModelRef]:
    original=norm(text)
    s=norm(unicodedata.normalize('NFKC', original))
    qualifiers=tuple(re.findall(r'\[([A-Za-z][A-Za-z0-9-]*)\]',s))
    cleaned=re.sub(r'\[[A-Za-z][A-Za-z0-9-]*\]','',s)
    cleaned=re.sub(r'(?<=\d)\s+([A-Z]{1,6})(?=\s*(?:/|$))',r'\1',cleaned)
    # Superscript generation text is sometimes emitted BEFORE the model.
    gm=re.search(r'\b([1-9]\d?)(?:st|nd|rd|th)\b(?:\s*(?:gen(?:eration)?\.?))?',cleaned,re.I)
    generation=f'{gm.group(1)}' if gm else ''
    models=[]
    matches=[m for m in MODEL.finditer(cleaned) if m.group(1).upper().split('-')[0] not in EXCLUDE]
    for i,m in enumerate(matches):
        prefix,separator,number,numeric_variant,suffix,post_variant,dash_suffix=m.groups()
        numeric_variant=numeric_variant or ''
        # Never interpret a suffix/interface as an unrelated model.
        sep='-' if '-' in separator else ' ' if separator else ''
        base=prefix.upper()+sep+number+numeric_variant+suffix.upper()+(post_variant or '')+dash_suffix.upper()
        # A Chinese qualifier in a header cell can distinguish a physical
        # variant (e.g. graphite). Preserve it instead of merging identities.
        stop=matches[i+1].start() if i+1<len(matches) else len(cleaned)
        tail=cleaned[m.end():stop]
        qualifier=re.match(r'\s*([\u3400-\u9fff]+)',tail)
        if qualifier:base+=' '+qualifier.group(1)
        models.append(base)
        # Expand only slash-connected suffixes /LA/LMA or full numeric model
        # names; prose after the header must not create additional machines.
        while True:
            sm=re.match(r'\s*/\s*(\d{3,5}[A-Za-z]{0,7}|[A-Za-z]{1,7})(?=\s*(?:/|$|\s))',tail)
            if not sm: break
            tok=sm.group(1)
            if tok.upper() in {'PSC','FANUC','SIEMENS','TNC'}: break
            models.append(prefix.upper()+' '+(tok.upper() if tok[0].isdigit() else number+tok.upper()))
            tail=tail[sm.end():]
    if not models:
        return []
    models=list(dict.fromkeys(models))
    # Configuration qualifiers remain separate from model identities.
    cm=re.search(r'\b(\d{4,6})\s*r\s*/\s*min\b',cleaned,re.I)
    configuration='base'
    if cm:
        configuration=cm.group(1)+'rpm'
        if re.search(r'\bPSC\b',cleaned): configuration+=' / PSC'
    has_long_pair=len(models)==2 and bool(re.search(r'\dL[A-Z]*$',models[-1])) and not re.search(r'\dL[A-Z]*$',models[0])
    refs=[]
    for idx,base in enumerate(models):
        display=base+(f' {generation}nd Gen.' if generation=='2' else (f' Gen.{generation}' if generation else ''))
        refs.append(ModelRef(display,base,generation,configuration,original,qualifiers,
                             ('long' if idx==1 else 'short') if has_long_pair else ''))
    return refs


def split_top_level_commas(text: str) -> list[str]:
    result=[]; start=0; depth=0
    for i,c in enumerate(text):
        if c in '({[': depth+=1
        elif c in ')}]': depth=max(0,depth-1)
        elif c==',' and depth==0:
            result.append(text[start:i].strip());start=i+1
    result.append(text[start:].strip())
    return result


def select_square_variant(value: str, variant: str, has_legend: bool) -> tuple[str,str]:
    if not variant or not re.search(r'\[\s*\d',value):
        return value,''
    if not has_legend:
        return value,'square_bracket_semantics_unresolved'
    if variant=='short':
        result=norm(re.sub(r'\[[^]]*\]','',value))
        result=re.sub(r'\s+\)',')',result)
        return result,'square_bracket_short_from_legend'
    # Replace each metric AND imperial alternative. Preserve the original value
    # separately; no numerical value is sourced from outside the same cell.
    result=re.sub(r'[-+]?\d+(?:\.\d+)?\s*\[([^]]+)\]',r'\1',value)
    return norm(result),'square_bracket_long_from_legend'


def footnote_rules(page_text: str):
    out={}
    for m in re.finditer(r'(\*{2,})\s*(?:Only|Applies?\s+(?:to\s+)?only)\s+([^\n]+)',page_text,re.I):
        refs=parse_header(m.group(2))
        if refs: out[m.group(1)]={r.model_base for r in refs}
    return out


def select_footnote(value: str, model: str, rules: dict) -> tuple[str,str]:
    for marker,targets in sorted(rules.items(),key=lambda kv:-len(kv[0])):
        if marker not in value: continue
        chunks=split_top_level_commas(value)
        marked=[x for x in chunks if marker in x]
        unmarked=[x for x in chunks if marker not in x]
        if marked and unmarked:
            chosen=marked if model in targets else unmarked
            return norm(', '.join(chosen).replace(marker,'')),'model_only_footnote'
        pm=re.fullmatch(r'\s*([-+]?\d+(?:\.\d+)?)\s*\(([-+]?\d+(?:\.\d+)?)'+re.escape(marker)+r'\)\s*',value)
        if pm:
            return pm.group(2 if model in targets else 1),'model_only_footnote'
        return value,'footnote_applicability_unresolved'
    return value,''
