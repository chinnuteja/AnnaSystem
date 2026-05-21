"""Text Order Pipeline — brain-driven action dispatcher.

Wires: Brain (LLM) → Action Dispatcher → Providers/Renderer → State Machine → DB
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from packages.core.conversation import ConversationStateMachine, ConversationState
from packages.core.session_recovery import recover_stale_parsing_if_needed
from packages.core.db import get_session
from packages.core.models import CanonicalSKU as DBCanonicalSKU, User, VoiceSession, VocabularyTerm
from packages.core.phone_utils import whatsapp_db_lookup_variants
from packages.core.family_resolver import resolve_family_context, FamilyContext
from packages.core.family_cart import FamilyCart, CartItem as FamilyCartItem, load_cart, save_cart, clear_cart as clear_family_cart, check_threshold_and_notify
from packages.core.occasion_calendar import build_occasion_hint
from packages.core.payer_notification import (
    render_payer_approval_notification,
    render_approval_confirmed_to_ordering_user,
    render_approval_rejected_to_ordering_user,
    render_approval_confirmed_to_payer,
    render_approval_rejected_to_payer,
)
from packages.providers.interface import CartItem, CanonicalSKU, Location, ProviderName, SkuPreview
from packages.providers.router import provider_router

from app.agents.brain import BrainAction, decide, load_history, save_turn
from app.agents.sku_mapper import resolve_and_quote
from app.agents.renderer import render_cart_confirmation, render_numbered_options, render_substitutes
from app.agents.executor import execute_order
from app.agents.discovery import discover_options, format_discovery_reply, format_selected_option_reply
from app.schemas.message import DiscoveryOption, ParsedIntent
from packages.providers.catalog_helpers import find_options_in_category

logger = logging.getLogger("foodleaf.pipeline")

DEFAULT_LOCATION = Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad")
DEFAULT_ADDRESS_LABEL = "Flat 304, Madhapur, Hyderabad"

_ADDR_REDIS_KEY = "user:{uid}:delivery_address"

async def _save_user_address(redis, user_id: str, address: str):
    """Persist user's delivery address in Redis (survives session cancels)."""
    await redis.set(_ADDR_REDIS_KEY.format(uid=user_id), address, ex=86400 * 30)

async def _get_user_address(redis, user_id: str) -> str | None:
    """Get user's persisted delivery address."""
    return await redis.get(_ADDR_REDIS_KEY.format(uid=user_id))

async def _resolve_address(ctx) -> str:
    """Resolve address: context > Redis persisted > default."""
    addr = ctx.ctx.get("delivery_address_label")
    if addr:
        return addr
    saved = await _get_user_address(ctx.redis, ctx.user_id)
    if saved:
        return saved
    return DEFAULT_ADDRESS_LABEL
DEFAULT_LOCATION_DELHI = Location(latitude=28.6139, longitude=77.2090, pincode="110001", city="Delhi")
DEFAULT_ADDRESS_LABEL_DELHI = "A-15, Lajpat Nagar, Delhi"

SUBSTITUTE_CATEGORY_HINTS = {
    "onion": "vegetables", "onions": "vegetables", "ulli": "vegetables",
    "pyaaz": "vegetables",
    "tomato": "vegetables", "potato": "vegetables", "aloo": "vegetables",
    "paneer": "dairy_paneer",
    "milk": "dairy_milk", "paalu": "dairy_milk", "doodh": "dairy_milk",
    "curd": "dairy_curd", "perugu": "dairy_curd", "dahi": "dairy_curd",
    "atta": "staples_flour", "godi pindi": "staples_flour",
    "rice": "staples_rice", "biyyam": "staples_rice", "chawal": "staples_rice",
    "oil": "oil_ghee", "nooney": "oil_ghee", "tel": "oil_ghee",
    "ghee": "oil_ghee",
}


class _Ctx:
    """Shared context for all action handlers."""
    __slots__ = (
        "csm", "redis", "user_id", "family_id", "from_phone", "text",
        "brain_action", "current", "location", "language", "input_mode",
        "audio_r2_key", "transcription_raw", "transcription_confidence",
        "whatsapp_message_id", "start_time", "user",
        "family_ctx",  # FamilyContext from family_resolver
    )
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)

    @property
    def ctx(self): return (self.current.get("context") or {}) if self.current else {}
    @property
    def vsid(self): return (self.current.get("session_id") or str(uuid.uuid4())) if self.current else str(uuid.uuid4())
    @property
    def turn(self): return (self.current.get("turn_count") or 0) + 1 if self.current else 1
    def loc(self): return self.location or (DEFAULT_LOCATION_DELHI if self.language.startswith("hi") else DEFAULT_LOCATION)
    @property
    def is_payer(self) -> bool: return getattr(self, "family_ctx", None) is not None and self.family_ctx.is_payer
    @property
    def is_ordering_user(self) -> bool: return getattr(self, "family_ctx", None) is not None and self.family_ctx.is_ordering_user


def _reply(ctx: _Ctx, text: str, state: str = "IDLE") -> dict:
    return {"reply_text": text, "reply_to": ctx.from_phone, "voice_session_id": ctx.vsid, "state": state}


# ============================================================================
# Main Entry Point
# ============================================================================

async def process_text_order(
    csm: ConversationStateMachine,
    from_phone: str,
    text: str,
    whatsapp_message_id: str,
    input_mode: str = "text",
    audio_r2_key: str | None = None,
    transcription_raw: str | None = None,
    transcription_confidence: float | None = None,
    location: Location | None = None,
    client_timings: dict | None = None,
) -> dict:
    start_time = time.monotonic()

    # --- Family-aware user lookup ---
    redis = csm._redis  # noqa: SLF001
    fam_ctx = await resolve_family_context(from_phone, redis)
    if fam_ctx is None:
        return _simple_reply(from_phone, "Sorry, this number isn't registered yet. Please contact your family admin to set up your account.")

    user = fam_ctx.user
    user_id = str(user.id)
    family_id = str(user.family_id)

    current = await csm.current_state(user_id)
    if current is None:
        restored = await _rehydrate_recent_pending_session(csm, user_id)
        if restored is not None:
            current = restored

    current, parsing_block = await recover_stale_parsing_if_needed(
        csm, user_id, current, action="UNKNOWN", text=text, correction_phrases=(),
    )
    if parsing_block:
        return _simple_reply(from_phone, "Just a moment, your previous request is still being processed.", state="PARSING")

    history = await load_history(redis, user_id, max_turns=12)

    # Build family context dict for brain prompt
    family_context_dict = fam_ctx.to_cache_dict()

    # Build proactive occasion hint
    occasion_hint = build_occasion_hint()

    brain_action = await decide(
        text=text, conversation_history=history, current_state=current,
        language=user.preferred_language or "en-IN", redis=redis,
        family_context=family_context_dict, occasion_hint=occasion_hint,
    )

    lang_code = brain_action.detected_language
    language = {
        "te": "te-IN", "en": "en-IN", "te-en": "te-IN",
        "hi": "hi-IN", "hi-en": "hi-IN",
    }.get(lang_code, "en-IN")

    ctx = _Ctx(
        csm=csm, redis=redis, user_id=user_id, family_id=family_id,
        from_phone=from_phone, text=text, brain_action=brain_action,
        current=current, location=location, language=language,
        input_mode=input_mode, audio_r2_key=audio_r2_key,
        transcription_raw=transcription_raw,
        transcription_confidence=transcription_confidence,
        whatsapp_message_id=whatsapp_message_id, start_time=start_time, user=user,
        family_ctx=fam_ctx,
    )

    # --- Address confirmation flow intercept ---
    _flow = ((current or {}).get("context") or {}).get("flow", "")
    if _flow == "awaiting_address_confirm":
        result = await _handle_address_confirm_response(ctx)
    elif _flow == "awaiting_address_input":
        result = await _handle_address_text_input(ctx)
    else:
        handler = _ACTION_HANDLERS.get(brain_action.action, _handle_unclear)
        result = await handler(ctx)

    await save_turn(redis, user_id, "user", text)
    await save_turn(redis, user_id, "bot", result.get("reply_text", ""))

    return result


def _simple_reply(to: str, text: str, state: str = "IDLE") -> dict:
    return {"reply_text": text, "reply_to": to, "voice_session_id": None, "state": state}


# ============================================================================
# Action Handlers
# ============================================================================

async def _handle_greet(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    if ctx.language.startswith("hi"):
        reply = a.reply_text or "Namaste! Anna hoon — aapke parivaar ka saathi. Bataiye, kya chahiye?"
    elif ctx.language == "en-IN":
        reply = a.reply_text or "Hello! I'm Anna — your family concierge. What would you like?"
    else:
        reply = a.reply_text or "Namaskaram! foodleaf lo text or voice tho order cheyyachu. Emi kavali?"
    if ctx.current and ctx.current.get("state") == ConversationState.AWAITING_CONFIRMATION.value:
        if ctx.ctx.get("rehydrated_from_db"):
            await ctx.csm.cancel_session(ctx.user_id)
    try:
        await ctx.csm.transition(ctx.user_id, "IDLE")
    except Exception:
        pass  # No active session — already IDLE
    await _persist(ctx, conversation_state="IDLE", outcome="greet")
    return _reply(ctx, reply, "IDLE")


async def _handle_chitchat(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    if ctx.language.startswith("hi"):
        reply = a.reply_text or "Haan ji, bataiye — kya madad karoon?"
    elif ctx.language == "en-IN":
        reply = a.reply_text or "Sure, tell me — how can I help?"
    else:
        reply = a.reply_text or "Haan, cheppandi — emi cheyyali?"
    if ctx.current and ctx.current.get("state") == ConversationState.AWAITING_CONFIRMATION.value:
        if ctx.ctx.get("resolved_cart"):
            await ctx.csm.bump_turn(ctx.user_id)
            await _persist(ctx, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
            return _reply(ctx, reply, "AWAITING_CONFIRMATION")
    try:
        await ctx.csm.transition(ctx.user_id, "IDLE")
    except Exception:
        pass  # No active session — already IDLE
    await _persist(ctx, conversation_state="IDLE", outcome="chitchat")
    return _reply(ctx, reply, "IDLE")


async def _handle_correct(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    if a.selected_index is not None or a.selected_name is not None:
        return await _handle_select_option(ctx)
    if a.items:
        return await _handle_order_items(ctx)
    if ctx.language.startswith("hi"):
        reply = a.reply_text or "Maaf kijiye, main samajh nahi paya. Dobara bataiye?"
    elif ctx.language == "en-IN":
        reply = a.reply_text or "Sorry, I didn't get that. Could you repeat?"
    else:
        reply = a.reply_text or "Sorry, nenu malli cheyali. Emi kavali cheppandi?"
    if ctx.current and ctx.current.get("state") == ConversationState.AWAITING_CONFIRMATION.value:
        await ctx.csm.cancel_session(ctx.user_id)
    try:
        si = await ctx.csm.start_session(ctx.user_id)
        vsid = si["session_id"]
    except Exception:
        return _reply(ctx, reply, "IDLE")
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
        "flow": "awaiting_assistant", "voice_session_id": vsid,
        "active_goal": "shop", "suggested_domain": "any",
        "confirmation_text": reply, "language": ctx.language,
    })
    await _persist(ctx, voice_session_id=vsid, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    return _reply(ctx, reply, "AWAITING_CONFIRMATION")


async def _handle_select_option(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    flow = ctx.ctx.get("flow", "")

    if flow == "discovery":
        visible = [DiscoveryOption.model_validate(o) for o in ctx.ctx.get("visible_options", [])]
        idx = a.selected_index
        if idx is None and a.selected_name:
            nm = a.selected_name.lower()
            for i, opt in enumerate(visible):
                if nm in opt.name.lower() or opt.name.lower() in nm:
                    idx = i; break
                for w in opt.name.lower().split():
                    if len(w) >= 4 and w in nm:
                        idx = i; break
                if idx is not None: break
        if idx is None or idx >= len(visible):
            return _reply(ctx, "Aa option dorakaledu. First one, second one, leda more options ani cheppandi.", "AWAITING_CONFIRMATION")
        sel = visible[idx]
        cart_data = _cart_data_from_discovery_option(sel)
        reply = format_selected_option_reply(sel)
        new_ctx = {**ctx.ctx, "flow": "discovery_selected", "selected_option": sel.model_dump(),
                   "resolved_cart": cart_data, "confirmation_text": reply}
        await ctx.csm.transition(ctx.user_id, "PARSING", context={"discovery_followup": "select"})
        await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context=new_ctx)
        await _persist(ctx, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
        return _reply(ctx, reply, "AWAITING_CONFIRMATION")

    if flow in {"option_selection", "substitute_selection"}:
        return await _handle_catalog_selection(ctx)

    return await _handle_unclear(ctx)


async def _handle_more_options(ctx: _Ctx) -> dict:
    if ctx.ctx.get("flow") != "discovery":
        return await _handle_unclear(ctx)
    query = ctx.ctx.get("discovery_query", ctx.text)
    offset = int(ctx.ctx.get("discovery_offset", 0)) + 3
    loc = _location_from_context(ctx.ctx) or DEFAULT_LOCATION
    result = await discover_options(ParsedIntent(action="DISCOVER", query_type="open_discovery", raw_text=query), loc, offset=offset)
    if not result.options:
        result = await discover_options(ParsedIntent(action="DISCOVER", query_type="open_discovery", raw_text=query), loc, offset=0)
    reply = format_discovery_reply(result)
    new_ctx = {**ctx.ctx, "flow": "discovery", "discovery_offset": result.offset,
               "visible_options": [o.model_dump() for o in result.options],
               "has_more": result.has_more, "confirmation_text": reply}
    await ctx.csm.transition(ctx.user_id, "PARSING", context={"discovery_followup": "more"})
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context=new_ctx)
    await _persist(ctx, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    return _reply(ctx, reply, "AWAITING_CONFIRMATION")


async def _handle_order_items(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    if not a.items:
        return await _handle_unclear(ctx)
    # Only redirect to discovery if domain is explicitly food_delivery/dineout
    # AND items look like restaurant dishes (no packaged grocery keywords)
    _grocery_keywords = {"packet", "packets", "kg", "g", "ml", "l", "litre", "liter", "pack", "box"}
    if a.domain_hint in {"food_delivery", "dineout"}:
        item_texts = " ".join(i.text.lower() for i in a.items) + " " + " ".join((i.unit or "").lower() for i in a.items)
        if not any(kw in item_texts for kw in _grocery_keywords):
            return await _handle_discover(ctx)

    from app.schemas.message import ParsedItem as SchemasParsedItem
    intent = ParsedIntent(
        action="ORDER", goal="shop", raw_text=ctx.text,
        items=[SchemasParsedItem(text=i.text, quantity=i.quantity, unit=i.unit, brand_hint=i.brand_hint) for i in a.items],
        domain_hint=a.domain_hint, language_detected=ctx.language,
    )
    loc = ctx.loc()

    if _should_offer_item_options(intent):
        return await _offer_item_options(ctx, intent, loc)

    candidates, cart, quote = await resolve_and_quote(intent, loc)

    if not candidates or quote is None:
        return await _handle_no_sku_match(ctx, intent)

    if quote and quote.line_items and not any(li.in_stock for li in quote.line_items):
        addr = await _resolve_address(ctx)
        ct = render_cart_confirmation(quote, address_label=addr, language=ctx.language, candidates=candidates)
        try:
            await ctx.csm.transition(ctx.user_id, "IDLE")
        except Exception:
            pass  # No active session — already IDLE
        await _persist(ctx, intent=intent, conversation_state="IDLE", outcome="failed", failure_reason="out_of_stock")
        return _reply(ctx, ct, "IDLE")

    addr = await _resolve_address(ctx)
    ct = render_cart_confirmation(quote, address_label=addr, language=ctx.language, candidates=candidates)
    cart_data = _cart_data_from_quote(quote, cart)

    # Ensure session exists before transitioning
    if not ctx.current:
        try:
            await ctx.csm.start_session(ctx.user_id)
        except Exception:
            pass  # Session may already exist from a concurrent request
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
        "voice_session_id": ctx.vsid, "parsed_intent": intent.model_dump(),
        "resolved_cart": cart_data, "confirmation_text": ct, "language": ctx.language,
    })
    await _persist(ctx, intent=intent, resolved_cart=cart_data, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    logger.info("Pipeline complete for %s in %dms — cart: %s, total: ₹%s",
                ctx.user_id, int((time.monotonic()-ctx.start_time)*1000),
                [c.display_name for c in candidates], quote.total_inr if quote else "?")
    return _reply(ctx, ct, "AWAITING_CONFIRMATION")


async def _handle_discover(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    # Guard: vague grocery requests should NOT go to discovery — redirect to chitchat
    _grocery_words = {"grocery", "groceries", "kavali", "chahiye", "essentials", "saman", "ration"}
    text_lower = ctx.text.lower()
    if any(w in text_lower for w in _grocery_words) and a.domain_hint not in {"food_delivery", "dineout"}:
        if ctx.language.startswith("hi"):
            reply = "Ji haan! Bataiye kya kya chahiye — milk, atta, rice, oil?"
        elif ctx.language == "en-IN":
            reply = "Sure! Tell me what items you need — milk, atta, rice, oil?"
        else:
            reply = "Sure Chinnu ji! Em em kavali cheppandi — milk, atta, rice, oil?"
        try:
            await ctx.csm.transition(ctx.user_id, "IDLE")
        except Exception:
            pass
        await _persist(ctx, conversation_state="IDLE", outcome="chitchat")
        return _reply(ctx, reply, "IDLE")
    # Build the ParsedIntent early so it's available for both code paths
    intent = ParsedIntent(action="DISCOVER", goal="discover", raw_text=a.discovery_query or ctx.text,
                          domain_hint=a.domain_hint, query_type="open_discovery", language_detected=ctx.language)
    if ctx.location is None:
        if ctx.language.startswith("hi"):
            reply = "Apna current location WhatsApp pe share karein. Phir nearby options dikhaoonga."
        elif ctx.language == "en-IN":
            reply = "Please share your current location on WhatsApp. Then I can show nearby options."
        else:
            reply = "Mee current location WhatsApp lo share cheyyandi. Appudu nearby options chepthanu."
        # Ensure session exists before transitioning
        if not ctx.current:
            try:
                await ctx.csm.start_session(ctx.user_id)
            except Exception:
                pass
        await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
            "flow": "awaiting_location", "voice_session_id": ctx.vsid,
            "parsed_intent": intent.model_dump(),
            "discovery_query": a.discovery_query or ctx.text,
            "confirmation_text": reply, "language": ctx.language,
        })
        await _persist(ctx, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending", failure_reason="awaiting_location")
        return _reply(ctx, reply, "AWAITING_CONFIRMATION")

    result = await discover_options(intent, ctx.loc())
    reply = format_discovery_reply(result)
    # Ensure session exists before transitioning
    if not ctx.current:
        try:
            await ctx.csm.start_session(ctx.user_id)
        except Exception:
            pass
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
        "flow": "discovery", "voice_session_id": ctx.vsid, "parsed_intent": intent.model_dump(),
        "discovery_query": intent.raw_text, "discovery_offset": result.offset,
        "visible_options": [o.model_dump() for o in result.options],
        "has_more": result.has_more, "location": _location_to_dict(ctx.loc()),
        "confirmation_text": reply, "language": ctx.language,
    })
    await _persist(ctx, intent=intent, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    return _reply(ctx, reply, "AWAITING_CONFIRMATION")


async def _handle_confirm(ctx: _Ctx) -> dict:
    if not ctx.current or ctx.current.get("state") != ConversationState.AWAITING_CONFIRMATION.value:
        if ctx.language.startswith("hi"):
            reply = "Koi pending order nahi hai. Kya order karna hai?"
        elif ctx.language == "en-IN":
            reply = "No pending order. What would you like to order?"
        else:
            reply = "Pending order ledu. Emi order cheyyali?"
        return _reply(ctx, reply, "IDLE")

    flow = ctx.ctx.get("flow", "")
    if flow == "discovery_selected":
        await ctx.csm.transition(ctx.user_id, "EXECUTING")
        order = await execute_order(ctx.user_id, ctx.family_id, ctx.ctx.get("resolved_cart", {}), ctx.vsid)
        if order:
            reply = f"Sare, option confirm ayindi! (ID: {order.provider_order_id[-6:] if order.provider_order_id else 'N/A'})."
            outcome = "order_placed"
        else:
            reply = "Sorry, provider nunchi issue vachindi. Option confirm avaledu."
            outcome = "failed"
        await ctx.csm.transition(ctx.user_id, "COMPLETE")
        await _update_voice_session_status(ctx.vsid, "COMPLETE", outcome)
        return _reply(ctx, reply, "COMPLETE")

    if flow == "discovery":
        if ctx.language.startswith("hi"):
            reply = "Pehle option select karein: pehla, doosra, ya more options bataiye."
        else:
            reply = "Mundhu option select cheyyandi: first one, second one, leda more options ani cheppandi."
        return _reply(ctx, reply, "AWAITING_CONFIRMATION")

    # --- Family cart threshold check ---
    cart_data = ctx.ctx.get("resolved_cart", {})
    cart_total = cart_data.get("quote_total_inr", 0) or 0
    threshold = ctx.family_ctx.approval_threshold if ctx.family_ctx else 0

    # If ordering user and total >= threshold, save to family cart and notify payer
    if ctx.is_ordering_user and threshold > 0 and cart_total >= threshold:
        # Build family cart from resolved cart data
        fam_cart = await load_cart(ctx.family_id, ctx.redis)
        for item_data in cart_data.get("items", []):
            fam_cart.add_item(FamilyCartItem(
                name=item_data.get("display_name", "item"),
                quantity=item_data.get("quantity", 1),
                price_inr=item_data.get("price_inr"),
                brand=item_data.get("brand"),
                added_by=ctx.user_id,
            ))
        fam_cart.ordering_user_id = ctx.user_id
        fam_cart.ordering_user_phone = ctx.from_phone
        fam_cart.payer_user_id = str(ctx.family_ctx.payer.id) if ctx.family_ctx and ctx.family_ctx.payer else None
        await save_cart(fam_cart, ctx.redis)

        # Check threshold and set pending_approval
        needs_approval = await check_threshold_and_notify(fam_cart, threshold, ctx.redis)

        if needs_approval and ctx.family_ctx and ctx.family_ctx.payer:
            # Notify payer via WhatsApp
            payer_notification = render_payer_approval_notification(
                fam_cart,
                payer_name=ctx.family_ctx.payer_display_name or "Payer",
                ordering_name=ctx.family_ctx.user.display_name or "Maa",
                family_name=ctx.family_ctx.family.display_name or "Family",
            )

            # Tell ordering user that approval is pending
            if ctx.language.startswith("hi"):
                ordering_reply = (
                    f"Aapka order ₹{cart_total:.0f} ka hai, isliye {ctx.family_ctx.payer_display_name or 'payer'} "
                    f"ki approval chahiye. Unhe notification bhej diya hai — jaldi approve karenge! 🙏"
                )
            else:
                ordering_reply = (
                    f"Your order is ₹{cart_total:.0f}, so {ctx.family_ctx.payer_display_name or 'payer'} "
                    f"needs to approve. Notification sent — they'll approve soon! 🙏"
                )

            await ctx.csm.transition(ctx.user_id, "AWAITING_APPROVAL", context={
                **ctx.ctx, "approval_status": "pending_approval",
                "family_cart_id": fam_cart.cart_id,
            })
            await _persist(ctx, resolved_cart=cart_data, conversation_state="AWAITING_APPROVAL", outcome="awaiting_payer_approval")

            return {
                "reply_text": ordering_reply,
                "reply_to": ctx.from_phone,
                "voice_session_id": ctx.vsid,
                "state": "AWAITING_APPROVAL",
                "notify_payer": {
                    "phone": ctx.family_ctx.payer_phone,
                    "text": payer_notification,
                },
            }

    # --- Address confirmation step before placing order ---
    addr = await _resolve_address(ctx)
    if ctx.language.startswith("hi"):
        addr_reply = f"📍 Delivery address: {addr}\n\nKya yeh address sahi hai? (haan / nahi)"
    elif ctx.language == "en-IN":
        addr_reply = f"📍 Delivery address: {addr}\n\nIs this address correct? (yes / no)"
    else:
        addr_reply = f"📍 Delivery address: {addr}\n\nEe address correct aa? (avunu / kadu)"
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
        **ctx.ctx, "flow": "awaiting_address_confirm",
    })
    return _reply(ctx, addr_reply, "AWAITING_CONFIRMATION")


async def _place_order_final(ctx: _Ctx) -> dict:
    """Actually place the order after address is confirmed."""
    cart_data = ctx.ctx.get("resolved_cart", {})
    # Auto-approve (below threshold or no family context) — place order directly
    logger.info("User %s CONFIRMED the order.", ctx.user_id)
    await ctx.csm.transition(ctx.user_id, "EXECUTING")
    order = await execute_order(ctx.user_id, ctx.family_id, cart_data, ctx.vsid)
    if order:
        if ctx.language.startswith("hi"):
            reply = f"Aapka order confirm ho gaya! (ID: ...{order.provider_order_id[-6:] if order.provider_order_id else 'N/A'}). Jaldi delivery hogi! 🙏"
        elif ctx.language == "en-IN":
            reply = f"Your order is confirmed! (ID: ...{order.provider_order_id[-6:] if order.provider_order_id else 'N/A'}). Delivery coming soon! 🙏"
        else:
            reply = f"Sare, mee order confirm ayindi! (ID: {order.provider_order_id[-6:] if order.provider_order_id else 'N/A'}). Tracking details twaralo vastayi."
        outcome = "order_placed"
    else:
        if ctx.language.startswith("hi"):
            reply = "Sorry, order place nahi ho paya. Dobara try karein."
        else:
            reply = "Sorry, provider nunchi issue vachindi. Order process avaledu."
        outcome = "failed"
    await ctx.csm.transition(ctx.user_id, "COMPLETE")
    await _update_voice_session_status(ctx.vsid, "COMPLETE", outcome)
    return _reply(ctx, reply, "COMPLETE")


async def _handle_address_confirm_response(ctx: _Ctx) -> dict:
    """Handle user's yes/no response to address confirmation."""
    text_lower = ctx.text.lower().strip()
    _yes_words = {"yes", "haan", "ha", "haa", "avunu", "antey", "sare", "ok", "confirm", "correct", "right", "ji"}
    _no_words = {"no", "nahi", "nah", "kadu", "vaddu", "wrong", "change", "galat", "incorrect"}

    if any(w in text_lower.split() for w in _yes_words) or any(w == text_lower for w in _yes_words):
        return await _place_order_final(ctx)

    if any(w in text_lower.split() for w in _no_words) or any(w == text_lower for w in _no_words):
        if ctx.language.startswith("hi"):
            reply = "Theek hai! Apna delivery address type karein ya WhatsApp pe location share karein.\n\nExample: Flat 201, Saikrupa Apartments, Kukatpally, 500072"
        elif ctx.language == "en-IN":
            reply = "Sure! Please type your delivery address or share your location on WhatsApp.\n\nExample: Flat 201, Saikrupa Apartments, Kukatpally, 500072"
        else:
            reply = "Sare! Mee delivery address type cheyyandi leda WhatsApp lo location share cheyyandi.\n\nExample: Flat 201, Saikrupa Apartments, Kukatpally, 500072"
        await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
            **ctx.ctx, "flow": "awaiting_address_input",
        })
        return _reply(ctx, reply, "AWAITING_CONFIRMATION")

    # User typed something else — might be an address directly
    return await _handle_address_text_input(ctx)


async def _handle_address_text_input(ctx: _Ctx) -> dict:
    """Parse a text address from user and ask for missing details or confirm."""
    import re
    text = ctx.text.strip()

    # Check if user wants to cancel
    _cancel_words = {"cancel", "vaddu", "nahi", "stop"}
    if any(w in text.lower().split() for w in _cancel_words):
        return await _handle_cancel(ctx)

    # Parse address components
    pincode_match = re.search(r'\b(\d{6})\b', text)
    pincode = pincode_match.group(1) if pincode_match else None
    flat_match = re.search(r'(?:flat|door|house|apt|#)\s*(?:no\.?\s*)?(\d[\w/-]*)', text, re.IGNORECASE)
    flat_no = flat_match.group(0).strip() if flat_match else None

    # Check if address seems incomplete (no flat/door number)
    has_flat = flat_no is not None or re.search(r'\d+[-/]\d+', text) is not None
    addr_text = text

    if not has_flat:
        # Address is too sparse — ask for flat number
        if ctx.language.startswith("hi"):
            reply = f"📍 Area samajh gaya: {text}\n\nAapka flat/house number kya hai?"
        elif ctx.language == "en-IN":
            reply = f"📍 Got the area: {text}\n\nWhat's your flat/house number?"
        else:
            reply = f"📍 Area ardham ayindi: {text}\n\nMee flat/house number cheppandi?"
        await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
            **ctx.ctx, "flow": "awaiting_address_input",
            "partial_address": text,
        })
        return _reply(ctx, reply, "AWAITING_CONFIRMATION")

    # If we have a partial address from before, combine
    partial = ctx.ctx.get("partial_address", "")
    if partial and len(text.split()) <= 4:
        addr_text = f"{text}, {partial}"

    # Address is complete enough — update and place order
    if ctx.language.startswith("hi"):
        addr_reply = f"📍 Delivery address updated: {addr_text}\n\nOrder place kar raha hoon..."
    elif ctx.language == "en-IN":
        addr_reply = f"📍 Delivery address updated: {addr_text}\n\nPlacing your order..."
    else:
        addr_reply = f"📍 Delivery address updated: {addr_text}\n\nOrder place chestunnanu..."

    # Update address in context and Redis, then place order
    ctx.ctx["delivery_address_label"] = addr_text
    await _save_user_address(ctx.redis, ctx.user_id, addr_text)
    
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
        **ctx.ctx, "flow": "pending_order", "delivery_address_label": addr_text,
    })
    order_result = await _place_order_final(ctx)
    # Prepend the address update message
    order_result["reply_text"] = addr_reply + "\n\n" + order_result["reply_text"]
    return order_result


async def _handle_cancel(ctx: _Ctx) -> dict:
    reply = "Theek hai, order cancel ho gaya." if ctx.language.startswith("hi") else ("Okay, your order has been cancelled." if ctx.language == "en-IN" else "Sare, mee order cancel chesanu.")
    if ctx.current:
        await ctx.csm.cancel_session(ctx.user_id)
        await _update_voice_session_status(ctx.vsid, "IDLE", "cancelled")
    return _reply(ctx, reply, "IDLE")


async def _handle_track_order(ctx: _Ctx) -> dict:
    from app.agents.renderer.tracking import render_tracking_prompt
    reply = render_tracking_prompt()
    try:
        await ctx.csm.transition(ctx.user_id, "IDLE")
    except Exception:
        pass  # No active session — already IDLE
    await _persist(ctx, conversation_state="IDLE", outcome="track")
    return _reply(ctx, reply, "IDLE")


async def _handle_ask_cart(ctx: _Ctx) -> dict:
    cart = ctx.ctx.get("resolved_cart", {})
    items = cart.get("items", [])
    if not items:
        if ctx.language.startswith("hi"):
            reply = "Aapka cart khaali hai. Kya order karna hai?"
        elif ctx.language == "en-IN":
            reply = "Your cart is empty. What would you like to order?"
        else:
            reply = "Mee cart empty undi. Emi order cheyyali?"
    else:
        base = ctx.ctx.get("confirmation_text") or _render_pending_cart_summary(ctx.ctx)
        if ctx.language.startswith("hi"):
            reply = f"{base}\nAur items add karne ke liye naam bataiye."
        else:
            reply = f"{base}\nInka items add cheyyali ante item peru cheppandi."
    if ctx.current and ctx.current.get("state") == ConversationState.AWAITING_CONFIRMATION.value:
        await ctx.csm.bump_turn(ctx.user_id)
        await _persist(ctx, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
        return _reply(ctx, reply, "AWAITING_CONFIRMATION")
    return _reply(ctx, reply, "IDLE")


async def _handle_clear_cart(ctx: _Ctx) -> dict:
    if ctx.language.startswith("hi"):
        reply = "Theek hai, cart khaali kar diya. Naya order bataiye."
    elif ctx.language == "en-IN":
        reply = "Okay, your cart has been cleared. What would you like to order?"
    else:
        reply = "Sare, mee cart clear chesanu. Kotha order cheppandi."
    if ctx.current:
        await ctx.csm.cancel_session(ctx.user_id)
        await _persist(ctx, conversation_state="IDLE", outcome="cancelled", failure_reason="user_cleared_cart")
    return _reply(ctx, reply, "IDLE")


async def _handle_update_address(ctx: _Ctx) -> dict:
    addr = ctx.brain_action.address_text or ctx.text
    await _save_user_address(ctx.redis, ctx.user_id, addr)
    if not ctx.current or ctx.current.get("state") != ConversationState.AWAITING_CONFIRMATION.value:
        reply = "Address save chesanu. Emi order cheyyali?" if ctx.language != "en-IN" else "Address saved. What would you like to order?"
        return _reply(ctx, reply, "IDLE")
    new_ctx = dict(ctx.ctx)
    new_ctx["delivery_address_label"] = addr
    ack = f"Delivery address updated: {addr}. Confirm chey-yana? (avunu / vaddu)"
    await ctx.csm.restore_session(ordering_user_id=ctx.user_id, voice_session_id=ctx.vsid,
                                   state="AWAITING_CONFIRMATION", context=new_ctx, turn_count=ctx.turn)
    await _persist(ctx, resolved_cart=ctx.ctx.get("resolved_cart"), conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    return _reply(ctx, ack, "AWAITING_CONFIRMATION")


async def _handle_unclear(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    if ctx.language.startswith("hi"):
        reply = a.reply_text or a.clarification_question or "Kya chahiye? Grocery, food delivery, ya kuch aur?"
    elif ctx.language == "en-IN":
        reply = a.reply_text or a.clarification_question or "What would you like? Groceries, food delivery, or something else?"
    else:
        reply = a.reply_text or a.clarification_question or "Sare, emi kavali? Groceries (atta, milk) / food delivery (biryani) / dineout?"
    if ctx.current and ctx.current.get("state") == ConversationState.AWAITING_CONFIRMATION.value:
        if ctx.ctx.get("resolved_cart"):
            await ctx.csm.bump_turn(ctx.user_id)
            await _persist(ctx, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
            return _reply(ctx, reply, "AWAITING_CONFIRMATION")
    try:
        si = await ctx.csm.start_session(ctx.user_id)
        vsid = si["session_id"]
    except Exception:
        return _reply(ctx, reply, "IDLE")
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
        "flow": "awaiting_assistant", "voice_session_id": vsid,
        "active_goal": "shop", "suggested_domain": "any",
        "confirmation_text": reply, "language": ctx.language,
    })
    await _persist(ctx, voice_session_id=vsid, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    return _reply(ctx, reply, "AWAITING_CONFIRMATION")


# ============================================================================
# Payer Approval Handlers
# ============================================================================

async def _handle_approve(ctx: _Ctx) -> dict:
    """Payer approves a pending payment request."""
    if not ctx.is_payer:
        reply = "Aap payer nahi hain, isliye approve nahi kar sakte." if ctx.language.startswith("hi") else "You're not the payer for this family."
        return _reply(ctx, reply, "IDLE")

    # Load the family cart
    cart = await load_cart(ctx.family_id, ctx.redis)
    if not cart.items or cart.approval_status != "pending_approval":
        reply = "Koi pending approval request nahi hai." if ctx.language.startswith("hi") else "No pending approval requests."
        return _reply(ctx, reply, "IDLE")

    # Mark as approved
    cart.approval_status = "approved"
    await save_cart(cart, ctx.redis)

    # Transition the ordering user's session from AWAITING_APPROVAL → EXECUTING
    ordering_user_id = cart.ordering_user_id
    if ordering_user_id:
        try:
            await ctx.csm.transition(ordering_user_id, "EXECUTING")
        except Exception:
            pass

    # Place the order (mock)
    order = await execute_order(ordering_user_id or ctx.user_id, ctx.family_id, ctx.ctx.get("resolved_cart", {}), ctx.vsid)

    # Notify payer
    payer_reply = render_approval_confirmed_to_payer(cart, payer_name=ctx.family_ctx.payer_display_name or "Payer", locale=ctx.language)

    # Notify ordering user (Maa) — stored in cart context for async delivery
    ordering_reply = render_approval_confirmed_to_ordering_user(
        cart, payer_name=ctx.family_ctx.payer_display_name or "Rahul",
        locale=ctx.family_ctx.primary_locale,
    )

    if order:
        payer_reply += f" Order ID: ...{order.provider_order_id[-6:] if order.provider_order_id else 'N/A'}"
        outcome = "order_placed"
    else:
        payer_reply += " (Order placement issue — will retry)"
        outcome = "failed"

    if ordering_user_id:
        try:
            await ctx.csm.transition(ordering_user_id, "COMPLETE")
        except Exception:
            pass
    await _update_voice_session_status(ctx.vsid, "COMPLETE", outcome)

    # Return both messages — the pipeline caller should send payer_reply to payer
    # and ordering_reply to the ordering user via WhatsApp
    ordering_phone = None
    if ctx.family_ctx and ctx.family_ctx.payer:
        # The ordering user is NOT the current user (payer); get their phone from the cart
        ordering_phone = cart.ordering_user_phone or (ctx.family_ctx.user.phone_e164 if ctx.family_ctx else None)
    return {
        "reply_text": payer_reply,
        "reply_to": ctx.from_phone,
        "voice_session_id": ctx.vsid,
        "state": "COMPLETE",
        "notify_ordering_user": {
            "phone": ordering_phone,
            "text": ordering_reply,
        },
    }


async def _handle_reject_approval(ctx: _Ctx) -> dict:
    """Payer rejects a pending payment request."""
    if not ctx.is_payer:
        reply = "Aap payer nahi hain, isliye reject nahi kar sakte." if ctx.language.startswith("hi") else "You're not the payer for this family."
        return _reply(ctx, reply, "IDLE")

    # Load the family cart
    cart = await load_cart(ctx.family_id, ctx.redis)
    if not cart.items or cart.approval_status != "pending_approval":
        reply = "Koi pending approval request nahi hai." if ctx.language.startswith("hi") else "No pending approval requests."
        return _reply(ctx, reply, "IDLE")

    # Mark as rejected
    cart.approval_status = "rejected"
    await save_cart(cart, ctx.redis)

    # Notify payer
    payer_reply = render_approval_rejected_to_payer(cart, payer_name=ctx.family_ctx.payer_display_name or "Payer", locale=ctx.language)

    # Notify ordering user (Maa)
    ordering_reply = render_approval_rejected_to_ordering_user(
        cart, payer_name=ctx.family_ctx.payer_display_name or "Rahul",
        locale=ctx.family_ctx.primary_locale,
    )

    if not ctx.current:
        try:
            await ctx.csm.start_session(ctx.user_id)
        except Exception:
            pass
    await ctx.csm.cancel_session(ctx.user_id)

    # Also cancel the ordering user's session
    ordering_user_id = cart.ordering_user_id
    if ordering_user_id:
        try:
            await ctx.csm.cancel_session(ordering_user_id)
        except Exception:
            pass
    await _persist(ctx, conversation_state="IDLE", outcome="rejected_by_payer")

    return {
        "reply_text": payer_reply,
        "reply_to": ctx.from_phone,
        "voice_session_id": ctx.vsid,
        "state": "IDLE",
        "notify_ordering_user": {
            "phone": cart.ordering_user_phone or (ctx.family_ctx.user.phone_e164 if ctx.family_ctx else None),
            "text": ordering_reply,
        },
    }


_ACTION_HANDLERS = {
    "greet": _handle_greet, "select_option": _handle_select_option,
    "more_options": _handle_more_options, "order_items": _handle_order_items,
    "discover": _handle_discover, "confirm": _handle_confirm,
    "cancel": _handle_cancel, "correct": _handle_correct,
    "track_order": _handle_track_order, "ask_cart": _handle_ask_cart,
    "clear_cart": _handle_clear_cart, "update_address": _handle_update_address,
    "chitchat": _handle_chitchat, "unclear": _handle_unclear,
    "approve": _handle_approve, "reject_approval": _handle_reject_approval,
}


# ============================================================================
# Sub-handlers
# ============================================================================

async def _handle_catalog_selection(ctx: _Ctx) -> dict:
    a = ctx.brain_action
    options = [_preview_from_dict(o) for o in ctx.ctx.get("options", [])]
    idx = a.selected_index
    if idx is None and a.selected_name:
        nm = a.selected_name.lower()
        for i, opt in enumerate(options):
            haystack = f"{opt.display_name} {opt.brand} {opt.pack_size_label}".lower()
            if opt.brand and opt.brand.lower() in nm:
                idx = i; break
            if any(p for p in nm.split() if len(p) >= 4 and p in haystack):
                idx = i; break
    if idx is None or idx >= len(options):
        reply = render_numbered_options(requested_text=ctx.ctx.get("requested_text", "item"),
                                        options=options, language=ctx.ctx.get("language", "te-IN"))
        return _reply(ctx, reply, "AWAITING_CONFIRMATION")
    sel = options[idx]
    provider = provider_router.grocery()
    sku = _canonical_from_preview(sel)
    qty = int(ctx.ctx.get("quantity") or 1)
    cart = await provider.assemble_cart([CartItem(canonical_sku=sku, quantity=qty)], ctx.loc())
    quote = await provider.quote_cart(cart)
    addr = await _resolve_address(ctx)
    ct = render_cart_confirmation(quote, address_label=addr, language=ctx.ctx.get("language", "te-IN"), candidates=[])
    cart_data = _cart_data_from_quote(quote, cart)
    await ctx.csm.restore_session(ordering_user_id=ctx.user_id, voice_session_id=ctx.vsid,
                                   state="AWAITING_CONFIRMATION", context={
                                       "voice_session_id": ctx.vsid,
                                       "parsed_intent": {"action": "ORDER", "raw_text": ctx.text},
                                       "resolved_cart": cart_data, "confirmation_text": ct,
                                       "language": ctx.ctx.get("language", "te-IN"),
                                   }, turn_count=ctx.turn)
    await _persist(ctx, resolved_cart=cart_data, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    return _reply(ctx, ct, "AWAITING_CONFIRMATION")


async def _offer_item_options(ctx: _Ctx, intent: ParsedIntent, location: Location) -> dict:
    requested_text = _requested_text_for_substitutes(intent)
    category = await _infer_substitute_category(intent)
    options = await find_options_in_category(category=category, limit=6, in_stock_only=True)
    brand_pref = None
    if intent.items and intent.items[0].brand_hint:
        brand_pref = intent.items[0].brand_hint.lower()
    if brand_pref:
        filtered = [o for o in options if ((o.brand or "").lower().find(brand_pref) != -1)]
        options = filtered or options
    options = options[:3]
    if len(options) < 2:
        # Not enough options to present a choice — resolve directly
        candidates, cart, quote = await resolve_and_quote(intent, location)
        if not candidates or quote is None:
            return await _handle_no_sku_match(ctx, intent)
        addr = await _resolve_address(ctx)
        ct = render_cart_confirmation(quote, address_label=addr, language=ctx.language, candidates=candidates)
        cart_data = _cart_data_from_quote(quote, cart)
        # Ensure session exists before transitioning
        if not ctx.current:
            try:
                await ctx.csm.start_session(ctx.user_id)
            except Exception:
                pass
        await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context={
            "voice_session_id": ctx.vsid, "parsed_intent": intent.model_dump(),
            "resolved_cart": cart_data, "confirmation_text": ct, "language": ctx.language,
        })
        await _persist(ctx, intent=intent, resolved_cart=cart_data, conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
        return _reply(ctx, ct, "AWAITING_CONFIRMATION")
    reply = render_numbered_options(requested_text=requested_text, options=options, language=ctx.language)
    opt_ctx = {
        "flow": "option_selection", "voice_session_id": ctx.vsid,
        "requested_text": requested_text,
        "options": [_preview_to_dict(o) for o in options],
        "quantity": intent.items[0].quantity or 1 if intent.items else 1,
        "language": ctx.language, "confirmation_text": reply,
    }
    # Ensure session exists before transitioning
    if not ctx.current:
        try:
            await ctx.csm.start_session(ctx.user_id)
        except Exception:
            pass
    await ctx.csm.transition(ctx.user_id, "AWAITING_CONFIRMATION", context=opt_ctx)
    await _persist(ctx, intent=intent, resolved_cart={"flow": "option_selection", "options": opt_ctx["options"]},
                  conversation_state="AWAITING_CONFIRMATION", outcome="still_pending")
    return _reply(ctx, reply, "AWAITING_CONFIRMATION")


async def _handle_no_sku_match(ctx: _Ctx, intent: ParsedIntent) -> dict:
    requested_text = _requested_text_for_substitutes(intent)
    category = await _infer_substitute_category(intent)
    substitutes = []
    if category:
        substitutes = await find_options_in_category(category=category, limit=3, in_stock_only=True)
    ct = render_substitutes(requested_text=requested_text, substitutes=substitutes, language=ctx.language)
    if ctx.current and ctx.current.get("state") == ConversationState.AWAITING_CONFIRMATION.value:
        if ctx.ctx.get("resolved_cart"):
            base = ctx.ctx.get("confirmation_text") or _render_pending_cart_summary(ctx.ctx)
            reply = f"{ct}\nMee existing cart alane undi.\n{base}"
            await ctx.csm.restore_session(ordering_user_id=ctx.user_id, voice_session_id=ctx.vsid,
                                           state="AWAITING_CONFIRMATION", context=ctx.ctx, turn_count=ctx.turn)
            await _persist(ctx, resolved_cart=ctx.ctx.get("resolved_cart"), conversation_state="AWAITING_CONFIRMATION",
                          outcome="still_pending", failure_reason="add_item_no_match")
            return _reply(ctx, reply, "AWAITING_CONFIRMATION")
    try:
        await ctx.csm.transition(ctx.user_id, "IDLE")
    except Exception:
        pass  # No active session — already IDLE
    await _persist(ctx, intent=intent, conversation_state="IDLE", outcome="failed", failure_reason="no_sku_match")
    return _reply(ctx, ct, "IDLE")


# ============================================================================
# Location Message Handler
# ============================================================================

async def process_location_message(
    csm: ConversationStateMachine, from_phone: str,
    whatsapp_message_id: str, location: Location,
) -> dict:
    start_time = time.monotonic()
    redis = csm._redis  # noqa: SLF001
    fam_ctx = await resolve_family_context(from_phone, redis)
    if fam_ctx is None:
        return _simple_reply(from_phone, "Sorry, this number isn't registered yet. Please contact your family admin to set up your account.")
    user = fam_ctx.user
    user_id = str(user.id)
    current = await csm.current_state(user_id)
    if not current or current.get("state") != ConversationState.AWAITING_CONFIRMATION.value:
        return _simple_reply(from_phone, "Got your location! What would you like to order or discover nearby?")
    context = current.get("context") or {}
    if context.get("flow") != "awaiting_location":
        return {"reply_text": "Location saved. No pending discovery request right now.",
                "reply_to": from_phone, "voice_session_id": current.get("session_id"), "state": current["state"]}
    vsid = current.get("session_id", str(uuid.uuid4()))
    intent = ParsedIntent.model_validate(context["parsed_intent"])
    result = await discover_options(intent, location)
    reply = format_discovery_reply(result)
    new_ctx = {
        "flow": "discovery", "voice_session_id": vsid, "parsed_intent": intent.model_dump(),
        "discovery_query": intent.raw_text, "discovery_offset": result.offset,
        "visible_options": [o.model_dump() for o in result.options],
        "has_more": result.has_more, "location": _location_to_dict(location),
        "confirmation_text": reply, "language": context.get("language", intent.language_detected),
    }
    await csm.transition(user_id, "PARSING", context={"location_received": True})
    await csm.transition(user_id, "AWAITING_CONFIRMATION", context=new_ctx)
    await _update_voice_session_status(vsid, "AWAITING_CONFIRMATION", "still_pending")
    return {"reply_text": reply, "reply_to": from_phone, "voice_session_id": vsid, "state": "AWAITING_CONFIRMATION"}


# ============================================================================
# Persistence & Helpers
# ============================================================================

async def _persist(
    ctx: _Ctx, *, intent=None, resolved_cart=None, conversation_state="IDLE",
    outcome="still_pending", failure_reason=None, voice_session_id=None,
):
    try:
        vsid = voice_session_id or ctx.vsid
        if ctx.turn > 0 and outcome == "still_pending":
            resolved_cart = {**(resolved_cart or {}), "_turn_count": ctx.turn}
        intent_obj = intent or _brain_action_to_intent(ctx.brain_action)
        async with get_session() as session:
            vs = VoiceSession(
                id=uuid.UUID(vsid) if len(vsid) == 36 else uuid.uuid4(),
                family_id=uuid.UUID(ctx.family_id),
                ordering_user_id=uuid.UUID(ctx.user_id),
                whatsapp_message_id=ctx.whatsapp_message_id,
                input_mode=ctx.input_mode,
                raw_text=ctx.text,
                audio_r2_key=ctx.audio_r2_key,
                transcription_raw=ctx.transcription_raw,
                normalized_text=ctx.text.strip().lower(),
                language_detected=ctx.language,
                transcription_confidence=ctx.transcription_confidence,
                parsed_intent=intent_obj.model_dump(),
                resolved_cart=resolved_cart,
                conversation_state=conversation_state,
                pipeline_latency_ms=int((time.monotonic() - ctx.start_time) * 1000),
                outcome=outcome,
                failure_reason=failure_reason,
                ack_message_sent=False,
            )
            await session.merge(vs)
    except Exception as e:
        logger.error("_persist failed (non-fatal): %s", e)


def _brain_action_to_intent(action: BrainAction) -> ParsedIntent:
    return ParsedIntent(
        action=action.action.upper() if action.action in ("order_items", "discover", "confirm", "cancel", "track_order") else "CHITCHAT",
        goal="shop" if action.action in ("order_items", "confirm") else "discover" if action.action == "discover" else "chat",
        raw_text=action.reply_text or "",
        domain_hint=action.domain_hint,
        language_detected={"te": "te-IN", "en": "en-IN", "te-en": "te-IN", "hi": "hi-IN", "hi-en": "hi-IN"}.get(action.detected_language, "hi-IN"),
    )


async def _update_voice_session_status(vsid: str, state: str, outcome: str, failure_reason=None, pipeline_ms=0):
    if not vsid or len(vsid) != 36:
        return
    async with get_session() as session:
        vs = await session.get(VoiceSession, uuid.UUID(vsid))
        if vs is None:
            return
        vs.conversation_state = state
        vs.outcome = outcome
        vs.pipeline_latency_ms = pipeline_ms
        vs.failure_reason = failure_reason
        vs.updated_at = datetime.now(timezone.utc)


async def mark_ack_message_sent(voice_session_id: str | None) -> None:
    if not voice_session_id or len(voice_session_id) != 36:
        return
    async with get_session() as session:
        vs = await session.get(VoiceSession, uuid.UUID(voice_session_id))
        if vs is None:
            return
        vs.ack_message_sent = True
        vs.updated_at = datetime.now(timezone.utc)


async def _lookup_user(phone_e164: str) -> User | None:
    variants = whatsapp_db_lookup_variants(phone_e164)
    if not variants:
        return None
    async with get_session() as session:
        result = await session.execute(select(User).where(User.whatsapp_phone_e164.in_(variants)))
        return result.scalars().first()



def _should_offer_item_options(intent: ParsedIntent) -> bool:
    if intent.action != "ORDER" or len(intent.items) != 1:
        return False
    item = intent.items[0]
    if item.quantity and item.quantity > 1:
        return False
    if any(c.isdigit() for c in intent.raw_text):
        return False
    return item.text.strip().lower() in {
        "atta", "godi pindi", "flour", "wheat flour",
        "milk", "paalu", "doodh", "rice", "biyyam", "chawal", "oil", "sunflower oil", "tel",
    }


async def _infer_substitute_category(intent: ParsedIntent) -> str | None:
    raw = " ".join([intent.raw_text, *(i.text for i in intent.items)]).lower()
    hinted = _category_hint_from_text(raw)
    if hinted:
        return hinted
    for item in intent.items:
        qt = item.text.lower().strip()
        if not qt:
            continue
        try:
            async with get_session() as session:
                result = await session.execute(
                    select(VocabularyTerm).where(VocabularyTerm.term.ilike(f"%{qt}%")).limit(1))
                vocab = result.scalars().first()
                if vocab is not None:
                    return vocab.maps_to_category
        except Exception as e:
            logger.warning("Failed to infer substitute category from vocabulary: %s", e)
    try:
        async with get_session() as session:
            result = await session.execute(
                select(DBCanonicalSKU.category).where(
                    DBCanonicalSKU.active.is_(True),
                    or_(DBCanonicalSKU.display_name_en.ilike(f"%{raw}%"), DBCanonicalSKU.brand.ilike(f"%{raw}%")),
                ).limit(1))
            return result.scalars().first()
    except Exception as e:
        logger.warning("Failed to infer substitute category from catalog: %s", e)
    return None


def _category_hint_from_text(text: str) -> str | None:
    lowered = text.lower()
    for token, category in SUBSTITUTE_CATEGORY_HINTS.items():
        if token in lowered:
            return category
    return None


def _requested_text_for_substitutes(intent: ParsedIntent) -> str:
    item_texts = [i.text.strip() for i in intent.items if i.text.strip()]
    return ", ".join(item_texts) if item_texts else intent.raw_text.strip() or "aa item"


def _render_pending_cart_summary(context: dict) -> str:
    cart = context.get("resolved_cart") or {}
    items = cart.get("items") or []
    if not items:
        return "Mee cart pending undi. Confirm chey-yana? (avunu / vaddu)"
    names = ", ".join(str(i.get("display_name", "item")).strip() for i in items if isinstance(i, dict))
    total = cart.get("quote_total_inr")
    if total is None:
        return f"Mee cart lo: {names}. Confirm chey-yana? (avunu / vaddu)"
    return f"Mee cart lo: {names}. Total ~₹{total}. Confirm chey-yana? (avunu / vaddu)"


def _preview_to_dict(option: SkuPreview) -> dict:
    return {"canonical_key": option.canonical_key, "display_name": option.display_name,
            "brand": option.brand, "pack_size_label": option.pack_size_label,
            "price_inr": option.price_inr, "in_stock": option.in_stock,
            "provider_specific_id": option.provider_specific_id,
            "category": option.category, "subcategory": option.subcategory,
            "unit": option.unit, "pack_quantity": option.pack_quantity, "eta_min": option.eta_min}


def _preview_from_dict(data: dict) -> SkuPreview:
    return SkuPreview(**data)


def _canonical_from_preview(option: SkuPreview) -> CanonicalSKU:
    return CanonicalSKU(
        canonical_key=option.canonical_key, display_name=option.display_name,
        display_names_local={}, category=option.category, subcategory=option.subcategory,
        brand=option.brand, pack_size=option.pack_size_label, unit=option.unit,
        pack_quantity=option.pack_quantity, estimated_price_inr=option.price_inr,
        typical_price_band_min_inr=option.price_inr, typical_price_band_max_inr=option.price_inr,
        image_url=None, provider_specific_id=option.provider_specific_id,
        provider=ProviderName.SWIGGY_INSTAMART, in_stock=option.in_stock,
        delivery_eta_min=option.eta_min or 18)


def _cart_data_from_quote(quote, cart) -> dict:
    in_stock = [li for li in quote.line_items if li.in_stock]
    return {
        "items": [{"canonical_key": li.canonical_key, "display_name": li.display_name,
                    "brand": li.brand, "price_inr": li.unit_price_inr, "quantity": li.qty}
                   for li in in_stock],
        "quote_total_inr": quote.total_inr, "delivery_fee_inr": quote.delivery_fee_inr,
        "provider": cart.provider.value if hasattr(cart.provider, 'value') else cart.provider,
        "provider_cart_id": cart.provider_cart_id,
    }


def _cart_data_from_discovery_option(option: DiscoveryOption) -> dict:
    provider = {"food": "swiggy_food", "instamart": "swiggy_instamart", "dineout": "swiggy_dineout"}[option.source]
    return {
        "flow": "discovery", "source": option.source, "provider": provider,
        "provider_cart_id": option.provider_id, "quote_total_inr": option.estimated_total_inr,
        "items": [{"canonical_key": option.option_id, "display_name": option.title,
                    "brand": option.subtitle, "price_inr": option.estimated_total_inr,
                    "quantity": option.action_payload.get("quantity", 1)}],
        "selected_option": option.model_dump(),
    }


def _location_to_dict(location: Location) -> dict:
    return {"latitude": location.latitude, "longitude": location.longitude,
            "pincode": location.pincode, "city": location.city,
            "address_line": location.address_line, "landmark": location.landmark}


def _location_from_context(context: dict) -> Location | None:
    raw = context.get("location")
    if not raw:
        return None
    return Location(latitude=float(raw["latitude"]), longitude=float(raw["longitude"]),
                    pincode=raw.get("pincode", "500032"), city=raw.get("city", "Hyderabad"),
                    address_line=raw.get("address_line"), landmark=raw.get("landmark"))


async def _rehydrate_recent_pending_session(csm: ConversationStateMachine, user_id: str) -> dict | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    try:
        async with get_session() as session:
            result = await session.execute(
                select(VoiceSession).where(VoiceSession.ordering_user_id == uuid.UUID(user_id))
                .where(VoiceSession.outcome == "still_pending")
                .where(VoiceSession.updated_at > cutoff)
                .order_by(VoiceSession.updated_at.desc()).limit(1))
            latest = result.scalars().first()
    except Exception as e:
        logger.warning("Skipping rehydration for user %s: %s", user_id[:8], e)
        return None
    if latest is None:
        return None
    restored_state = latest.conversation_state or ConversationState.AWAITING_CONFIRMATION.value
    if restored_state not in {"PARSING", "AWAITING_CONFIRMATION", "AWAITING_APPROVAL", "EXECUTING"}:
        return None
    parsed_intent = latest.parsed_intent or {}
    resolved_cart = latest.resolved_cart or {}
    confirmation_text = (parsed_intent.get("clarification_question")
                         or resolved_cart.get("confirmation_text")
                         or "Mee previous request pending undi. Confirm cheyyana, leda details malli cheppandi?")
    flow = "awaiting_assistant" if parsed_intent.get("needs_clarification") else None
    if flow is None and isinstance(resolved_cart, dict):
        if resolved_cart.get("flow") in {"discovery", "discovery_selected"}:
            flow = resolved_cart.get("flow")
        elif resolved_cart:
            flow = "pending_order"
    prior_turn = int((resolved_cart or {}).get("_turn_count", 1)) if isinstance(resolved_cart, dict) else 1
    context = {
        "voice_session_id": str(latest.id), "parsed_intent": parsed_intent,
        "resolved_cart": {k: v for k, v in (resolved_cart or {}).items() if k != "_turn_count"} if isinstance(resolved_cart, dict) else {},
        "confirmation_text": confirmation_text, "flow": flow,
        "language": parsed_intent.get("language_detected", "te-IN"), "rehydrated_from_db": True,
    }
    await csm.restore_session(ordering_user_id=user_id, voice_session_id=str(latest.id),
                               state=restored_state, context=context, turn_count=max(2, prior_turn),
                               started_at=latest.created_at.isoformat() if latest.created_at else None,
                               last_turn_at=latest.updated_at.isoformat() if latest.updated_at else None)
    logger.info("Rehydrated pending session %s for user %s from Postgres", latest.id, user_id[:8])
    return await csm.current_state(user_id)
