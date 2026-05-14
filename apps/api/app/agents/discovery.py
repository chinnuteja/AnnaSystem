from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.schemas.message import DiscoveryOption, DiscoveryResult, ParsedIntent
from packages.providers.interface import Location
from packages.providers.router import provider_router

logger = logging.getLogger("foodleaf.discovery")
T = TypeVar("T")

MAX_OPTIONS = 8


async def discover_options(
    intent: ParsedIntent,
    location: Location,
    offset: int = 0,
    page_size: int = 3,
) -> DiscoveryResult:
    """Return ranked food, grocery, and dineout options with human-readable reasons."""
    query = intent.raw_text.strip()
    lowered = query.lower()
    all_options: list[DiscoveryOption] = []

    food_options = await _food_options(lowered, location)
    grocery_options = await _grocery_options(lowered, location)
    dineout_options = await _dineout_options(lowered, location)

    all_options.extend(food_options)
    all_options.extend(grocery_options)
    all_options.extend(dineout_options)

    domain_weights = None
    if intent.router_trace and isinstance(intent.router_trace.get("domain_scores"), dict):
        domain_weights = intent.router_trace["domain_scores"]

    ranked = sorted(
        all_options,
        key=lambda o: _rank_key(o, domain_weights, dineout_preferred=_wants_dineout(lowered)),
    )[:MAX_OPTIONS]
    for index, option in enumerate(ranked, start=1):
        option.rank = index
        option.option_id = f"disc-{index}"

    visible = ranked[offset : offset + page_size]
    return DiscoveryResult(
        query=query,
        options=visible,
        offset=offset,
        has_more=offset + page_size < len(ranked),
    )


def format_discovery_reply(result: DiscoveryResult) -> str:
    if not result.options:
        return "Konchem clear ga cheppandi? Dinner, tiffin, veg, fast delivery laanti hint ivvandi."

    lines = ["Mee kosam best options chusanu:"]
    for option in result.options:
        eta = ""
        if option.eta_min and option.eta_max:
            eta = f", {option.eta_min}-{option.eta_max} min"
        offer = f", {option.offer_text}" if option.offer_text else ""
        reason = "; ".join(option.reasoning[:2])
        lines.append(
            f"{option.rank}. {option.title} - approx ₹{option.estimated_total_inr}{eta}{offer}. Why: {reason}."
        )

    lines.append("First one / second one / more options ani reply cheyyandi.")
    return "\n".join(lines)


def format_selected_option_reply(option: DiscoveryOption) -> str:
    eta = ""
    if option.eta_min and option.eta_max:
        eta = f" Delivery approx {option.eta_min}-{option.eta_max} minutes."
    return (
        f"Sare, {option.title} select chesanu. Approx total ₹{option.estimated_total_inr}."
        f"{eta} Confirm chey-yana?"
    )


async def _food_options(query: str, location: Location) -> list[DiscoveryOption]:
    provider = provider_router.food()
    cuisine_filter = _cuisine_filter(query)
    only_offers = _wants_offer_or_budget(query)
    restaurants = await _with_provider_retry(
        lambda: provider.search_restaurants(
            query_text=None,
            cuisine_filter=cuisine_filter,
            location=location,
            max_delivery_min=35 if _wants_fast(query) else None,
            only_with_offers=only_offers,
            limit=6,
        )
    )

    options: list[DiscoveryOption] = []
    for restaurant in restaurants:
        menu = await _with_provider_retry(lambda: provider.get_restaurant_menu(restaurant.provider_restaurant_id))
        if _wants_veg(query):
            menu = [item for item in menu if item.is_veg]
        if not menu:
            continue

        item = sorted(menu, key=lambda m: (not m.is_bestseller, m.price_inr))[0]
        reasons = [
            f"{restaurant.rating} rating",
            f"{restaurant.distance_km} km from shared location",
            f"{restaurant.delivery_time_min}-{restaurant.delivery_time_max} min delivery",
        ]
        if restaurant.has_offer and restaurant.offer_text:
            reasons.append(restaurant.offer_text)
        if restaurant.is_pure_veg:
            reasons.append("pure veg place")

        options.append(
            DiscoveryOption(
                option_id="pending",
                rank=0,
                source="food",
                title=f"{item.name} from {restaurant.name}",
                subtitle=", ".join(restaurant.cuisines),
                provider_id=restaurant.provider_restaurant_id,
                estimated_total_inr=item.price_inr + 45,
                eta_min=restaurant.delivery_time_min,
                eta_max=restaurant.delivery_time_max,
                rating=restaurant.rating,
                offer_text=restaurant.offer_text,
                reasoning=reasons,
                action_payload={
                    "restaurant_id": restaurant.provider_restaurant_id,
                    "menu_item_id": item.provider_menu_item_id,
                    "menu_item_name": item.name,
                    "restaurant_name": restaurant.name,
                    "quantity": 1,
                },
            )
        )
    return options


async def _grocery_options(query: str, location: Location) -> list[DiscoveryOption]:
    provider = provider_router.grocery()
    search_terms = _grocery_search_terms(query)
    options: list[DiscoveryOption] = []

    for term in search_terms:
        try:
            skus = await _with_provider_retry(lambda: provider.search_skus(term, "te-IN", location, limit=1))
        except Exception as e:
            logger.warning("Grocery discovery search failed for %s: %s", term, e)
            continue
        if not skus:
            continue
        sku = skus[0]
        reasons = [
            "home cooking option",
            f"available around {location.city}",
            f"{sku.delivery_eta_min or 20} min grocery delivery",
        ]
        if sku.estimated_price_inr <= 150:
            reasons.append("budget friendly")

        options.append(
            DiscoveryOption(
                option_id="pending",
                rank=0,
                source="instamart",
                title=f"Cook at home: {sku.display_name}",
                subtitle=f"{sku.brand} {sku.pack_size}",
                provider_id=sku.provider_specific_id,
                estimated_total_inr=sku.estimated_price_inr,
                eta_min=sku.delivery_eta_min or 15,
                eta_max=(sku.delivery_eta_min or 15) + 10,
                rating=None,
                offer_text=None,
                reasoning=reasons,
                action_payload={
                    "canonical_key": sku.canonical_key,
                    "provider_sku_id": sku.provider_specific_id,
                    "display_name": sku.display_name,
                    "quantity": 1,
                },
            )
        )
    return options


async def _dineout_options(query: str, location: Location) -> list[DiscoveryOption]:
    if "home" in query or "delivery" in query:
        return []

    provider = provider_router.dineout()
    restaurants = await _with_provider_retry(
        lambda: provider.search_dineout(
            query_text=None,
            cuisine_filter=_cuisine_filter(query),
            location=location,
            only_with_deals=_wants_offer_or_budget(query),
            limit=4,
        )
    )

    options: list[DiscoveryOption] = []
    for restaurant in restaurants:
        deal = restaurant.active_deals[0] if restaurant.active_deals else None
        options.append(
            DiscoveryOption(
                option_id="pending",
                rank=0,
                source="dineout",
                title=f"Dineout: {restaurant.name}",
                subtitle=", ".join(restaurant.cuisines),
                provider_id=restaurant.provider_restaurant_id,
                estimated_total_inr=restaurant.cost_for_two_inr,
                eta_min=None,
                eta_max=None,
                rating=restaurant.rating,
                offer_text=deal,
                reasoning=[
                    f"{restaurant.rating} rating",
                    f"{restaurant.distance_km} km from shared location",
                    deal or "good dine-in option",
                ],
                action_payload={
                    "restaurant_id": restaurant.provider_restaurant_id,
                    "restaurant_name": restaurant.name,
                    "party_size": 2,
                },
            )
        )
    return options


def _rank_key(
    option: DiscoveryOption,
    domain_weights: dict[str, float] | None = None,
    dineout_preferred: bool = False,
) -> tuple:
    eta = option.eta_max if option.eta_max is not None else 60
    offer_bonus = -15 if option.offer_text else 0
    rating_bonus = -(option.rating or 4.0) * 10
    price = option.estimated_total_inr
    retrieval_bias = 0.0
    if domain_weights:
        src = option.source
        if src == "instamart":
            retrieval_bias = -28.0 * float(domain_weights.get("grocery", 0.0))
        elif src == "food":
            retrieval_bias = -28.0 * float(domain_weights.get("food_delivery", 0.0))
        elif src == "dineout":
            retrieval_bias = -28.0 * float(domain_weights.get("dineout", 0.0))
    if dineout_preferred:
        retrieval_bias += -80.0 if option.source == "dineout" else 35.0
    return (eta + offer_bonus + rating_bonus + retrieval_bias, price)


def _cuisine_filter(query: str) -> list[str] | None:
    if any(token in query for token in ("tiffin", "breakfast", "idli", "dosa", "south")):
        return ["South Indian"]
    if "biryani" in query:
        return ["Biryani"]
    if "pizza" in query:
        return ["Pizza"]
    if any(token in query for token in ("north", "paneer", "naan")):
        return ["North Indian"]
    return None


def _grocery_search_terms(query: str) -> list[str]:
    if any(token in query for token in ("breakfast", "tiffin", "idli", "dosa")):
        return ["rava", "milk"]
    if any(token in query for token in ("dinner", "lunch", "meal")):
        return ["rice", "dal", "atta"]
    if any(token in query for token in ("healthy", "light")):
        return ["curd", "fruit"]
    return ["atta", "milk"]


def _wants_fast(query: str) -> bool:
    return any(token in query for token in ("fast", "quick", "twaraga", "jaldi", "urgent"))


def _wants_offer_or_budget(query: str) -> bool:
    return any(token in query for token in ("cheap", "budget", "offer", "discount", "low price"))


def _wants_dineout(query: str) -> bool:
    return any(token in query for token in ("dineout", "dine-in", "dine in", "table", "buffet", "restaurant deal"))


def _wants_veg(query: str) -> bool:
    return any(token in query for token in ("veg", "vegetarian", "pure veg", "no non veg"))


async def _with_provider_retry(fn: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as e:
            last_error = e
            logger.warning("Discovery provider call failed on attempt %s/%s: %s", attempt + 1, attempts, e)
    assert last_error is not None
    raise last_error
