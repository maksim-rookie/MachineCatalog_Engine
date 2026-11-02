"""Explicit, auditable model-family identity; never infer a family from a numeric prefix alone."""
from __future__ import annotations
import re
from .geometry import norm


def resolve_family(model: str, schema) -> tuple[str,str,str]:
    """Return (base_model, modification, rule_id).

    An unconstrained digit or prefix match is deliberately forbidden: DVF
    8000T and DVF 8000T/50, for example, stay separate without a reviewed rule.
    New families are configured in JSON; no PDF-specific parser is needed.
    """
    for item in schema.config.get('model_family_rules',[]):
        match=re.fullmatch(item['pattern'],norm(model),re.I)
        if match:
            groups={k:(v or '') for k,v in match.groupdict().items()}
            try:
                base=norm(item['base_model'].format(**groups))
                variant=norm(item.get('model_variant','').format(**groups))
            except (KeyError,ValueError) as exc:
                raise ValueError(f'Invalid model family rule {item.get("id", "?")}: {exc}') from exc
            if not base:raise ValueError('An empty family is unsafe: '+repr(item))
            return base, variant or 'standard', item.get('id','configured_family_rule')
    return model,'standard','exact_model_identity'
