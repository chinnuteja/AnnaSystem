from __future__ import annotations

from app.schemas.message import ParsedIntent

from .chitchat import render_chitchat
from .discovery import render_discovery_ack
from .tracking import render_tracking_prompt


def render_intent_reply(intent: ParsedIntent, conv_ctx: dict | None = None) -> str:
    if intent.action == "CHITCHAT":
        return render_chitchat(conv_ctx)

    if intent.action == "TRACK":
        return render_tracking_prompt()

    if intent.action == "DISCOVER":
        return render_discovery_ack()

    if intent.needs_clarification:
        return intent.clarification_question or (
            "Sare. Groceries (atta, milk) / food delivery (biryani) / dineout — edhi kavali?"
        )

    if intent.goal in {"shop", "discover"} and (not intent.items):
        return (
            "Sare. Emi kavali? Groceries (atta, milk) leda food delivery (biryani) "
            "leda dineout? Okka line lo cheppandi."
        )

    return (
        "Sorry, aa item dorakaledu. Item peru konchem vere laga cheppandi "
        "(example: 'aashirvaad atta', '2 milk') leda 'dinner options' ani cheppandi."
    )
