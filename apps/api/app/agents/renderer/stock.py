from __future__ import annotations

from packages.providers.interface import SkuPreview

from .templates._registry import templates_for


def render_substitutes(
    *,
    requested_text: str,
    substitutes: list[SkuPreview],
    language: str = "te-IN",
) -> str:
    t = templates_for(language)
    if not substitutes:
        if language == "en-IN":
            return f"Sorry, I couldn't find {requested_text}. Please try another item name."
        return f"Sorry, {requested_text} dorakaledu. Item peru konchem vere laga cheppandi."

    if language == "en-IN":
        lines = [f"I couldn't find an exact match for {requested_text}.", t.substitutes_header()]
    else:
        lines = [f"{requested_text} exact ga dorakaledu.", t.substitutes_header()]
    for substitute in substitutes:
        lines.append(t.substitute_line(substitute.display_name, substitute.pack_size_label, substitute.price_inr).strip())
    return "\n".join(lines)


def render_numbered_options(
    *,
    requested_text: str,
    options: list[SkuPreview],
    language: str = "te-IN",
) -> str:
    if not options:
        return render_substitutes(requested_text=requested_text, substitutes=[], language=language)

    if language == "en-IN":
        lines = [f"{requested_text} lo these options are available:"]
        prompt = "Which one should I add? Reply with number, brand, or size."
    else:
        lines = [f"{requested_text} lo ee options unnayi:"]
        prompt = "Yedi add cheyyali? Number, brand, leda size cheppandi."

    for idx, option in enumerate(options, start=1):
        size = f" {option.pack_size_label}" if option.pack_size_label else ""
        lines.append(f"{idx}. {option.display_name}{size} — ₹{option.price_inr}")
    lines.append(prompt)
    return "\n".join(lines)
