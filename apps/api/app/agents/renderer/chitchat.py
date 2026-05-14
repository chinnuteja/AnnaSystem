from __future__ import annotations

from redis.asyncio import Redis

from packages.providers.interface import Location
from packages.providers.router import provider_router


IDLE_VARIANTS = [
    "Haan, cheppandi — emi cheyyali?",
    "Sare, order cheyyala, options chudala, track cheyyala?",
    "Cheppandi, groceries, food delivery, dineout — edhi kavali?",
]

IDLE_VARIANTS_EN = [
    "Yes, tell me — what should I help with?",
    "Sure, do you want to order, compare options, or track something?",
    "Tell me what you need: groceries, food delivery, or dineout?",
]

MID_ORDER_VARIANTS = [
    "Mee order pending undi — confirm cheyyana, leda venaki vellali?",
    "Cart ready ga undi — avunu ante proceed chestha, vaddu ante cancel chestha.",
    "Order hold lo undi — confirm cheyyala?",
]

MID_ORDER_VARIANTS_EN = [
    "Your order is pending — should I confirm it or go back?",
    "Your cart is ready — say yes to proceed or no to cancel.",
    "The order is on hold — do you want to confirm it?",
]

ASSISTANT_VARIANTS = [
    "Cheppandi, em kavali? Groceries / food delivery / dineout?",
    "Mee request ki domain cheppandi — groceries, food delivery, leda dineout?",
    "Ok, mundu type cheppandi: grocery aa food aa dineout aa?",
]

ASSISTANT_VARIANTS_EN = [
    "Tell me what you need: groceries, food delivery, or dineout?",
    "Please choose the type: groceries, food delivery, or dineout.",
    "Okay, first tell me the type: grocery, food, or dineout?",
]


def render_chitchat(conv_ctx: dict | None = None) -> str:
    state = (conv_ctx or {}).get("state")
    context = (conv_ctx or {}).get("context") or {}
    turn_count = int((conv_ctx or {}).get("turn_count") or 0)

    if not conv_ctx or turn_count <= 1:
        return "Namaskaram! foodleaf lo text or voice tho order cheyyachu. Emi kavali?"

    if state == "AWAITING_CONFIRMATION" and context.get("flow") == "awaiting_assistant":
        return "Cheppandi, em kavali? Groceries (atta, milk) / food delivery (biryani) / dineout?"

    if state == "AWAITING_CONFIRMATION" and (
        context.get("resolved_cart")
        or context.get("quote_total_inr")
        or context.get("confirmation_text")
        or context.get("flow") in {"discovery", "discovery_selected"}
    ):
        return "Mee order pending undi — confirm cheyyana, leda venaki vellali?"

    return "Haan, cheppandi — emi cheyyali?"


async def render_chitchat_rotating(
    redis: Redis,
    *,
    conv_ctx: dict | None = None,
    user_id: str | None = None,
    language: str = "te-IN",
    location: Location | None = None,
) -> str:
    context_tag = _context_tag(conv_ctx)
    variants = await _variants_for_context(context_tag, language=language, location=location)
    key = f"chitchat:rotation:{user_id or 'anonymous'}:{context_tag}"
    count = await redis.incr(key)
    return variants[(count - 1) % len(variants)]


async def _variants_for_context(
    context_tag: str,
    *,
    language: str,
    location: Location | None,
) -> list[str]:
    if context_tag == "mid_order":
        return MID_ORDER_VARIANTS_EN if language == "en-IN" else MID_ORDER_VARIANTS
    if context_tag == "awaiting_assistant":
        return ASSISTANT_VARIANTS_EN if language == "en-IN" else ASSISTANT_VARIANTS

    idle_variants = IDLE_VARIANTS_EN if language == "en-IN" else IDLE_VARIANTS
    offer = await _catalog_offer_phrase(location, language)
    if offer:
        if language == "en-IN":
            return [*idle_variants, f"Also, {offer} is available now."]
        return [*idle_variants, f"Inka {offer} available undi."]
    return idle_variants


def _context_tag(conv_ctx: dict | None) -> str:
    state = (conv_ctx or {}).get("state")
    context = (conv_ctx or {}).get("context") or {}
    if state == "AWAITING_CONFIRMATION" and context.get("flow") == "awaiting_assistant":
        return "awaiting_assistant"
    if state == "AWAITING_CONFIRMATION" and (
        context.get("resolved_cart")
        or context.get("quote_total_inr")
        or context.get("confirmation_text")
        or context.get("flow") in {"discovery", "discovery_selected"}
    ):
        return "mid_order"
    return "idle"


async def _catalog_offer_phrase(location: Location | None, language: str) -> str | None:
    if location is None:
        location = Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad")
    try:
        restaurants = await provider_router.food().search_restaurants(
            query_text=None,
            cuisine_filter=None,
            location=location,
            only_with_offers=True,
            limit=1,
        )
    except Exception:
        return None
    if not restaurants or not restaurants[0].offer_text:
        return None
    if language == "en-IN":
        return f"{restaurants[0].name} has {restaurants[0].offer_text}"
    return f"{restaurants[0].name} lo {restaurants[0].offer_text}"
