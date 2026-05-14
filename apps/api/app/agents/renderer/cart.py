from __future__ import annotations

from app.schemas.message import CandidateItem
from packages.providers.interface import QuoteResult

from .templates._registry import templates_for


def render_cart_lines(quote: QuoteResult, language: str = "te-IN") -> list[str]:
    t = templates_for(language)
    lines: list[str] = []
    for li in quote.line_items:
        if li.in_stock:
            lines.append(t.cart_line(li.display_name, li.pack_size_label, li.qty, li.line_total_inr))
        else:
            lines.append(t.cart_oos_line(li.display_name, li.pack_size_label, li.qty))
            for substitute in li.substitutes:
                lines.append(
                    t.substitute_line(
                        substitute.display_name,
                        substitute.pack_size_label,
                        substitute.price_inr,
                    )
                )
        if (
            li.requested_size_label
            and li.pack_size_label
            and li.requested_size_label.strip().lower() != li.pack_size_label.strip().lower()
        ):
            lines.append(t.cart_size_adjustment(li.requested_size_label, li.pack_size_label))
    return lines


def render_cart_confirmation(
    quote: QuoteResult,
    address_label: str | None = None,
    language: str = "te-IN",
    candidates: list[CandidateItem] | None = None,
) -> str:
    # Backward-compatible fallback when provider hasn't populated line_items.
    if not quote.line_items:
        item_text = ", ".join(c.display_name for c in (candidates or []))
        item_prefix = f"{item_text} " if item_text else ""
        return (
            f"Sare, {item_prefix}cart lo pettanu. Total ₹{quote.total_inr}. "
            f"Delivery approx {quote.estimated_delivery_min}-{quote.estimated_delivery_max} minutes. "
            "Confirm chey-yana?"
        )

    t = templates_for(language)
    parts = [t.cart_header()]
    parts.extend(render_cart_lines(quote, language))
    has_in_stock_items = any(li.in_stock for li in quote.line_items)
    if not has_in_stock_items:
        return "\n".join(parts)
    if quote.applied_offers:
        offers_block = t.offers_applied(quote.applied_offers)
        if offers_block:
            parts.append(offers_block)
    parts.append(t.cart_summary(quote.subtotal_inr, quote.delivery_fee_inr, quote.total_inr))
    parts.append(t.cart_eta(quote.estimated_delivery_min, quote.estimated_delivery_max, address_label))
    parts.append(t.cart_confirm_prompt())
    return "\n".join(parts)
