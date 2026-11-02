"""Additional grouped views and explicit, non-destructive column exclusions."""
from .logical_fields import CUT,CUT_AXES,CUT_KEYS,TABLE,TABLE_KEYS,TABLE_COMPONENTS,REMOVED,merge_cutting_feed,merge_table_size
from .storage import write_csv,atomic_json

CUT_COLUMN='[Feedrate] '+CUT
TABLE_COLUMN='[Table] '+TABLE

def parameter(column):return column.split('] ',1)[-1].split('__',1)[0]


def prepare(fields):
    cut=any(parameter(c) in CUT_KEYS|set(CUT_AXES) for c in fields)
    table=any(parameter(c) in set(TABLE_KEYS)|set(TABLE_COMPONENTS)|{TABLE} for c in fields)
    removed=REMOVED|set(CUT_AXES)|(CUT_KEYS-{CUT})|set(TABLE_KEYS)|set(TABLE_COMPONENTS)
    fields=[c for c in fields if parameter(c) not in removed]
    for enabled,column in [(cut,CUT_COLUMN),(table,TABLE_COLUMN)]:
        if enabled:
            for c in [column,column+'__note']:
                if c not in fields:fields.append(c)
    return fields,cut,table


def fill(row,fields,facts,cut,table,status_table=False,missing=None):
    for enabled,column,merge,keys in [(cut,CUT_COLUMN,merge_cutting_feed,CUT_KEYS|set(CUT_AXES)),
                                      (table,TABLE_COLUMN,merge_table_size,set(TABLE_KEYS)|set(TABLE_COMPONENTS)|{TABLE})]:
        if not enabled:continue
        value,note=merge(facts,missing) if column==CUT_COLUMN else merge(facts);state='REVIEW_REQUIRED' if '!-' in value else 'NOT_SPECIFIED' if value=='-' else 'MERGED_UNVERIFIED'
        row[column]=state if status_table else value;row[column+'__note']=note
        if column+'__source_value' in fields:
            selected=[f for f in facts if f.get('parameter') in keys]
            row[column+'__unit']='mm/min' if column==CUT_COLUMN else 'mm'
            row[column+'__source_unit']=' || '.join(dict.fromkeys(str(f.get('unit_source','')) for f in selected))
            row[column+'__source_value']=' || '.join(str(f.get('value_raw',f.get('value',''))) for f in selected)
            row[column+'__source_ref']=' || '.join(f"{f.get('source_file','')}:page={f.get('source_page','')}:fact={f.get('fact_id','')}" for f in selected)
            row[column+'__status']=state


def archive_hidden(output,documents):
    # Source extraction and the detailed normalized/derived audits are retained.
    hidden=[f for d in documents for f in d.get('facts',[]) if f.get('parameter') in REMOVED]
    atomic_json(output/'HIDDEN_PARAMETERS_ARCHIVE.json',hidden)
    atomic_json(output/'PRESENTATION_POLICY.json',{'hidden_parameters':sorted(REMOVED),
        'archived_source_facts':len(hidden),'data_deleted':False,'threshold_applied':False,
        'note':'Only the requested columns are hidden. Raw facts and normalization/derived audits remain available.'})
