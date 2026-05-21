"""Message Parser — thin compatibility shim.

The brain (app.agents.brain) is now the primary NLU engine. This module
provides backward-compatible exports for dev routes and tests that still
call parse_text_message directly.
"""

from __future__ import annotations

import logging

from app.agents.brain import BrainAction, decide, ParsedItem as BrainParsedItem
from app.schemas.message import ParsedIntent, ParsedItem

logger = logging.getLogger("foodleaf.parser")

# Keep CORRECTION_PHRASES for backward compat (tests, semantic_router)
CORRECTION_PHRASES = (
    "adem ledu", "not that", "wrong", "new order", "take new order",
    "different", "change that", "cancel that", "malli", "inkoti", "vere",
    "actually", "i sent", "i said", "i meant", "i wanted", "no i",
    "not this", "wrong one", "not that one", "other one", "the other",
    "this is wrong",
)

# Empty stubs — brain handles all detection now
DISCOVERY_WORDS = ()
CHITCHAT_WORDS = ()
STRONG_TRACK_PHRASES = ()
VAGUE_SHOP_PHRASES = ()
DINEOUT_SIGNALS = ()
CONFIRM_WORDS = ()
CHECKOUT_WORDS = ()


def _brain_action_to_parsed_intent(action: BrainAction, raw_text: str, language: str) -> ParsedIntent:
    """Convert a BrainAction to a ParsedIntent for backward compat."""
    action_map = {
        "greet": "CHITCHAT", "select_option": "ORDER", "more_options": "DISCOVER",
        "order_items": "ORDER", "discover": "DISCOVER", "confirm": "CONFIRM",
        "cancel": "CANCEL", "correct": "ORDER", "track_order": "TRACK",
        "ask_cart": "ORDER", "clear_cart": "CANCEL", "update_address": "ORDER",
        "chitchat": "CHITCHAT", "unclear": "UNCLEAR",
    }
    goal_map = {
        "greet": "chat", "chitchat": "chat", "unclear": "chat",
        "order_items": "shop", "confirm": "shop", "cancel": "shop",
        "discover": "discover", "track_order": "track",
    }
    parsed_action = action_map.get(action.action, "CHITCHAT")
    goal = goal_map.get(action.action, "shop")
    needs_clarification = action.action in ("unclear",) and parsed_action not in ("CONFIRM", "CANCEL")

    items = []
    for bi in action.items:
        items.append(ParsedItem(text=bi.text, quantity=bi.quantity, unit=bi.unit, brand_hint=bi.brand_hint))

    lang_detected = {"te": "te-IN", "en": "en-IN", "te-en": "te-IN"}.get(action.detected_language, language)

    return ParsedIntent(
        action=parsed_action, goal=goal, raw_text=raw_text, items=items,
        needs_clarification=needs_clarification,
        clarification_question=action.clarification_question,
        domain_hint=action.domain_hint, language_detected=lang_detected,
    )


async def parse_text_message(
    text: str, language: str = "en-IN", conversation_context: dict | None = None,
) -> ParsedIntent:
    """Parse a text message using the brain. Returns ParsedIntent for backward compat."""
    brain_action = await decide(
        text=text, conversation_history=[], current_state=conversation_context, language=language,
    )
    return _brain_action_to_parsed_intent(brain_action, text, language)


def _has_substantive_item(items: list, raw_text: str) -> bool:
    filler = {"something", "order", "item", "food", "cheyyali", "kavali", "please", "just"}
    for item in items:
        text = (item.text if hasattr(item, 'text') else str(item)).strip().lower()
        if text and text not in filler and len(text) >= 2:
            return True
    return False


def _try_trivial_parse(text: str, language: str = "te-IN") -> ParsedIntent | None:
    return None  # brain handles all parsing


def _deterministic_parse(text: str, language: str = "te-IN") -> ParsedIntent | None:
    return None  # brain handles all parsing


def post_process_intent(intent: ParsedIntent, raw_text: str) -> ParsedIntent:
    return intent  # brain handles all post-processing
