from __future__ import annotations

from fastapi import APIRouter

from app.agents.confirmation import build_confirmation
from app.agents.discovery import discover_options, format_discovery_reply
from app.agents.message_parser import parse_text_message
from app.agents.renderer import render_cart_confirmation, render_intent_reply
from app.agents.sku_mapper import resolve_and_quote
from app.schemas.message import (
    CandidateItem,
    MvpMessageRequest,
    MvpMessageResponse,
    QuoteSummary,
    TextMessageRequest,
    TextMessageResponse,
)
from packages.providers.interface import CartItem
from packages.providers.interface import Location
from packages.providers.router import provider_router


router = APIRouter()


@router.get("/health")
async def health():
    return {"ok": True, "service": "foodleaf-api"}


@router.post("/api/dev/text-message", response_model=TextMessageResponse)
async def dev_text_message(payload: TextMessageRequest):
    intent = await parse_text_message(payload.text, payload.language)
    location = Location(
        latitude=payload.latitude,
        longitude=payload.longitude,
        pincode=payload.pincode,
        city=payload.city,
    )

    if intent.action == "DISCOVER":
        discovery = await discover_options(intent, location)
        return TextMessageResponse(
            parsed_intent=intent,
            candidates=[],
            quote=None,
            confirmation_text=format_discovery_reply(discovery),
            discovery_result=discovery,
        )

    candidates, _cart, quote = await resolve_and_quote(intent, location)
    confirmation = build_confirmation(intent, candidates, quote)
    quote_summary = None
    if quote is not None:
        quote_summary = QuoteSummary(
            subtotal_inr=quote.subtotal_inr,
            delivery_fee_inr=quote.delivery_fee_inr,
            handling_fee_inr=quote.handling_fee_inr,
            taxes_inr=quote.taxes_inr,
            discount_inr=quote.discount_inr,
            total_inr=quote.total_inr,
            estimated_delivery_min=quote.estimated_delivery_min,
            estimated_delivery_max=quote.estimated_delivery_max,
            applied_offers=quote.applied_offers,
        )

    return TextMessageResponse(
        parsed_intent=intent,
        candidates=candidates,
        quote=quote_summary,
        confirmation_text=confirmation,
    )


@router.post("/api/dev/mvp-message", response_model=MvpMessageResponse)
async def dev_mvp_message(payload: MvpMessageRequest):
    """Stateless WhatsApp-like MVP endpoint for text/voice transcript/location testing.

    This avoids depending on Meta/Gupshup while still exercising the same parser,
    mock discovery providers, mock grocery provider, and Telugu confirmation copy.
    """
    text = (payload.audio_transcript if payload.input_mode == "voice" else payload.text) or ""
    text = text.strip()
    if not text:
        intent = await parse_text_message("location shared", payload.language)
        return MvpMessageResponse(
            parsed_intent=intent,
            reply_text="Text leda voice transcript pampandi. Location matrame unte emi order/discover cheyyalo ardham kaledu.",
            needs_location=False,
        )

    intent = await parse_text_message(text, payload.language)
    intent.input_mode = payload.input_mode

    location = None
    if payload.location is not None:
        location = Location(
            latitude=payload.location.latitude,
            longitude=payload.location.longitude,
            pincode=payload.location.pincode,
            city=payload.location.city,
            address_line=payload.location.address_line,
            landmark=payload.location.landmark,
        )

    if intent.action == "DISCOVER":
        if location is None:
            return MvpMessageResponse(
                parsed_intent=intent,
                reply_text="Mee current location WhatsApp lo share cheyyandi. Appudu nearby offers, dineout, dinner options chusi chepthanu.",
                needs_location=True,
            )
        discovery = await discover_options(intent, location)
        return MvpMessageResponse(
            parsed_intent=intent,
            reply_text=format_discovery_reply(discovery),
            discovery_result=discovery,
            needs_location=False,
        )

    if intent.action != "ORDER":
        return MvpMessageResponse(
            parsed_intent=intent,
            reply_text=render_intent_reply(intent, conv_ctx=None),
        )

    grocery_location = location or Location(
        latitude=17.4486,
        longitude=78.3792,
        pincode="500032",
        city="Hyderabad",
    )
    provider = provider_router.grocery()
    candidates = []
    cart_items = []
    for item in intent.items:
        skus = await provider.search_skus(item.text, payload.language, grocery_location, limit=1)
        if not skus:
            continue
        sku = skus[0]
        candidates.append(
            CandidateItem(
                canonical_key=sku.canonical_key,
                display_name=sku.display_name,
                brand=sku.brand,
                price_inr=sku.estimated_price_inr,
                provider_specific_id=sku.provider_specific_id,
                in_stock=sku.in_stock,
            )
        )
        cart_items.append(CartItem(canonical_sku=sku, quantity=item.quantity or 1))

    if not cart_items:
        return MvpMessageResponse(
            parsed_intent=intent,
            reply_text="Sorry, aa item mock catalog lo dorakaledu. Vere laga cheppandi?",
        )

    cart = await provider.assemble_cart(cart_items, grocery_location)
    quote = await provider.quote_cart(cart)
    quote_summary = QuoteSummary(
        subtotal_inr=quote.subtotal_inr,
        delivery_fee_inr=quote.delivery_fee_inr,
        handling_fee_inr=quote.handling_fee_inr,
        taxes_inr=quote.taxes_inr,
        discount_inr=quote.discount_inr,
        total_inr=quote.total_inr,
        estimated_delivery_min=quote.estimated_delivery_min,
        estimated_delivery_max=quote.estimated_delivery_max,
        applied_offers=quote.applied_offers,
    )
    return MvpMessageResponse(
        parsed_intent=intent,
        candidates=candidates,
        quote=quote_summary,
        reply_text=render_cart_confirmation(
            quote,
            address_label=None,
            language=intent.language_detected or payload.language,
            candidates=candidates,
        ),
    )
