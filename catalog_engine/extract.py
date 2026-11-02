"""Catalogue extraction with source-scoped models, configurations and values."""
from __future__ import annotations
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import time
import pymupdf as fitz
from .geometry import extract_tables, plumber_fallback, recover_trailing_rows, candidate_shared_text_span, norm
from .models import parse_header, select_square_variant, footnote_rules, select_footnote, select_named_model_exception
from .schema import Schema
from .values import split_embedded_conditions, option_parts, value_warnings
from . import ENGINE_VERSION
from .identity import table_identity
from .families import resolve_family
from .classification import document_identity


def page_kind(text: str) -> str:
    top=norm(text)[:1800].lower()
    if re.search(r'(?:machine|main|technical) specifications',top[:450]): return 'machine_specifications'
    if re.search(r'standard.*optional specifications',top): return 'options_matrix'
    if 'numeric control specifications' in top or 'numeric control specifications' in text.lower(): return 'controller_specifications'
    if re.search(r'power\s*\|\s*torque',top): return 'power_torque_diagrams'
    if re.search(r'tooling system|tool interface|working range|dimensions',top[:350]): return 'drawing_or_accessory'
    return 'supplemental'


def machine_identity(pages, schema):
    beginning=' '.join(p['text'] for p in pages[:3])
    alltext=' '.join(p['text'] for p in pages)
    manufacturer=schema.identify_manufacturer(pages)
    if re.search(r'turning cent(?:er|re)|turn[- ]mill',beginning,re.I):
        return manufacturer,'turning','Turning Center'
    if re.search(r'machining cent(?:er|re)',beginning,re.I):
        five=bool(re.search(r'5[- ]?axis|five[- ]axis',beginning,re.I))
        orient='Horizontal' if re.search(r'horizontal machining',beginning,re.I) else 'Vertical' if re.search(r'vertical machining',beginning,re.I) else ''
        return manufacturer,'machining',('5-Axis ' if five else '')+(orient+' ' if orient else '')+'Machining Center'
    return manufacturer,'unknown','Unclassified machine'


def issue(source_file, page, reason, **kwargs):
    return {'source_file':source_file,'source_page':page,'reason':reason,**kwargs}


def extract_pdf(path: Path, schema: Schema | None = None, progress=None) -> dict:
    schema=schema or Schema()
    started=time.perf_counter()
    fitz.TOOLS.set_small_glyph_heights(True)
    result={'source_file':path.name,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'engine_version':ENGINE_VERSION,'facts':[],'review':[],'registry':{},'cell_statuses':[],
            'raw_tables':[],'unmapped_fields':[],'source_pages':[],'ocr_events':[],'spec_pages':[]}
    with fitz.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError('Encrypted PDF requires a password; no extraction was attempted.')
        total=len(pdf)
        for i,page in enumerate(pdf):
            text=page.get_text('text',sort=True)
            words=page.get_text('words',sort=True)
            result['source_pages'].append({'source_file':path.name,'page':i+1,'width_pt':page.rect.width,
                'height_pt':page.rect.height,'kind':page_kind(text),'text':text,
                'words':[{'bbox':[round(x,3) for x in w[:4]],'text':w[4]} for w in words]})
            if progress: progress(.30*(i+1)/total,'Reading native PDF',f'page {i+1}/{total}')
        read_elapsed=time.perf_counter()-started
        extraction_started=time.perf_counter()
        manufacturer,machine_class,machine_type=machine_identity(result['source_pages'],schema)
        cover_identity=document_identity(result['source_pages'],schema)
        result.update(manufacturer=manufacturer,machine_class=machine_class,machine_type=machine_type)
        alltext=norm(' '.join(p['text'] for p in result['source_pages']))
        long_legend=bool(re.search(r'\[\s*\]\s*:?\s*long bed',alltext,re.I))
        result['source_legends']={'square_brackets_long_bed':long_legend}
        for pi,page in enumerate(pdf):
            sourcepage=result['source_pages'][pi]
            if not sourcepage['words']:
                result['review'].append(issue(path.name,pi+1,'no_native_text',instruction='Page may be scanned. Its image must be reviewed or processed with an explicit OCR adapter.'))
            declared_main=sourcepage['kind']=='machine_specifications'
            # Unlabelled comparison tables can qualify by structure + several
            # independent parameter categories. An accessory table must not
            # become the machine's main specifications by default.
            candidate=(sourcepage['kind']=='supplemental' and schema.has_table_header(sourcepage['text']))
            if not declared_main and not candidate:
                if progress: progress(.30+.65*(pi+1)/total,'Classifying pages',f'page {pi+1}/{total}: {sourcepage["kind"]}')
                continue
            tables=extract_tables(page,pi+1,schema)
            if not tables:
                tables=plumber_fallback(path,pi+1,page,schema)
            if not declared_main:
                accepted=[]
                for t in tables:
                    mapped=[schema.match(r.labels,r.unit) for r in t.rows]
                    mapped=[m for m in mapped if m]
                    if len(mapped)>=5 and len({m['category'] for m in mapped})>=3:
                        accepted.append(t)
                tables=accepted
                if tables: sourcepage['kind']='machine_specifications_structural'
            if not tables:
                if declared_main:
                    result['review'].append(issue(path.name,pi+1,'specification_geometry_unresolved',instruction='Raw text and word coordinates retained; no speculative assignments.'))
                continue
            # A final control/NC row may sit below the last vector cell.
            # Recover only nearby, schema-recognized text with source coordinates.
            for t in tables:
                recover_trailing_rows(page,t,schema)
            result['spec_pages'].append(pi+1)
            fnrules=footnote_rules(sourcepage['text']+'\n'+page.get_text('text'))
            for ti,table in enumerate(tables,1):
                table_class,table_type,class_evidence=table_identity(page,table,schema,fallback=cover_identity)
                # A duplicate identity in distinct header columns must never
                # silently merge values from different physical machines.
                header_identities={}
                for hi,h in enumerate(table.headers):
                    for ref in parse_header(h.text):
                        pair=(ref.model,ref.configuration)
                        if pair in header_identities:
                            result['review'].append(issue(path.name,pi+1,'model_identity_collision',
                                model=ref.model,configuration=ref.configuration,
                                header_raw=h.text,other_header=header_identities[pair],
                                instruction='Two distinct header columns resolve to one identity; preserve raw table and do not trust combined values.'))
                        header_identities[pair]=h.text
                result['raw_tables'].append({'source_file':path.name,'source_table':ti,
                                            'machine_class':table_class,'machine_type':table_type,
                                            'class_evidence':class_evidence,**table.to_dict()})
                header_refs=[]
                for header in table.headers:
                    refs=parse_header(header.text)
                    header_refs.append((header,refs))
                    if any(ref.header_qualifiers for ref in refs):
                        result['review'].append(issue(path.name,pi+1,'header_qualifier_requires_review',header_raw=header.text,models=[r.model for r in refs],instruction='Bracketed header qualifier retained; not promoted to an independent model or assumed for all variants.'))
                    if not refs:
                        result['review'].append(issue(path.name,pi+1,'model_header_unresolved',header_raw=header.text,bbox=header.bbox))
                    for ref in refs:
                        model_class,model_type,evidence=table_identity(page,table,schema,header.text,
                                                                      (table_class,table_type,class_evidence))
                        family,variant,family_rule=resolve_family(ref.model,schema)
                        meta=result['registry'].setdefault(ref.model,{'model':ref.model,'model_base':ref.model_base,
                            'base_model':family,'model_variant':variant,'model_family_rule':family_rule,
                            'generation':ref.generation,'manufacturer':manufacturer,'machine_class':model_class,
                            'machine_type':model_type,'class_evidence':evidence,'evidence':'explicit_specification_header','headers':[],
                            'configurations':[],'header_qualifiers':[]})
                        if meta['machine_class']!=model_class or meta['machine_type']!=model_type:
                            meta.update(machine_class='unknown',machine_type='Unclassified machine',class_evidence='class_evidence_conflict')
                            result['review'].append(issue(path.name,pi+1,'model_class_conflict',model=ref.model))
                        if ref.configuration not in meta['configurations']: meta['configurations'].append(ref.configuration)
                        if ref.header_raw not in meta['headers']: meta['headers'].append(ref.header_raw)
                        meta['header_qualifiers']=sorted(set(meta['header_qualifiers'])|set(ref.header_qualifiers))
                section_context=''
                for row in table.rows:
                    if row.labels and row.labels[-1].startswith(('»','›','▶')) and not row.unit and not any(c.text.strip() for c in row.values):
                        section_context=row.labels[-1].lstrip('»›▶ ').strip()
                        continue
                    if section_context and len(row.labels)==1:row.labels=[section_context]+row.labels
                    for warning in row.warnings:
                        result['review'].append(issue(path.name,pi+1,warning,source_table=ti,source_row=row.row_index))
                    rule=schema.match(row.labels,row.unit)
                    if rule is None:
                        result['unmapped_fields'].append({'source_file':path.name,'source_page':pi+1,'source_table':ti,
                            'source_row':row.row_index,'source_label':' / '.join(row.labels),'unit_source':row.unit,
                            'cells':[{"value_raw":c.text,"bbox":c.bbox} for c in row.values]})
                    inferred=candidate_shared_text_span(row,table.headers,
                        rule['key'] if rule else '',
                        schema.config.get('shared_text_recovery_keys',[]),
                        schema.config.get('shared_text_min_coverage',.75))
                    cell_iter=[(cell,'') for cell in row.values]
                    if inferred:
                        cell_iter=[(inferred,'shared_text_span_inferred')]
                        result['review'].append(issue(path.name,pi+1,'shared_text_span_inferred',
                            parameter=rule['key'],source_row_id=f'{pi+1}:{ti}:{row.row_index}',
                            source_cell_bbox=list(row.values[0].bbox),
                            blank_cell_bbox=list(row.values[-1].bbox),
                            instruction='An existing text value appears to span the last model; review applicability.'))
                    for cell,scope_recovery in cell_iter:
                        # The overlapping *cell span* determines applicability,
                        # never just the nearest text centre.
                        assignments=[]
                        for header,refs in header_refs:
                            overlap=min(cell.bbox[2],header.bbox[2])-max(cell.bbox[0],header.bbox[0])
                            smaller=min(cell.bbox[2]-cell.bbox[0],header.bbox[2]-header.bbox[0])
                            if overlap>max(1,smaller*.45): assignments.extend(refs)
                        if not assignments:
                            result['review'].append(issue(path.name,pi+1,'value_without_model',value_raw=cell.text,bbox=cell.bbox))
                            continue
                        for ref in assignments:
                            model_meta=result['registry'][ref.model]
                            rowid=f'{pi+1}:{ti}:{row.row_index}'
                            record={'source_file':path.name,'source_page':pi+1,'source_table':ti,'source_row':row.row_index,
                                'source_row_id':rowid,'model':ref.model,'model_base':ref.model_base,
                                'base_model':model_meta['base_model'],'model_variant':model_meta['model_variant'],
                                'model_family_rule':model_meta['model_family_rule'],'generation':ref.generation,
                                'configuration':ref.configuration,'source_label':' / '.join(row.labels),
                                'unit_source':row.unit,'value_raw':cell.text,'cell_bbox':list(cell.bbox),
                                'source_header':ref.header_raw,'header_qualifiers':list(ref.header_qualifiers),
                                'manufacturer':manufacturer,'machine_type':model_meta['machine_type'],'machine_class':model_meta['machine_class'],
                                'method':table.method,'scope':'explicit_specification_cell',
                                'recovery_note':getattr(row,'recovery','')}
                            original_cells = [c for c in row.values if c.text == cell.text and c.text.strip()]
                            if scope_recovery and len(original_cells) == 1:
                                record['source_cell_bbox'] = list(original_cells[0].bbox)
                            if not rule:
                                result['cell_statuses'].append({**record,'parameter':'','status':'UNMAPPED_LABEL'})
                                continue
                            key=rule['key']
                            record.update(parameter=key,parameter_normalized=key,
                                          category=schema.parameter_group(key,rule['category']),
                                          source_group=' / '.join(row.labels[:-1]),
                                          matched_synonym=rule.get('_matched_synonym',''),
                                          unit_expected=rule['unit'])
                            applicable=[r.model_base for _,refs in header_refs for r in refs]
                            named_value,named_transform=select_named_model_exception(cell.text,ref.model_base,applicable)
                            selected,transform=select_square_variant(named_value,ref.square_variant,long_legend)
                            selected,fntransform=select_footnote(selected,ref.model_base,fnrules)
                            transformations=[x for x in [named_transform,transform,fntransform] if x]
                            # A numeric speed explicitly present in the header AND
                            # in the shared speed cell defines this configuration.
                            hs=re.match(r'(\d+)rpm',ref.configuration)
                            if hs and key=='spindle_speed_rpm' and hs.group(1) in re.findall(r'\d+',selected):
                                selected=hs.group(1); transformations.append('explicit_header_speed_configuration')
                            split=split_embedded_conditions(selected,row.labels,key)
                            for part in split:
                                val=norm(part['value']);base,options,availability=option_parts(val,key)
                                optional=schema.is_optional(' / '.join(row.labels))
                                if optional and availability not in {'not_applicable','missing'}:
                                    base='';options=list(dict.fromkeys(options+[val]));availability='optional_only'
                                warnings=value_warnings(val,rule['unit'],row.unit,key)
                                warnings.extend(row.warnings)
                                if scope_recovery:warnings.append(scope_recovery)
                                if optional: warnings.append('optional_equipment')
                                if rule.get('_schema_warning'): warnings.append(rule['_schema_warning'])
                                if getattr(row,'recovery','')=='below_geometric_table_edge': warnings.append('geometric_tail_row_recovered')
                                if part.get('warning'): warnings.append(part['warning'])
                                warnings.extend(x for x in transformations if x.endswith('unresolved'))
                                # A grouped model header is insufficient to prove
                                # that all optional values apply to every member.
                                if options and len(parse_header(ref.header_raw))>1 and fnrules:
                                    warnings.append('shared_model_options_require_applicability_check')
                                cond=part['condition']
                                status='SOURCE_BLANK' if not val else ('NOT_APPLICABLE' if availability=='not_applicable' else 'EXTRACTED_REVIEW' if warnings else 'EXTRACTED')
                                result['cell_statuses'].append({**record,'condition':cond,'status':status,'value':val})
                                if not val:
                                    result['review'].append(issue(path.name,pi+1,'empty_source_cell',model=ref.model,configuration=ref.configuration,parameter=key,source_row_id=rowid,cell_bbox=list(cell.bbox)))
                                    continue
                                if scope_recovery:record['recovery_note']= '; '.join(x for x in (record['recovery_note'],scope_recovery) if x)
                                fact={**record,'value':val,'base_value':base,'options':options,
                                    'availability':availability,'option_kind':'section_optional' if optional else 'brace_options' if options else '',
                                    'condition':'; '.join(f'{k}={v}' for k,v in sorted(cond.items())),
                                    'condition_data':cond,'duty_cycle':part['duty_cycle'],
                                    'transformations':transformations,'warnings':sorted(set(warnings)),
                                    'review_status':'NEEDS_REVIEW' if warnings else 'UNVERIFIED',
                                    'source_quote':' | '.join(row.labels+[row.unit,cell.text]),'human_verified':False}
                                fact['fact_id']=hashlib.sha256(json.dumps([result['source_sha256'],rowid,ref.model,ref.configuration,key,cond,val],sort_keys=True).encode()).hexdigest()[:20]
                                result['facts'].append(fact)
                                for warning in sorted(set(warnings)):
                                    result['review'].append(issue(path.name,pi+1,warning,model=ref.model,configuration=ref.configuration,
                                        parameter=key,value_raw=cell.text,source_row_id=rowid,fact_id=fact['fact_id']))
            if progress: progress(.30+.65*(pi+1)/total,'Extracting specification cells',f'page {pi+1}/{total}')
    # Duplicate PDF drawing objects must not multiply facts.
    result['facts']=list({f['fact_id']:f for f in result['facts']}.values())
    classes={m['machine_class'] for m in result['registry'].values()}
    types={m['machine_type'] for m in result['registry'].values()}
    result['machine_class']=next(iter(classes)) if len(classes)==1 else 'mixed' if classes else 'unknown'
    result['machine_type']=next(iter(types)) if len(types)==1 else 'Mixed catalogue' if types else 'Unclassified machine'
    if not result['registry']:
        result['review'].append(issue(path.name,0,'no_supported_specification_table',instruction='No model measurements were guessed. Inspect SOURCE_PAGES.jsonl and add a geometry/schema adapter.'))
    result['native_timings']={'read_seconds':round(read_elapsed,6),'extract_seconds':round(time.perf_counter()-extraction_started,6)}
    if progress: progress(1,'Extraction finished',f'{len(result["registry"])} models, {len(result["facts"])} records')
    return result
