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
        synonym_file=self.config.get('parameter_synonym_file')
        self.config['parameter_synonyms']=(json.loads((path.parent/synonym_file).read_text(encoding='utf-8')).get('synonyms',[])
                                            if synonym_file else [])
        normalization_file = self.config.get('normalization_file')
        self.config['normalization'] = (json.loads((path.parent / normalization_file).read_text(encoding='utf-8'))
                                        if normalization_file else {})
        translated=[rule for d in self.config['dictionaries'] for rule in d.get('rules',[])]
        self.config['rules']=translated+self.config['rules']
        if overrides and overrides.exists():
            extra=json.loads(overrides.read_text(encoding='utf-8'))
            for name, values in extra.get('normalization', {}).items():
                self.config['normalization'].setdefault(name, {}).update(values)
            self.config['rules']=extra.get('rules',[])+self.config['rules']
            self.config['parameter_synonyms']=extra.get('parameter_synonyms',[])+self.config['parameter_synonyms']
            self.config['model_family_rules']=extra.get('model_family_rules',[])+self.config.get('model_family_rules',[])
            self.config['cover_machine_types']=extra.get('cover_machine_types',[])+self.config.get('cover_machine_types',[])
            for key in ('parameter_order','parameter_groups'):
                if key in extra:self.config[key]=extra[key]
            self.config['label_aliases'].update(extra.get('label_aliases',{}))
            self.config['required_fields'].update(extra.get('required_fields',{}))
            self.config['manufacturers']=extra.get('manufacturers',[])+self.config.get('manufacturers',[])
            self.config.setdefault('required_any',{}).update(extra.get('required_any',{}))
            self.config['machine_types']=extra.get('machine_types',[])+self.config.get('machine_types',[])
            overrides={x['key']:x for x in extra.get('machine_classes',[])}
            self.config['machine_classes']=list(overrides.values())+[x for x in self.config.get('machine_classes',[]) if x['key'] not in overrides]
            for key in ('classification_min_margin','shared_text_recovery_keys','shared_text_min_coverage'):
                if key in extra:self.config[key]=extra[key]
        self.synonyms=[]
        for rule in self.config.get('parameter_synonyms',[]):
            labels={self.normalize_label(label) for label in rule.get('labels',[])}
            self.synonyms.append((rule,labels,re.compile(rule.get('section',''),re.I)))
        self.rules=[]
        for rule in self.config['rules']:
            self.rules.append((rule,re.compile(rule['pattern'],re.I),re.compile(rule.get('section',''),re.I)))

    def identify_manufacturer(self, pages):
        """Identify explicit maker text; unknown names are not invented.

        Prefer the cover and introductory pages over passing mentions elsewhere.
        Manufacturer patterns are editable in config/schema.json or overrides.
        """
        sources=['\n'.join(p['text'] for p in pages[:2]),
                 '\n'.join(p['text'] for p in pages)]
        for text in sources:
            for entry in self.config.get('manufacturers',[]):
                if re.search(entry['pattern'],text,re.I):return entry['name']
        return ''

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
        # Exact multilingual aliases precede broad regex patterns. Section
        # scoping prevents 'X-axis' travel from becoming rapid traverse.
        for rule,names,section_rx in self.synonyms:
            if leaf not in names or (rule.get('section') and not section_rx.search(section)):
                continue
            if rule.get('unit_family') and family and rule['unit_family']!=family:
                continue
            return {**rule,'category':self.parameter_group(rule['key'],rule['category']),
                    '_matched_synonym':leaf}
        candidates=[]
        for order,(rule,rx,sx) in enumerate(self.rules):
            if rule.get('section') and not sx.search(section): continue
            if rule.get('context') and not re.search(rule['context'],full,re.I):continue
            if rule.get('unit_family') and family and family!=rule['unit_family']:continue
            if rx.search(leaf):
                # Prefer a rule that matches the specific last sublabel to
                # an earlier group label such as "Type of tool shank".
                candidates.append((2,order,rule))
            elif rx.search(full):
                candidates.append((1,order,rule))
        if not candidates: return None
        _,_,rule=min(candidates,key=lambda item:(-item[0],item[1]))
        result=dict(rule)
        result['category']=self.parameter_group(result['key'],result.get('category','Other'))
        if result['key']=='chuck_size_inch' and re.search(r'\bmm\b',unit,re.I):
            result['key']='chuck_size_mm';result['unit']='mm'
        return result

    def parameter_group(self, key, fallback='Other'):
        for category,params in self.config.get('parameter_order',{}).items():
            if key in params:return category
        # Controlled prefix conventions let older and newly added rules retain
        # predictable ordering even when a category is missing in one language.
        for prefix,category in [('travel_','Travel'),('spindle_','Spindle'),
                 ('table_','Table'),('pallet_','Table'),('rapid_traverse_','Feedrate'),
                 ('cutting_feed_','Feedrate'),('tool_','Automatic tool changer (ATC)'),
                 ('machine_weight_','Machine dimensions'),('machine_height_','Machine dimensions'),
                 ('machine_length_','Machine dimensions'),('machine_width_','Machine dimensions'),
                 ('machine_dimensions_','Machine dimensions'),('control_','Control')]:
            if key.startswith(prefix):return category
        return fallback

    def required(self, machine_class):
        return self.config['required_fields'].get(machine_class,[])

    def required_alternatives(self,machine_class):
        return self.config.get('required_any',{}).get(machine_class,[])
