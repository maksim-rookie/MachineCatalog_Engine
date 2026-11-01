from __future__ import annotations
from pathlib import Path
import json
import re
import unicodedata
from .geometry import norm

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'schema.json'

class Schema:
    def __init__(self, path: Path = DEFAULT_CONFIG, overrides: Path | None = None):
        self.config=json.loads(path.read_text(encoding='utf-8'))
        # Embed dictionaries in the effective configuration: snapshots and cache
        # fingerprints must change when a translation changes.
        self.config['dictionaries']=[json.loads((path.parent/name).read_text(encoding='utf-8'))
                                     for name in self.config.get('dictionary_files',[])]
        translated=[rule for d in self.config['dictionaries'] for rule in d.get('rules',[])]
        self.config['rules']=translated+self.config['rules']
        if overrides and overrides.exists():
            extra=json.loads(overrides.read_text(encoding='utf-8'))
            self.config['rules']=extra.get('rules',[])+self.config['rules']
            self.config['label_aliases'].update(extra.get('label_aliases',{}))
            self.config['required_fields'].update(extra.get('required_fields',{}))
        self.rules=[]
        for rule in self.config['rules']:
            self.rules.append((rule,re.compile(rule['pattern'],re.I),re.compile(rule.get('section',''),re.I)))

    def header_is(self, text, role):
        value=norm(unicodedata.normalize('NFKC',text)).casefold()
        return any(value==norm(alias).casefold() for d in self.config['dictionaries']
                   for alias in d.get('table_headers',{}).get(role,[]))

    def has_table_header(self, text):
        return all(any(alias.casefold() in text.casefold() for d in self.config['dictionaries']
                       for alias in d.get('table_headers',{}).get(role,[]))
                   for role in ('description','unit'))

    def is_optional(self, text):
        return any(re.search(d['optional_pattern'],text,re.I) for d in self.config['dictionaries']
                   if d.get('optional_pattern'))

    def normalize_label(self, label: str):
        label=norm(unicodedata.normalize('NFKC',label)).lower()
        # Whitespace between a Latin axis and Chinese characters is layout only.
        label=re.sub(r'\s+(?=[\u3400-\u9fff])','',label)
        for d in self.config['dictionaries']:
            for old,new in d.get('sections',{}).items():
                if re.fullmatch(re.escape(old)+r'(?:[（(].*)?',label,re.I):
                    return new.lower()
        for old,new in self.config['label_aliases'].items():
            label=re.sub(r'\b'+re.escape(old)+r'\b',new,label)
        return label

    def match(self, labels: list[str], unit: str = ''):
        from .values import unit_family
        normalized=[self.normalize_label(x) for x in labels]
        full=' / '.join(normalized)
        leaf=normalized[-1] if normalized else ''
        section=normalized[0] if normalized else ''
        family=unit_family(unit)
        # Source writers occasionally use a misleading label (e.g. "Tool storage
        # capacity" under TABLE with kg/lb). Only an explicit section+unit
        # disambiguation may override such an inconsistent label, and it is
        # always flagged for review. The source quote remains unmodified.
        for special in self.config.get('section_unit_rules',[]):
            if (re.search(special['section'],section,re.I)
                    and re.search(special['pattern'],leaf,re.I)
                    and family==special['unit_family']):
                return {**special,'_schema_warning':'source_label_unit_context_disagreement'}
        candidates=[]
        for order,(rule,rx,sx) in enumerate(self.rules):
            if rule.get('section') and not sx.search(section): continue
            if rx.search(leaf):
                # Prefer a rule that matches the specific last sublabel to
                # an earlier group label such as "Type of tool shank".
                candidates.append((2,order,rule))
            elif rx.search(full):
                candidates.append((1,order,rule))
        if not candidates: return None
        _,_,rule=min(candidates,key=lambda item:(-item[0],item[1]))
        result=dict(rule)
        if result['key']=='chuck_size_inch' and re.search(r'\bmm\b',unit,re.I):
            result['key']='chuck_size_mm';result['unit']='mm'
        return result

    def required(self, machine_class):
        return self.config['required_fields'].get(machine_class,[])
