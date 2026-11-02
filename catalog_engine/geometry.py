"""Recover logical table cells from PDF vector fills and ruling lines.

Cell geometry, including merged ranges, is retained. A blank cell is never
forward-filled merely because a preceding model has a value.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Iterable
import re

Box = tuple[float, float, float, float]


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def cluster(values: Iterable[float], tolerance: float = 0.8) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def contains(box: Box, x: float, y: float, tolerance: float = 0.15) -> bool:
    return box[0] - tolerance <= x <= box[2] + tolerance and box[1] - tolerance <= y <= box[3] + tolerance


def cell_text(words: list, box: Box) -> str:
    chosen = [w for w in words if contains(box, (w[0]+w[2])/2, (w[1]+w[3])/2)]
    # Work with visual lines rather than PDF object order, which may interleave
    # whole columns. Font-size-independent line grouping preserves multiline cells.
    lines: list[list] = []
    for word in sorted(chosen, key=lambda w: ((w[1]+w[3])/2, w[0])):
        cy = (word[1]+word[3])/2
        tol = max(0.9, min(2.2, (word[3]-word[1])*0.25))
        if lines and abs(cy - sum((w[1]+w[3])/2 for w in lines[-1])/len(lines[-1])) <= tol:
            lines[-1].append(word)
        else:
            lines.append([word])
    return "\n".join(" ".join(w[4] for w in sorted(line, key=lambda w:w[0])) for line in lines)


@dataclass
class Cell:
    bbox: Box
    text: str


@dataclass
class LogicalRow:
    row_index: int
    bbox: Box
    labels: list[str]
    unit: str
    values: list[Cell]
    recovery: str = ''
    warnings: tuple[str, ...] = ()


@dataclass
class LogicalTable:
    page: int
    bbox: Box
    headers: list[Cell]
    rows: list[LogicalRow]
    method: str

    def to_dict(self):
        return asdict(self)


def primitives(page):
    rectangles, verticals, horizontals = [], [], []
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "re":
                r = item[1]
                b = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
                if r.width > 3 and r.height > 2:
                    rectangles.append(b)
                verticals += [(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)]
                horizontals += [(r.x0, r.y0, r.x1), (r.x0, r.y1, r.x1)]
            elif item[0] == "l":
                a, b = item[1:3]
                if abs(a.x-b.x) < 0.3 and abs(a.y-b.y) > 2:
                    verticals.append(((a.x+b.x)/2, min(a.y,b.y), max(a.y,b.y)))
                if abs(a.y-b.y) < 0.3 and abs(a.x-b.x) > 3:
                    horizontals.append((min(a.x,b.x), (a.y+b.y)/2, max(a.x,b.x)))
    unique = {tuple(round(v,1) for v in r):r for r in rectangles}
    return list(unique.values()), verticals, horizontals


def smallest_rect(rectangles, word, max_height=100):
    x, y = (word[0]+word[2])/2, (word[1]+word[3])/2
    candidates=[r for r in rectangles if contains(r,x,y) and r[3]-r[1]<max_height]
    return min(candidates, key=lambda r:(r[2]-r[0])*(r[3]-r[1])) if candidates else None


def extract_tables(page, page_number: int, schema=None) -> list[LogicalTable]:
    """Detect configured multilingual table headers without catalogue profiles."""
    from .models import parse_header
    words=page.get_text("words", sort=True)
    rects, vs, hs = primitives(page)
    if schema is None:
        from .schema import Schema
        schema=Schema()
    units=[w for w in words if schema.header_is(w[4],'unit')]
    descriptions=[w for w in words if schema.header_is(w[4],'description')]
    tables=[]
    for uw in units:
        uc=smallest_rect(rects,uw,60)
        if not uc:
            continue
        nearby=[w for w in descriptions if w[0]<uw[0] and abs((w[1]+w[3]-uw[1]-uw[3])/2)<15]
        if not nearby:
            continue
        dc=smallest_rect(rects,nearby[0],60)
        if not dc:
            continue
        header_top,header_bottom=uc[1],uc[3]
        x_left,x_unit,x_data=dc[0],uc[0],uc[2]
        # True header cells, not equally divided invented model columns.
        headers=[r for r in rects if abs(r[1]-header_top)<1 and abs(r[3]-header_bottom)<1 and r[0]>=x_data-0.8]
        headers=sorted(headers,key=lambda r:r[0])
        if not headers:
            header_edges=cluster([x_data]+[x for x,y0,y1 in vs if y0<=header_top+1 and y1>=header_bottom-1 and x>x_data])
            headers=[(a,header_top,b,header_bottom) for a,b in zip(header_edges,header_edges[1:]) if b-a>8]
        if not headers:
            continue
        right=max(r[2] for r in headers)
        header_cells=[Cell(r,cell_text(words,r)) for r in headers]
        if not any(parse_header(c.text) for c in header_cells):
            continue
        unit_rows=[r for r in rects if abs(r[0]-x_unit)<.8 and abs(r[2]-x_data)<.8 and r[1]>=header_bottom-.8 and r[3]-r[1]>3]
        # Some designers draw only lines around white cells, no fill rectangles.
        if not unit_rows:
            ys=cluster([header_bottom]+[y for x0,y,x1 in hs if x0<x_unit+1 and x1>x_data-1 and y>header_bottom])
            unit_rows=[(x_unit,a,x_data,b) for a,b in zip(ys,ys[1:]) if b-a>3]
        unit_rows=sorted(unit_rows,key=lambda r:r[1])
        rows=[]
        for idx,ur in enumerate(unit_rows):
            top,bottom=ur[1],ur[3]
            if bottom-top>200:  # page backgrounds must never be treated as rows
                continue
            mid=(top+bottom)/2
            label_rects=[r for r in rects if r[0]>=x_left-.8 and r[2]<=x_unit+.8 and r[1]<=mid<=r[3] and r[3]-r[1]<page.rect.height*.85]
            label_rects=sorted(label_rects,key=lambda r:(r[0],r[2]-r[0]))
            labels=[]
            last_right=x_left
            for r in label_rects:
                if r[0]<last_right-.8:
                    continue
                text=norm(cell_text(words,r))
                if text:
                    labels.append(text)
                last_right=r[2]
            if not labels:
                text=norm(cell_text(words,(x_left,top,x_unit,bottom)))
                if text: labels=[text]
            # A divider must cross the centre of this particular row. Header
            # dividers alone do NOT subdivide merged body cells.
            xs=cluster([x_data,right]+[x for x,y0,y1 in vs if x_data+.8<x<right-.8 and y0+.15<mid<y1-.15])
            vals=[]
            for a,b in zip(xs,xs[1:]):
                if b-a>3:
                    box=(a,top,b,bottom)
                    vals.append(Cell(box,cell_text(words,box)))
            rows.append(LogicalRow(idx,(x_left,top,right,bottom),labels,norm(cell_text(words,ur)),vals))
        if rows:
            tables.append(LogicalTable(page_number,(x_left,header_top,right,max(r.bbox[3] for r in rows)),header_cells,rows,"native_vector_cells"))
    # Equal headers can be found twice in PDFs with repeated hidden text.
    tables=list({tuple(round(x,1) for x in t.bbox):t for t in tables}.values())
    # Prefer physical cells wherever they exist. Coordinate recovery is scoped
    # to headers for which no ruled table was recovered.
    for recovered in coordinate_tables(page,page_number,schema):
        if not any(contains(t.bbox,recovered.headers[0].bbox[0]+1,
                            sum(recovered.headers[0].bbox[1::2])/2) for t in tables):
            tables.append(recovered)
    return tables


def coordinate_tables(page, page_number, schema):
    """Recover columns from actual header anchors, never equal page divisions.

    Horizontal rules delimit rows when available. Text baselines are a fallback
    for fully borderless tables. Empty model cells stay empty; text straddling
    two inferred columns is retained for review rather than copied to both.
    """
    from .models import parse_header
    from statistics import median
    words=page.get_text('words',sort=True)
    rects,_,horizontal=primitives(page)
    units=[w for w in words if schema.header_is(w[4],'unit')]
    descriptions=[w for w in words if schema.header_is(w[4],'description')]
    tables=[]
    for uw in units:
        cy=(uw[1]+uw[3])/2; height=uw[3]-uw[1]
        unit_center=(uw[0]+uw[2])/2
        ds=[w for w in descriptions if w[0]<uw[0] and abs((w[1]+w[3])/2-cy)<height*.6]
        if not ds: continue
        dw=max(ds,key=lambda w:w[0])
        header_right=page.rect.width
        nearby_rules=[(a,y,b) for a,y,b in horizontal if a<=unit_center<=b and cy<y<cy+height*3]
        if nearby_rules:
            first_y=min(y for a,y,b in nearby_rules)
            intervals=[]
            for a,y,b in sorted(horizontal):
                if abs(y-first_y)>.8:continue
                if intervals and a<=intervals[-1][1]+2:intervals[-1][1]=max(intervals[-1][1],b)
                else:intervals.append([a,b])
            containing=[b for a,b in intervals if a<=unit_center<=b]
            if containing:header_right=max(containing)
        header_words=sorted([w for w in words if uw[2]+2<w[0]<header_right
                             and abs((w[1]+w[3])/2-cy)<height*.6],key=lambda w:w[0])
        groups=[]
        for w in header_words:
            if not groups or w[0]-groups[-1][-1][2]>max(10,height*1.2):groups.append([w])
            else:groups[-1].append(w)
        groups=[g for g in groups if parse_header(' '.join(w[4] for w in g))]
        if not groups:continue
        centers=[(min(w[0] for w in g)+max(w[2] for w in g))/2 for g in groups]
        edges=[(unit_center+centers[0])/2]+[(a+b)/2 for a,b in zip(centers,centers[1:])]
        edges.append(min(page.rect.width,centers[-1]+(centers[-1]-(centers[-2] if len(centers)>1 else unit_center))/2))
        xleft=dw[0]-2; xunit=uw[0]-max(8,height); right=edges[-1]
        if len(centers)>1 and centers[0]-(centers[1]-centers[0])/2>uw[2]:
            edges[0]=centers[0]-(centers[1]-centers[0])/2
        # Shaded section bands often preserve real column spans even though
        # the white body rows have no vertical rules. Reuse that evidence.
        unit_boxes=[r for r in rects if r[1]>cy and r[1]<cy+height*5
                    and r[0]<unit_center<r[2] and r[2]<centers[0]
                    and abs((r[0]+r[2])/2-unit_center)<height]
        if unit_boxes:
            ur=min(unit_boxes,key=lambda r:r[1])
            columns=[r for r in rects if abs(r[1]-ur[1])<1 and abs(r[3]-ur[3])<1
                     and any(r[0]<center<r[2] for center in centers)]
            columns=sorted(columns,key=lambda r:r[0])
            if len(columns)==len(centers) and all(r[0]<center<r[2] for r,center in zip(columns,centers)):
                xunit=ur[0];edges=[r[0] for r in columns]+[columns[-1][2]];right=edges[-1]
        # Only rules crossing the unit anchor can delimit this table. Decorative
        # rules on another half of a spread cannot extend its rows.
        ys=cluster([y for a,y,b in horizontal if a<=unit_center<=b and y>cy and b-a>15])
        ys=[y for y in ys if y<page.rect.height-15]
        if len(ys)>=3:
            typical=median([b-a for a,b in zip(ys,ys[1:]) if 3<b-a<60] or [height*1.8])
            end=1
            while end<len(ys) and ys[end]-ys[end-1]<=max(60,typical*3):end+=1
            ys=ys[:end]
            if ys[0]-cy>height*2:continue
        else:
            # Native label baselines provide independent row evidence. Stop on
            # a large gap or another header; footer text is outside this region.
            label_words=[w for w in words if xleft<=w[0]<xunit and w[2]<=xunit+1 and (w[1]+w[3])/2>cy+height*.7]
            baselines=cluster([(w[1]+w[3])/2 for w in label_words],max(1,height*.3))
            if len(baselines)<3:continue
            typical=median([b-a for a,b in zip(baselines,baselines[1:]) if height*.8<b-a<height*3] or [height*1.7])
            selected=[];prev=cy
            for y in baselines:
                if y-prev>typical*2.5:break
                selected.append(y);prev=y
            if len(selected)<3:continue
            ys=[(cy+selected[0])/2]+[(a+b)/2 for a,b in zip(selected,selected[1:])]+[selected[-1]+typical/2]
        if len(ys)<3:continue
        header_top=cy-height*.7; header_bottom=ys[0]
        headers=[Cell((a,header_top,b,header_bottom),' '.join(w[4] for w in g))
                 for a,b,g in zip(edges,edges[1:],groups)]
        rows=[];section=''
        for top,bottom in zip(ys,ys[1:]):
            label=norm(cell_text(words,(xleft,top,xunit,bottom)))
            unit=norm(cell_text(words,(xunit,top,edges[0],bottom)))
            band=[w for w in words if top<(w[1]+w[3])/2<bottom and edges[0]<=(w[0]+w[2])/2<right]
            if not label:continue
            if label.startswith(('»','›','▶')) and not unit and not band:
                section=label.lstrip('»›▶ ').strip();continue
            labels=([section] if section else [])+[label]
            values=[];ambiguous=False
            for a,b in zip(edges,edges[1:]):
                chosen=[w for w in band if a<=(w[0]+w[2])/2<b]
                crossing=[w for w in chosen if w[0]<a-1 or w[2]>b+1]
                if crossing:ambiguous=True
                safe=[w for w in chosen if w not in crossing]
                values.append(Cell((a,top,b,bottom),cell_text(safe,(a,top,b,bottom))))
            # Raw ambiguous text remains in the source-page coordinate audit.
            rows.append(LogicalRow(len(rows),(xleft,top,right,bottom),labels,unit,values,
                                   'header_coordinate_columns',('ambiguous_column_alignment',) if ambiguous else ()))
        if rows:tables.append(LogicalTable(page_number,(xleft,header_top,right,ys[-1]),headers,rows,'native_coordinate_columns'))
    return tables


def plumber_fallback(path, page_number: int, page, schema=None) -> list[LogicalTable]:
    """Ruled / native text fallback. Still uses real cell spans; never ffill."""
    import pdfplumber
    from .models import parse_header
    if schema is None:
        from .schema import Schema
        schema=Schema()
    words=page.get_text('words',sort=True)
    result=[]
    with pdfplumber.open(path) as doc:
        pp=doc.pages[page_number-1]
        for strategy in ('lines','text'):
            settings={'vertical_strategy':strategy,'horizontal_strategy':strategy}
            for t in pp.find_tables(settings):
                boxes=[tuple(b) for b in t.cells if b and b[2]-b[0]>4 and b[3]-b[1]>3]
                if len(boxes)<8: continue
                units=[b for b in boxes if schema.header_is(cell_text(words,b),'unit')]
                for uc in units:
                    dc=[b for b in boxes if b[0]<uc[0] and abs(b[1]-uc[1])<2 and schema.header_is(cell_text(words,b),'description')]
                    if not dc: continue
                    top,bottom=uc[1],uc[3]; left=dc[0][0]; data=uc[2]
                    headers=[Cell(b,cell_text(words,b)) for b in boxes if b[0]>=data-.8 and abs(b[1]-top)<2 and abs(b[3]-bottom)<2]
                    headers.sort(key=lambda c:c.bbox[0])
                    if not any(parse_header(c.text) for c in headers): continue
                    right=max(c.bbox[2] for c in headers)
                    urs=sorted([b for b in boxes if abs(b[0]-uc[0])<1 and abs(b[2]-data)<1 and b[1]>=bottom-.8],key=lambda b:b[1])
                    rows=[]
                    for i,ur in enumerate(urs):
                        y=(ur[1]+ur[3])/2
                        lb=sorted([b for b in boxes if b[0]>=left-.8 and b[2]<=uc[0]+.8 and b[1]<=y<=b[3]],key=lambda b:b[0])
                        labels=[norm(cell_text(words,b)) for b in lb if norm(cell_text(words,b))]
                        values=[Cell(b,cell_text(words,b)) for b in boxes if b[0]>=data-.8 and b[2]<=right+.8 and b[1]<=y<=b[3]]
                        values.sort(key=lambda c:c.bbox[0])
                        if labels: rows.append(LogicalRow(i,(left,ur[1],right,ur[3]),labels,norm(cell_text(words,ur)),values))
                    if rows: result.append(LogicalTable(page_number,tuple(t.bbox),headers,rows,'native_'+strategy+'_fallback'))
            if result: break
    return result


def recover_trailing_rows(page, table: LogicalTable, schema) -> int:
    """Conservatively recover an omitted last row from native text immediately
    below the geometric table boundary.

    This is not a forward-fill and never extrapolates a model's value. It adds
    a row only when its *own* text is in the expected label/value regions and
    the label matches a configured parameter. It reconstructs merged value
    spans from real vertical ruling lines crossing that row. Rows are marked
    for review because their bottom border was not captured.
    """
    if not table.rows or not table.headers: return 0
    words=page.get_text('words',sort=True)
    if not words: return 0
    last=table.rows[-1]
    bottom=last.bbox[3]
    row_heights=sorted(r.bbox[3]-r.bbox[1] for r in table.rows if r.bbox[3]>r.bbox[1])
    typical=row_heights[len(row_heights)//2] if row_heights else 18
    if typical>60: typical=20
    xleft=table.bbox[0]
    xdata=table.headers[0].bbox[0]
    right=table.bbox[2]
    limit=min(bottom+min(typical*1.85,34),page.rect.height-12)
    trailing=[w for w in words if bottom+0.35 < (w[1]+w[3])/2 < limit and xleft<= (w[0]+w[2])/2 < right]
    if not trailing: return 0
    # First actual baseline only: do not turn footer notes into a continuation.
    cy=min((w[1]+w[3])/2 for w in trailing)
    band=[w for w in trailing if abs((w[1]+w[3])/2-cy)<3.1]
    label_boundary=xdata-50
    label_words=[w for w in band if (w[0]+w[2])/2 < label_boundary]
    value_words=[w for w in band if (w[0]+w[2])/2 >= xdata]
    if not label_words or not value_words: return 0
    label_txt=norm(' '.join(w[4] for w in sorted(label_words,key=lambda w:w[0])))
    if not label_txt: return 0
    # The leftmost group label is typically in the first 60 pt, then a field
    # name begins at xleft+60..90. Retain the original label text either way.
    group=[w for w in label_words if (w[0]+w[2])/2<xleft+65]
    tail=[w for w in label_words if xleft+65<=(w[0]+w[2])/2<label_boundary]
    labels=([norm(' '.join(w[4] for w in sorted(group,key=lambda w:w[0]))),
             norm(' '.join(w[4] for w in sorted(tail,key=lambda w:w[0])))]) if group and tail else [label_txt]
    rule=schema.match(labels,'')
    if not rule: return 0
    # Infer only the row band; boundaries of model cells still require actual
    # vertical ruling lines through the recovered row. No interpolated columns.
    top=bottom+0.03
    end=min(bottom+typical,cy+max((w[3]-w[1])/2 for w in band)+2)
    if end<=cy: end=cy+4
    _,verticals,_=primitives(page)
    xedges=cluster([xdata,right]+[x for x,y0,y1 in verticals
                   if xdata+.8<x<right-.8 and y0+0.15<cy<y1-0.15])
    cells=[Cell((a,top,b,end),cell_text(words,(a,top,b,end)))
           for a,b in zip(xedges,xedges[1:]) if b-a>3]
    if not any(c.text.strip() for c in cells): return 0
    unit_words=[w for w in band if label_boundary <= (w[0]+w[2])/2 < xdata]
    unit=norm(' '.join(w[4] for w in sorted(unit_words,key=lambda w:w[0])))
    # Validate the final interpretation against the actual unit text too.
    if not schema.match(labels,unit): return 0
    new=LogicalRow(last.row_index+1,(xleft,top,right,end),labels,unit,cells,
                   recovery='below_geometric_table_edge')
    table.rows.append(new)
    table.bbox=(table.bbox[0],table.bbox[1],table.bbox[2],end)
    return 1


def candidate_shared_text_span(row: LogicalRow, headers: list[Cell], parameter: str,
                               allowed: list[str], min_coverage: float = .75):
    """Review-only recovery for a *text* value split from one trailing blank.

    Never infer numeric specifications and never interpolate an internal gap.
    The source table remains immutable; the caller must retain the original
    cell coordinates and mark derived applicability as unverified.
    """
    if parameter not in allowed or len(headers)<4 or len(row.values)!=2:
        return None
    first,last=row.values
    if not norm(first.text) or norm(last.text): return None
    xmin=headers[0].bbox[0]
    xmax=headers[-1].bbox[2]
    total=xmax-xmin
    if total<=0 or abs(first.bbox[0]-xmin)>1.3 or abs(last.bbox[2]-xmax)>1.3:
        return None
    if abs(first.bbox[2]-last.bbox[0])>1.3: return None
    if (first.bbox[2]-first.bbox[0])/total < min_coverage: return None
    # The content is from the existing first cell; only applicability to the
    # far-right header is inferred. Caller must include the warning.
    return Cell((first.bbox[0],first.bbox[1],last.bbox[2],first.bbox[3]),first.text)
