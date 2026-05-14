from __future__ import annotations

from app.agents.renderer import render_cart_confirmation, render_intent_reply
from app.schemas.message import CandidateItem, ParsedIntent
from packages.providers.interface import QuoteResult


def build_confirmation(
    intent: ParsedIntent,
    candidates: list[CandidateItem],
    quote: QuoteResult | None,
    conv_ctx: dict | None = None,
) -> str:
    if quote is None or not candidates:
        return render_intent_reply(intent, conv_ctx=conv_ctx)

    if not quote.line_items:
        item_text = ", ".join(item.display_name for item in candidates)
        return (
            f"Sare, {item_text} cart lo pettanu. Total ₹{quote.total_inr}. "
            f"Delivery approx {quote.estimated_delivery_min}-{quote.estimated_delivery_max} minutes. "
            "Confirm chey-yana?"
        )

    language = (conv_ctx or {}).get("context", {}).get("language", "te-IN") if conv_ctx else "te-IN"
    address_label = (conv_ctx or {}).get("context", {}).get("delivery_address_label")
    return render_cart_confirmation(
        quote,
        address_label=address_label,
        language=language,
        candidates=candidates,
    )
