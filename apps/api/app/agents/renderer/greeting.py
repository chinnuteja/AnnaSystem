from __future__ import annotations

from .templates._registry import templates_for


def render_welcome(
    language: str = "te-IN",
    grocery_example: str = "",
    food_example: str = "",
) -> str:
    t = templates_for(language)
    cap = t.capability_line(grocery_example, food_example)
    return t.welcome(cap)
