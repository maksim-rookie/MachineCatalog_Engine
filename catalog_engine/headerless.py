"""Recover headerless specification tables from explicit model and unit anchors.

No catalogue names, page numbers or equally divided columns are used. A table
must contain several recognized rows from independent parameter categories.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
import re

from .geometry import Cell, LogicalRow, LogicalTable, cell_text, cluster, norm
from .models import parse_header

SECTIONS = {'accuracy','travel','travels','table','feed','feedrate','feedrates',
            'spindle','tool magazine','automatic tool changer','capacity','machine',
            'machine dimensions','control'}

UNIT = re.compile(r'^\(?(?:mm|cm|kg|m/min|mm/min|r/min|rpm|deg|sec|pcs|kW)(?:\([^)]*\))?\)?$', re.I)


def _lines(words, tolerance=2):
    result = []
    for word in sorted(words, key=lambda w: ((w[1]+w[3])/2, w[0])):
        y = (word[1]+word[3])/2
        if not result or abs(y-result[-1][0]) > tolerance:
            result.append((y, [word]))
        else:
            result[-1][1].append(word)
    return result


def headerless_tables(page, page_number, schema):
    words = page.get_text('words', sort=True)
    candidates = [w for w in words if len(parse_header(w[4])) == 1]
    tables = []
    header_lines = _lines(candidates, 4)
    for line_index, (_, anchors) in enumerate(header_lines):
        bottom = max(w[3] for w in anchors)
        stop = header_lines[line_index+1][0] - 5 if line_index+1 < len(header_lines) else page.rect.height
        unit_words = [w for w in words if bottom < w[1] < stop and UNIT.fullmatch(w[4])]
        if len(unit_words) < 3:
            continue
        # Both left-aligned and right-aligned unit columns occur in catalogues.
        # Select an observed edge cluster, rather than a fixed column width.
        alignments = []
        for coordinate in (2, 0):
            for edge in cluster([w[coordinate] for w in unit_words], tolerance=1.5):
                group = [w for w in unit_words if abs(w[coordinate]-edge) < 1.6]
                alignments.append(group)
        unit_words = max(alignments, key=len)
        right = max(w[2] for w in unit_words)
        if len(unit_words) < 3:
            continue
        anchors = sorted((w for w in anchors if (w[0]+w[2])/2 > right), key=lambda w: w[0])
        if not anchors:
            continue
        centers = [(w[0]+w[2])/2 for w in anchors]
        unit_left = min(w[0] for w in unit_words)
        data_left = right+1
        last = min(page.rect.width-1, centers[-1]+(centers[-1]-centers[-2])/2) if len(centers)>1 else page.rect.width-1
        boundaries = [data_left]+[(a+b)/2 for a,b in zip(centers, centers[1:])]+[last]
        if any(a >= b for a,b in zip(boundaries, boundaries[1:])):
            continue
        headers = [Cell((a, min(w[1] for w in anchors), b, bottom), anchor[4])
                   for a,b,anchor in zip(boundaries, boundaries[1:], anchors)]
        last_row = max(w[3] for w in unit_words)+12
        label_words = [w for w in words if bottom < w[1] < min(stop,last_row)
                       and w[0] < unit_left-1 and not w[4].isdigit()]
        lines = _lines(label_words)
        if not lines:
            continue
        for header in headers:
            full_header = cell_text(words,(header.bbox[0],header.bbox[1]-1,header.bbox[2],min(w[1] for w in label_words)-1))
            if full_header:
                header.text = norm(full_header)
        rows, context = [], ''
        for i,(y,line) in enumerate(lines):
            top = (lines[i-1][0]+y)/2 if i else bottom
            low = (lines[i+1][0]+y)/2 if i+1<len(lines) else min(stop,last_row)
            label = norm(' '.join(w[4] for w in sorted(line,key=lambda w:w[0])))
            units = [w for w in unit_words if top < (w[1]+w[3])/2 < low]
            unit = norm(' '.join(w[4] for w in units))
            values = [Cell((a,top,b,low), cell_text(words,(a,top,b,low)))
                      for a,b in zip(boundaries,boundaries[1:])]
            if not unit and (label.casefold() in SECTIONS or not any(c.text for c in values)):
                context = label
                continue
            labels = [context,label] if context else [label]
            if schema.match(labels,unit) is None and schema.match([label],unit):
                labels = [label]
            warnings = []
            for w in words:
                if top < (w[1]+w[3])/2 < low and w[0] >= data_left:
                    if any(w[0]+.5 < edge < w[2]-.5 for edge in boundaries[1:-1]):
                        warnings.append('ambiguous_column_alignment')
                        break
            rows.append(LogicalRow(i,(min(w[0] for w in label_words),top,last,low),labels,unit,values,warnings=tuple(warnings)))
        matched = [rule for row in rows if (rule := schema.match(row.labels,row.unit))]
        if len(matched) >= 3 and len({r['category'] for r in matched}) >= 3:
            tables.append(LogicalTable(page_number,(min(w[0] for w in label_words),headers[0].bbox[1],last,last_row),
                                       headers,rows,'headerless_model_unit_anchors'))
    return tables
