"""Taxonomy of broad classes and specific equipment types, backed by cover evidence."""
from __future__ import annotations
import re
from .geometry import norm


def document_identity(pages, schema):
    """Accept an explicit machine-type statement on the cover only.

    Mixed catalogues such as KAIBO have a manufacturer title on the cover and
    therefore rely on per-table headings. Marketing text on later pages may
    never impose a class on the whole catalogue.
    """
    if not pages:return None
    text=norm(pages[0].get('text',''))[:1600]
    for rule in schema.config.get('cover_machine_types',[]):
        if re.search(rule['pattern'],text,re.I):
            return rule['class'],rule['type'],'explicit_document_cover'
    return None


def category_tree(schema):
    out={}
    for entry in schema.config.get('machine_taxonomy',[]):
        out[entry['class']]={'label_ru':entry.get('label_ru',''),
                              'types':entry.get('types',[])}
    return out
