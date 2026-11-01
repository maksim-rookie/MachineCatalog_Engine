"""Table-scoped identity: catalogue introductions never classify every model."""
from __future__ import annotations
from statistics import median
import re
from .geometry import norm


def table_identity(page, table, schema, header_text='', fallback=None):
    rules=[r for d in schema.config['dictionaries'] for r in d.get('machine_types',[])]
    # An explicit type qualifier in this model's header has highest priority.
    for rule in rules:
        if re.search(rule['pattern'],header_text,re.I):
            return rule['class'],rule['type'],'model_header'
    if fallback is not None:return fallback
    keys={m['key'] for row in table.rows if (m:=schema.match(row.labels,row.unit))}
    turning=bool(keys & {'max_turning_diameter_mm','max_turning_length_mm','bar_working_diameter_mm','turret_station_count'})
    machining={'travel_x_mm','travel_y_mm','travel_z_mm'}<=keys and bool(keys & {'table_size_mm','table_load_kg','pallet_size_mm'})
    inferred='turning' if turning else 'machining' if machining else 'unknown'
    lines=[]
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            spans=line.get('spans',[])
            if spans:
                lines.append((norm(''.join(s['text'] for s in spans)),max(s['size'] for s in spans),tuple(line['bbox'])))
    typical=median([size for text,size,box in lines if text] or [8])
    candidates=[]
    for text,size,box in lines:
        if not text or len(text)>120 or size<max(11,typical*1.4):continue
        # Titles inside another table/body must not leak across to this one.
        inside=table.bbox[0]<=box[0]<=table.bbox[2] and box[1]>table.bbox[1]
        if inside:continue
        for order,rule in enumerate(rules):
            if re.search(rule['pattern'],text,re.I):
                distance=abs(box[1]-table.bbox[1])+abs(box[0]-table.bbox[0])*.15
                candidates.append((distance,order,rule))
                break
    if candidates:
        _,_,rule=min(candidates,key=lambda item:(item[0],item[1]))
        if inferred=='unknown' or inferred==rule['class']:
            return rule['class'],rule['type'],'local_heading'
        return 'unknown','Unclassified machine','class_evidence_conflict'
    if inferred!='unknown':
        return inferred,('Turning Center' if inferred=='turning' else 'Machining Center'),'table_parameters'
    return 'unknown','Unclassified machine','insufficient_local_evidence'
