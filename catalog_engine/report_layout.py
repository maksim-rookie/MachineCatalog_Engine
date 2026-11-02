"""Human-readable, non-alphabetical group order and safe family summary views."""
from __future__ import annotations
from collections import defaultdict

MISSING_MARKERS=('[НЕ ИЗВЛЕЧЕНО','[ПУСТАЯ ЯЧЕЙКА')


def ordered_columns(columns, schema):
    """Group order is configured; within groups use configured parameter order.

    Unlisted parameters retain input order (usually the order in the source
    technical tables), never get alphabetically promoted before configured ones.
    """
    groups=schema.config.get('parameter_groups',[])
    overrides=schema.config.get('parameter_order',{})
    by_group={g:i for i,g in enumerate(groups)}
    raw_order={col:i for i,col in enumerate(columns)}
    def sort_key(col):
        category,_,parameter=col[1:].partition('] ')
        params=overrides.get(category,[])
        return (by_group.get(category,len(groups)),
                0 if parameter in params else 1,
                params.index(parameter) if parameter in params else raw_order[col],
                raw_order[col])
    return sorted(dict.fromkeys(columns),key=sort_key)


def aggregate_families(configuration_rows, identity_keys, data_columns):
    """One base-model row with explicitly qualified divergent cell values.

    No first/last value is chosen when configurations differ; detailed physical
    machine records are preserved in ALL_CONFIGURATIONS_wide.csv.
    """
    grouped=defaultdict(list)
    for row in configuration_rows:
        key=(row['source_file'],row['base_model'],row.get('generation',''),
             row.get('machine_class',''),row.get('machine_type',''))
        grouped[key].append(row)
    families=[];clean_families=[];metadata=[]
    for key,members in grouped.items():
        first=members[0]
        seen_physical=list(dict.fromkeys(r['model'] for r in members))
        seen_configs=list(dict.fromkeys(f"{r['model']} [{r.get('model_variant','standard')}] / {r['configuration']}" for r in members))
        is_family=len(members)>1
        base={k:first.get(k,'') for k in identity_keys}
        base['model']=key[1];base['base_model']=key[1]
        base['model_variant']='multiple variants' if len(seen_physical)>1 else first.get('model_variant','standard')
        values=dict(base);clean=dict(base)
        for col in data_columns:
            distinct=defaultdict(list)
            missing=[]
            for member in members:
                descriptor=f"{member['model']} / {member.get('model_variant','standard')} / {member['configuration']}"
                val=member.get(col,'')
                if not val or val.startswith(MISSING_MARKERS):
                    missing.append(descriptor)
                else:
                    distinct[val].append(descriptor)
            if not distinct:
                values[col]='[НЕТ ПОДТВЕРЖДЁННОГО ЗНАЧЕНИЯ ДЛЯ БАЗОВОЙ МОДЕЛИ]' if is_family else first.get(col,'')
                clean[col]=''
                continue
            if len(distinct)==1 and not missing:
                single=next(iter(distinct))
                values[col]=single
                # Base family clean scalars require agreement across all
                # physical variants AND configurations.
                clean[col]=single if not is_family else single if '[ПРОВЕРИТЬ:' not in single and '[ОПЦИЯ]' not in single and '[condition:' not in single else ''
                continue
            # Never output competing configurations as an unlabeled list.
            vals=[]
            for value,scopes in distinct.items():
                vals.append(f'{value} [исполнения: {"; ".join(scopes)}]')
            values[col]=' || '.join(vals)
            clean[col]=''
        families.append(values)
        clean_families.append(clean)
        metadata.append({'source_file':key[0],'manufacturer':first.get('manufacturer',''),
                         'base_model':key[1],'machine_class':key[3],'machine_type':key[4],
                         'physical_models':seen_physical,
                         'physical_models_count':len(seen_physical),
                         'configurations':seen_configs,'configuration_rows':len(members),
                         'merge_status':'HIERARCHICAL_GROUP_WITH_SEPARATE_VARIANTS' if is_family else 'SINGLE_DESIGNATION',
                         'human_verified':False})
    return families,clean_families,metadata
