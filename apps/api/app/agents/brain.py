"""FoodLeaf Brain — LLM-native dialogue engine with conversation history.

Replaces the old message_parser + guardrails + pipeline if-chains with a single
context-aware LLM call that emits a structured BrainAction.

Primary model: Gemini 3 Flash (fast, multilingual, strong reasoning).
Fallback:     Azure OpenAI GPT-4o-mini (existing deployment).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.agents.brain_prompts import get_prompt_builder

logger = logging.getLogger("foodleaf.brain")

# Confidence gate: override low-confidence actions to unclear
_CONFIDENCE_THRESHOLD = float(os.getenv("BRAIN_CONFIDENCE_THRESHOLD", "0.5"))

# Response cache settings
_CACHE_TTL_SEC = 5 * 60  # 5 minutes
_CACHE_SKIP_ACTIONS = frozenset({"confirm", "cancel", "update_address", "approve", "reject_approval"})  # side-effect actions must always hit LLM


def _cache_fingerprint(state: dict | None) -> str:
    """Build a structural fingerprint of the conversation state for cache keying."""
    if state is None:
        return "idle"
    ctx = state.get("context") or {}
    parts = [
        state.get("state", "?"),
        ctx.get("flow", ""),
        str(len(ctx.get("visible_options", []))),
        str(len(ctx.get("options", []))),
        str(len((ctx.get("resolved_cart") or {}).get("items", []))),
    ]
    return "|".join(parts)


def _cache_key(text: str, state_fp: str) -> str:
    """Build a Redis cache key from text + state fingerprint."""
    raw = f"{text}::{state_fp}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"brain_cache:{digest}"

# ---------------------------------------------------------------------------
# BrainAction — the single structured output from the brain
# ---------------------------------------------------------------------------

class ParsedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    quantity: int | None = None
    unit: str | None = None
    brand_hint: str | None = None


class BrainAction(BaseModel):
    """The single structured output from the brain."""
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "greet",           # First-contact or explicit greeting
        "select_option",   # User selected from numbered options (discovery or catalog)
        "more_options",    # User wants more options
        "order_items",     # User named specific items to buy
        "discover",        # User wants recommendations/options
        "confirm",         # User confirmed pending order
        "cancel",          # User cancelled
        "correct",         # User is correcting a bot mistake
        "track_order",     # User wants order status
        "ask_cart",        # User asking what's in their cart
        "clear_cart",      # User wants to empty cart
        "update_address",  # User providing delivery address
        "chitchat",        # Small talk, questions, non-commercial
        "unclear",         # Can't determine intent, need clarification
        "approve",         # Payer approves a pending payment request
        "reject_approval", # Payer rejects a pending payment request
    ]

    # For select_option: which option (0-indexed), or name of the option
    selected_index: int | None = None
    selected_name: str | None = None

    # For order_items: extracted items
    items: list[ParsedItem] = Field(default_factory=list)

    # For discover: query/preferences
    discovery_query: str | None = None
    domain_hint: Literal["grocery", "food_delivery", "dineout", "any"] = "any"

    # For update_address
    address_text: str | None = None

    # For conversational actions (greet, chitchat, correct, unclear):
    # LLM generates the reply text directly in the user's language
    reply_text: str | None = None

    # For unclear: clarification question (also LLM-generated)
    clarification_question: str | None = None

    # Language the user is speaking
    detected_language: Literal["te", "en", "te-en", "hi", "hi-en"] = "te-en"

    # Confidence score
    confidence: float = 0.8

    # Reasoning trace for debugging
    reasoning: str | None = None

    # For approve/reject_approval: which payment request (family cart id)
    approval_target: str | None = None


# ---------------------------------------------------------------------------
# Conversation History Helpers
# ---------------------------------------------------------------------------

_HISTORY_MAX_TURNS = 20
_HISTORY_TTL_SEC = 24 * 60 * 60  # 24 hours


def _history_key(user_id: str) -> str:
    return f"conv_history:{user_id}"


async def save_turn(redis: Any, user_id: str, role: str, text: str) -> None:
    """Append a conversation turn to Redis history."""
    key = _history_key(user_id)
    entry = json.dumps({"role": role, "text": text, "ts": time.time()})
    pipe = redis.pipeline()
    pipe.rpush(key, entry)
    pipe.ltrim(key, -_HISTORY_MAX_TURNS, -1)
    pipe.expire(key, _HISTORY_TTL_SEC)
    await pipe.execute()


async def load_history(redis: Any, user_id: str, max_turns: int = 8) -> list[dict]:
    """Load recent conversation turns from Redis."""
    key = _history_key(user_id)
    raw_entries = await redis.lrange(key, -max_turns, -1)
    history: list[dict] = []
    for raw in raw_entries:
        try:
            history.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return history


# ---------------------------------------------------------------------------
# Context Builder — assembles everything the brain needs to see
# ---------------------------------------------------------------------------

def _build_state_description(current: dict | None) -> str:
    """Describe the current conversation state in natural language for the LLM."""
    if current is None:
        return "No active session. User is idle — this is likely a first contact or fresh start."

    state = current.get("state", "IDLE")
    context = current.get("context") or {}
    flow = context.get("flow", "")

    parts = [f"Current state: {state}"]

    if flow:
        flow_descriptions = {
            "awaiting_assistant": "Bot asked the user what they want (groceries/food/dineout). Waiting for their answer.",
            "awaiting_location": "Bot asked for the user's location. Waiting for them to share it.",
            "discovery": "Bot showed discovery options (restaurants/dineout/grocery). User needs to pick one or ask for more.",
            "discovery_selected": "User selected an option. Bot showed details and is waiting for confirmation.",
            "option_selection": "Bot showed numbered catalog options (e.g., different brands of atta). User needs to pick one.",
            "substitute_selection": "Bot showed substitute options for an out-of-stock item.",
            "pending_order": "User has a pending cart/order waiting for confirmation.",
        }
        parts.append(f"Flow: {flow} — {flow_descriptions.get(flow, flow)}")

    # Visible options
    visible = context.get("visible_options", [])
    if visible:
        parts.append("Visible options shown to user:")
        for i, opt in enumerate(visible):
            name = opt.get("title") or opt.get("name", "?")
            source = opt.get("source", "")
            price = opt.get("estimated_total_inr")
            price_str = f" ~₹{price}" if price else ""
            parts.append(f"  [{i}] {name} ({source}){price_str}")

    # Catalog options (option_selection flow)
    catalog_opts = context.get("options", [])
    if catalog_opts:
        parts.append("Catalog options shown to user:")
        for i, opt in enumerate(catalog_opts):
            name = opt.get("display_name", "?")
            brand = opt.get("brand", "")
            size = opt.get("pack_size_label", "")
            parts.append(f"  [{i}] {name} ({brand}, {size})")

    # Cart contents
    cart = context.get("resolved_cart", {})
    if cart and isinstance(cart, dict):
        items = cart.get("items", [])
        if items:
            parts.append("Current cart:")
            for item in items:
                name = item.get("display_name", "?")
                qty = item.get("quantity", 1)
                price = item.get("price_inr", "?")
                parts.append(f"  • {name} × {qty} — ₹{price}")
            total = cart.get("quote_total_inr")
            if total:
                parts.append(f"  Cart total: ₹{total}")

    # Pending confirmation text
    conf_text = context.get("confirmation_text")
    if conf_text:
        parts.append(f"Last bot message to user: {conf_text[:200]}")

    # Suggested domain
    domain = context.get("suggested_domain")
    if domain and domain not in ("any", "unknown"):
        parts.append(f"Locked domain: {domain}")

    return "\n".join(parts)


def _build_history_block(history: list[dict]) -> str:
    """Format conversation history for the prompt."""
    if not history:
        return "No previous conversation."
    lines = []
    for turn in history:
        role = turn.get("role", "?")
        text = turn.get("text", "")
        lines.append(f"{'User' if role == 'user' else 'Bot'}: {text}")
    return "\n".join(lines)


def _build_system_prompt(
    state_description: str,
    history_block: str,
    user_language: str = "te-IN",
    *,
    family_context: dict | None = None,
    occasion_hint: str | None = None,
) -> str:
    """Build the full system prompt using the active versioned prompt."""
    builder, version = get_prompt_builder()
    # Only v3_anna prompt accepts family_context and occasion_hint kwargs
    kwargs: dict = {}
    if version == "v3_anna":
        if family_context is not None:
            kwargs["family_context"] = family_context
        if occasion_hint is not None:
            kwargs["occasion_hint"] = occasion_hint
    return builder(state_description, history_block, user_language, **kwargs)


# ---------------------------------------------------------------------------
# LLM Callers
# ---------------------------------------------------------------------------

_BRAIN_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "greet", "select_option", "more_options", "order_items",
                "discover", "confirm", "cancel", "correct", "track_order",
                "ask_cart", "clear_cart", "update_address", "chitchat", "unclear",
                "approve", "reject_approval",
            ],
        },
        "selected_index": {"type": ["integer", "null"]},
        "selected_name": {"type": ["string", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "quantity": {"type": ["integer", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "brand_hint": {"type": ["string", "null"]},
                },
                "required": ["text"],
            },
        },
        "discovery_query": {"type": ["string", "null"]},
        "domain_hint": {"type": "string", "enum": ["grocery", "food_delivery", "dineout", "any"]},
        "address_text": {"type": ["string", "null"]},
        "reply_text": {"type": ["string", "null"]},
        "clarification_question": {"type": ["string", "null"]},
        "detected_language": {"type": "string", "enum": ["te", "en", "te-en", "hi", "hi-en"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": ["string", "null"]},
        "approval_target": {"type": ["string", "null"]},
    },
    "required": ["action", "detected_language", "confidence"],
}


async def _call_gemini(system_prompt: str, user_message: str) -> BrainAction | None:
    """Call Gemini 3 Flash with structured JSON output."""
    if not settings.gemini_api_key:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)

        def _sync_call():
            return client.models.generate_content(
                model=settings.gemini_model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=_BRAIN_ACTION_SCHEMA,
                    temperature=0.1,
                    max_output_tokens=1024,
                ),
            )

        response = await asyncio.to_thread(_sync_call)

        if not response.text:
            logger.warning("Gemini returned empty response")
            return None

        data = json.loads(response.text)
        action = BrainAction.model_validate(data)
        logger.info(
            "Gemini brain: action=%s confidence=%.2f reasoning=%s",
            action.action, action.confidence, (action.reasoning or "")[:100],
        )
        return action

    except Exception as e:
        logger.warning("Gemini brain call failed: %s", e)
        return None


async def _call_azure_openai(system_prompt: str, user_message: str, language: str = "te-IN") -> BrainAction:
    """Call Azure OpenAI as fallback with structured output."""
    from openai import AsyncAzureOpenAI

    client = AsyncAzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )

    # Build a simpler schema for Azure OpenAI structured output
    _nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    _nullable_int = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    azure_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "BrainAction",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "greet", "select_option", "more_options", "order_items",
                            "discover", "confirm", "cancel", "correct", "track_order",
                            "ask_cart", "clear_cart", "update_address", "chitchat", "unclear",
                            "approve", "reject_approval",
                        ],
                    },
                    "selected_index": _nullable_int,
                    "selected_name": _nullable_str,
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "quantity": _nullable_int,
                                "unit": _nullable_str,
                                "brand_hint": _nullable_str,
                            },
                            "required": ["text", "quantity", "unit", "brand_hint"],
                            "additionalProperties": False,
                        },
                    },
                    "discovery_query": _nullable_str,
                    "domain_hint": {"type": "string", "enum": ["grocery", "food_delivery", "dineout", "any"]},
                    "address_text": _nullable_str,
                    "reply_text": _nullable_str,
                    "clarification_question": _nullable_str,
                    "detected_language": {"type": "string", "enum": ["te", "en", "te-en", "hi", "hi-en"]},
                    "confidence": {"type": "number"},
                    "reasoning": _nullable_str,
                    "approval_target": _nullable_str,
                },
                "required": [
                    "action", "selected_index", "selected_name", "items",
                    "discovery_query", "domain_hint", "address_text",
                    "reply_text", "clarification_question", "detected_language",
                    "confidence", "reasoning", "approval_target",
                ],
                "additionalProperties": False,
            },
        },
    }

    try:
        completion = await client.beta.chat.completions.parse(
            model=settings.azure_openai_deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format=azure_schema,
            timeout=5.0,
        )
        parsed = completion.choices[0].message.parsed
        if parsed:
            action = BrainAction.model_validate(parsed)
            logger.info(
                "Azure brain: action=%s confidence=%.2f reasoning=%s",
                action.action, action.confidence, (action.reasoning or "")[:100],
            )
            return action
    except Exception as e:
        logger.warning("Azure OpenAI brain call failed: %s", e)

    # Last resort: return unclear
    lang = language[:2] if language else "hi-en"
    if lang == "hi":
        return BrainAction(
            action="unclear",
            clarification_question="Kya chahiye? Grocery, food delivery, ya kuch aur?",
            reply_text="Samajh nahi aaya, dobara bataiye?",
            detected_language="hi-en",
            confidence=0.3,
            reasoning="Both LLM calls failed",
        )
    return BrainAction(
        action="unclear",
        clarification_question="Sare, emi kavali? Groceries, food delivery, leda dineout?",
        reply_text="Sare, emi kavali? Cheppandi.",
        detected_language="te-en",
        confidence=0.3,
        reasoning="Both LLM calls failed",
    )


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

async def decide(
    text: str,
    *,
    conversation_history: list[dict],
    current_state: dict | None = None,
    language: str = "te-IN",
    redis: Any = None,
    family_context: dict | None = None,
    occasion_hint: str | None = None,
) -> BrainAction:
    """The brain's main entry point.

    Takes the user's message, conversation history, and current state,
    calls the LLM, and returns a structured BrainAction.

    If redis is provided, caches responses for 5 minutes keyed on
    (text + state fingerprint). Side-effect actions (confirm, cancel,
    update_address) are never cached.
    """
    t0 = time.monotonic()

    # Check cache (if redis available)
    state_fp = _cache_fingerprint(current_state)
    cache_hit = False
    if redis is not None:
        ck = _cache_key(text.strip().lower(), state_fp)
        try:
            cached_raw = await redis.get(ck)
            if cached_raw:
                cached_action = BrainAction.model_validate_json(cached_raw)
                # Never return cached side-effect actions
                if cached_action.action not in _CACHE_SKIP_ACTIONS:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    logger.info(
                        "brain decide: action=%s model=cache latency=%dms confidence=%.2f",
                        cached_action.action, latency_ms, cached_action.confidence,
                    )
                    return cached_action
        except Exception as e:
            logger.warning("Brain cache read error: %s", e)

    state_description = _build_state_description(current_state)
    history_block = _build_history_block(conversation_history)
    system_prompt = _build_system_prompt(
        state_description, history_block, language,
        family_context=family_context, occasion_hint=occasion_hint,
    )

    # Track prompt version for observability
    _, prompt_version = get_prompt_builder()

    # Try Gemini first, then Azure OpenAI
    action = await _call_gemini(system_prompt, text)
    model_used = "gemini"

    if action is None:
        action = await _call_azure_openai(system_prompt, text, language)
        model_used = "azure_openai"

    # Confidence gate: override low-confidence actions to unclear
    if (
        action.confidence < _CONFIDENCE_THRESHOLD
        and action.action not in ("unclear", "greet")
    ):
        original_action = action.action
        logger.warning(
            "Low confidence %.2f for action=%s, overriding to unclear (threshold=%.2f)",
            action.confidence, original_action, _CONFIDENCE_THRESHOLD,
        )
        action = BrainAction(
            action="unclear",
            clarification_question=action.clarification_question
                or ("Kya chahiye? Clear bataiye — grocery, food, ya kuch aur?" if action.detected_language.startswith("hi") else "Emi kavali? Clear ga cheppandi — groceries, food, leka dineout?"),
            reply_text=action.reply_text or ("Samajh nahi aaya, dobara bataiye?" if action.detected_language.startswith("hi") else "Hmm, clear ga artham kaledu. Malli cheppandi?"),
            detected_language=action.detected_language,
            confidence=action.confidence,
            reasoning=f"Original action={original_action} overridden due to low confidence ({action.confidence:.2f} < {_CONFIDENCE_THRESHOLD})",
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "brain decide: action=%s model=%s prompt=%s latency=%dms confidence=%.2f",
        action.action, model_used, prompt_version, latency_ms, action.confidence,
    )

    # Write cache (if redis available and action is not a side-effect)
    if redis is not None and action.action not in _CACHE_SKIP_ACTIONS:
        try:
            ck = _cache_key(text.strip().lower(), state_fp)
            await redis.set(ck, action.model_dump_json(), ex=_CACHE_TTL_SEC)
        except Exception as e:
            logger.warning("Brain cache write error: %s", e)

    return action
