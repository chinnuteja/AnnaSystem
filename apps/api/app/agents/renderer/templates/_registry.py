from __future__ import annotations

from .en_in import EnInTemplates
from .hi_in import HiInTemplates
from .te_in import TeInTemplates

_MAP = {"te-IN": TeInTemplates, "en-IN": EnInTemplates, "hi-IN": HiInTemplates}


def templates_for(language: str):
    cls = _MAP.get(language, EnInTemplates)
    return cls()
