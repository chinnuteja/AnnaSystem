"""
MockSwiggyAdapter — implements IGroceryProvider, IFoodProvider, IDineoutProvider
against static JSON catalogs.

Behavior:
- Realistic latency (50-300ms per call) so latency budget testing is meaningful
- Deterministic failures (~3% rate) so retry/circuit-breaker logic is exercised
- Stateful in-memory cart and order tracking
- Telugu/Hindi term matching via display_names_local
- Deterministic order IDs (uuid-style) so logs are debuggable

Swap-in plan:
- Day 1 to Day N: agents/* call MockSwiggyAdapter
- When real Swiggy MCP keys arrive: implement SwiggyAdapter against real MCP
- One-line config change in providers/router.py — swap "mock" → "real"
- All agent code stays unchanged
"""

from __future__ import annotations
import json
import asyncio
import math
import random
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from packages.providers.interface import (
    Location, CanonicalSKU, CartItem, CartHandle, CartLine, AvailabilityResult,
    QuoteResult, CustomerProfile, PaymentRef, OrderResult, OrderStatus,
    OrderStatusEnum, CancellationResult, ProviderName,
    Restaurant, MenuItem, DineoutRestaurant, DineoutSlot, DineoutBooking,
)


# ============================================================================
# Catalog Loading
# ============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"

def _load_json(filename: str) -> dict:
    with open(DATA_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)

_INSTAMART = _load_json("instamart_catalog.json")
_FOOD = _load_json("food_catalog.json")
_DINEOUT = _load_json("dineout_catalog.json")


# ============================================================================
# Helpers
# ============================================================================

async def _simulate_latency(min_ms: int = 50, max_ms: int = 300):
    """Realistic API latency."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _maybe_fail(failure_rate: float = 0.03):
    """Deterministic failure injection. Configure failure_rate=0.0 to disable."""
    if random.random() < failure_rate:
        raise RuntimeError("MOCK_PROVIDER_TRANSIENT_FAILURE: simulated upstream error")


def _new_id(prefix: str = "MOCK") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _distance_km(origin: Location, destination: dict) -> float:
    lat1 = math.radians(float(origin.latitude))
    lon1 = math.radians(float(origin.longitude))
    lat2 = math.radians(float(destination["latitude"]))
    lon2 = math.radians(float(destination["longitude"]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(6371.0 * c, 1)


def _matches_query(sku_data: dict, query_text: str, language: str) -> tuple[bool, float]:
    """Simple text match across canonical name, brand, category, and language-specific terms.
    Returns (matched, score 0.0-1.0)."""
    q = query_text.lower().strip()
    if not q:
        return (False, 0.0)
    q_normalized = re.sub(r"\b\d+(?:\.\d+)?\s*(kg|g|gm|gram|grams|l|ml)\b", " ", q)
    q_normalized = re.sub(r"[^a-z0-9\u0C00-\u0C7F]+", " ", q_normalized)
    q_words = {w.rstrip("s") for w in q_normalized.split() if len(w) >= 2}
    searchable = " ".join(
        [
            sku_data.get("canonical_key", ""),
            sku_data.get("display_name", ""),
            sku_data.get("brand", ""),
            sku_data.get("category", ""),
            sku_data.get("subcategory", ""),
            " ".join(sku_data.get("display_names_local", {}).get(language, [])),
        ]
    ).lower()
    searchable = re.sub(r"[^a-z0-9\u0C00-\u0C7F]+", " ", searchable)
    searchable_words = {w.rstrip("s") for w in searchable.split() if len(w) >= 2}

    # Exact match against display_name
    if q in sku_data["display_name"].lower():
        return (True, 1.0)
    if q_words and q_words.issubset(searchable_words):
        return (True, 0.92)

    # Match against brand
    if q in sku_data.get("brand", "").lower():
        return (True, 0.9)

    # Match against language-specific terms
    local_names = sku_data.get("display_names_local", {}).get(language, [])
    for term in local_names:
        if q in term.lower() or term.lower() in q:
            return (True, 0.95)

    # Match against category words
    if q in sku_data.get("category", "").lower() or q in sku_data.get("subcategory", "").lower():
        return (True, 0.6)

    return (False, 0.0)


def _to_canonical_sku(sku_data: dict) -> CanonicalSKU:
    return CanonicalSKU(
        canonical_key=sku_data["canonical_key"],
        display_name=sku_data["display_name"],
        display_names_local=sku_data["display_names_local"],
        category=sku_data["category"],
        subcategory=sku_data["subcategory"],
        brand=sku_data["brand"],
        pack_size=sku_data["pack_size"],
        unit=sku_data["unit"],
        pack_quantity=sku_data["pack_quantity"],
        estimated_price_inr=sku_data["estimated_price_inr"],
        typical_price_band_min_inr=sku_data["typical_price_band_min_inr"],
        typical_price_band_max_inr=sku_data["typical_price_band_max_inr"],
        image_url=sku_data.get("image_url"),
        provider_specific_id=sku_data["provider_specific_id"],
        provider=ProviderName(sku_data["provider"]),
        in_stock=sku_data["in_stock"],
        delivery_eta_min=sku_data.get("delivery_eta_min"),
    )


# In-memory state for stateful operations (carts, orders)
_CARTS: dict[str, CartHandle] = {}
_ORDERS: dict[str, dict] = {}        # provider_order_id -> {status, customer, etc}
_DINEOUT_BOOKINGS: dict[str, DineoutBooking] = {}


# ============================================================================
# IGroceryProvider — Mock Instamart
# ============================================================================

class MockSwiggyInstamartAdapter:
    name = ProviderName.SWIGGY_INSTAMART

    async def search_skus(
        self, query_text: str, language: str, location: Location, limit: int = 5
    ) -> list[CanonicalSKU]:
        await _simulate_latency(80, 250)
        _maybe_fail()

        scored = []
        for sku in _INSTAMART["skus"]:
            matched, score = _matches_query(sku, query_text, language)
            if matched:
                scored.append((score, 1 if sku["in_stock"] else 0, sku))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [_to_canonical_sku(s) for _, _, s in scored[:limit]]

    async def check_availability(
        self, sku_ids: list[str], location: Location
    ) -> dict[str, AvailabilityResult]:
        await _simulate_latency(60, 200)
        _maybe_fail()

        result = {}
        for sku in _INSTAMART["skus"]:
            if sku["provider_specific_id"] in sku_ids or sku["canonical_key"] in sku_ids:
                key = sku["provider_specific_id"]
                result[key] = AvailabilityResult(
                    sku_id=key,
                    available=sku["in_stock"],
                    current_price_inr=sku["estimated_price_inr"],
                    pack_size_available=[sku["pack_size"]],
                    delivery_eta_min=sku.get("delivery_eta_min"),
                    reason_if_unavailable=None if sku["in_stock"] else "out_of_stock",
                )
        return result

    async def assemble_cart(
        self, items: list[CartItem], location: Location
    ) -> CartHandle:
        await _simulate_latency(100, 200)
        _maybe_fail()

        cart_id = _new_id("CART")
        handle = CartHandle(
            provider=ProviderName.SWIGGY_INSTAMART,
            provider_cart_id=cart_id,
            items=items,
            expires_at=datetime.utcnow() + timedelta(minutes=30),
        )
        _CARTS[cart_id] = handle
        return handle

    async def quote_cart(self, cart: CartHandle) -> QuoteResult:
        await _simulate_latency(80, 180)
        _maybe_fail()

        line_items = []
        for item in cart.items:
            sku = item.canonical_sku
            line_total = sku.estimated_price_inr * item.quantity
            line_items.append(
                CartLine(
                    canonical_key=sku.canonical_key,
                    display_name=sku.display_name,
                    brand=sku.brand,
                    pack_size_label=sku.pack_size,
                    qty=item.quantity,
                    unit_price_inr=sku.estimated_price_inr,
                    line_total_inr=line_total,
                    in_stock=sku.in_stock,
                    eta_min=sku.delivery_eta_min,
                    requested_size_label=item.notes,
                    category=sku.category,
                    substitutes=item.substitutes,
                )
            )

        subtotal = sum(li.line_total_inr for li in line_items if li.in_stock)
        delivery_fee = 25 if 0 < subtotal < 250 else 0
        handling_fee = 8 if subtotal > 0 else 0
        # Crude offer logic
        applied_offers = []
        discount = 0
        if subtotal >= 500:
            discount = int(subtotal * 0.05)
            applied_offers.append("5% off above ₹500")
        taxes = int((subtotal - discount) * 0.05)
        total = subtotal + delivery_fee + handling_fee + taxes - discount
        etas = [li.eta_min for li in line_items if li.eta_min]
        eta_min = min(etas, default=15)
        eta_max = max(etas, default=25) + 5

        return QuoteResult(
            cart_handle=cart,
            subtotal_inr=subtotal,
            delivery_fee_inr=delivery_fee,
            handling_fee_inr=handling_fee,
            taxes_inr=taxes,
            discount_inr=discount,
            applied_offers=applied_offers,
            total_inr=total,
            estimated_delivery_min=eta_min,
            estimated_delivery_max=eta_max,
            line_items=line_items,
        )

    async def execute_checkout(
        self, cart: CartHandle, payment_ref: PaymentRef, customer: CustomerProfile
    ) -> OrderResult:
        await _simulate_latency(200, 500)
        _maybe_fail(failure_rate=0.05)  # Slightly higher failure rate at checkout

        quote = await self.quote_cart(cart)
        if payment_ref.amount_paid_inr < quote.total_inr:
            return OrderResult(
                success=False, provider_order_id=None, final_total_inr=None,
                estimated_delivery_at=None,
                failure_code="payment_amount_mismatch",
                failure_reason=f"Payment ₹{payment_ref.amount_paid_inr} < quote ₹{quote.total_inr}",
            )

        order_id = _new_id("INST-ORD")
        eta = datetime.utcnow() + timedelta(minutes=20)
        _ORDERS[order_id] = {
            "status": OrderStatusEnum.CONFIRMED,
            "customer": customer,
            "cart": cart,
            "placed_at": datetime.utcnow(),
            "eta": eta,
            "rider_name": None,
            "rider_phone": None,
            "delivered_at": None,
        }

        return OrderResult(
            success=True, provider_order_id=order_id,
            final_total_inr=quote.total_inr,
            estimated_delivery_at=eta,
            failure_code=None, failure_reason=None,
        )

    async def track_order(self, provider_order_id: str) -> OrderStatus:
        await _simulate_latency(40, 120)

        if provider_order_id not in _ORDERS:
            raise ValueError(f"Order {provider_order_id} not found")

        order = _ORDERS[provider_order_id]
        # Auto-progress status based on time elapsed
        elapsed_min = (datetime.utcnow() - order["placed_at"]).total_seconds() / 60
        if elapsed_min < 5:
            status = OrderStatusEnum.CONFIRMED
        elif elapsed_min < 12:
            status = OrderStatusEnum.PREPARING
            order["rider_name"] = "Ramesh K"
            order["rider_phone"] = "+919876543210"
        elif elapsed_min < 22:
            status = OrderStatusEnum.OUT_FOR_DELIVERY
        else:
            status = OrderStatusEnum.DELIVERED
            if not order["delivered_at"]:
                order["delivered_at"] = datetime.utcnow()
        order["status"] = status

        return OrderStatus(
            provider_order_id=provider_order_id,
            status=status,
            rider_name=order.get("rider_name"),
            rider_phone=order.get("rider_phone"),
            rider_lat=17.4486 if status == OrderStatusEnum.OUT_FOR_DELIVERY else None,
            rider_lng=78.3792 if status == OrderStatusEnum.OUT_FOR_DELIVERY else None,
            eta_minutes=max(0, int(22 - elapsed_min)),
            last_updated_at=datetime.utcnow(),
            delivered_at=order.get("delivered_at"),
        )

    async def cancel_order(self, provider_order_id: str) -> CancellationResult:
        await _simulate_latency(100, 250)

        if provider_order_id not in _ORDERS:
            return CancellationResult(success=False, refund_amount_inr=0, refund_eta_hours=0,
                                      cancellation_fee_inr=0, failure_reason="order_not_found")
        order = _ORDERS[provider_order_id]
        if order["status"] in (OrderStatusEnum.OUT_FOR_DELIVERY, OrderStatusEnum.DELIVERED):
            return CancellationResult(success=False, refund_amount_inr=0, refund_eta_hours=0,
                                      cancellation_fee_inr=0,
                                      failure_reason="order_already_dispatched")
        order["status"] = OrderStatusEnum.CANCELLED
        # Refund full amount; no fee
        cart = order["cart"]
        # Crude total
        return CancellationResult(success=True, refund_amount_inr=500, refund_eta_hours=2,
                                  cancellation_fee_inr=0, failure_reason=None)


# ============================================================================
# IFoodProvider — Mock Swiggy Food
# ============================================================================

class MockSwiggyFoodAdapter:
    name = ProviderName.SWIGGY_FOOD

    async def search_restaurants(
        self, query_text: Optional[str], cuisine_filter: Optional[list[str]],
        location: Location, max_delivery_min: Optional[int] = None,
        only_with_offers: bool = False, limit: int = 10
    ) -> list[Restaurant]:
        await _simulate_latency(120, 300)
        _maybe_fail()

        results = []
        for r in _FOOD["restaurants"]:
            # Cuisine filter
            if cuisine_filter:
                if not any(c.lower() in [rc.lower() for rc in r["cuisines"]] for c in cuisine_filter):
                    continue
            # Text query against name + cuisines
            if query_text:
                q = query_text.lower()
                if (q not in r["name"].lower()
                        and not any(q in c.lower() for c in r["cuisines"])):
                    continue
            # Delivery time filter
            if max_delivery_min and r["delivery_time_max"] > max_delivery_min:
                continue
            # Offers filter
            if only_with_offers and not r["has_offer"]:
                continue
            enriched = {**r, "distance_km": _distance_km(location, r["location"])}
            results.append(enriched)

        # Sort by offer relevance, dynamic distance from shared location, then rating.
        results.sort(key=lambda x: (0 if x["has_offer"] else 1, x["distance_km"], -x["rating"]))

        return [
            Restaurant(
                provider_restaurant_id=r["provider_restaurant_id"],
                name=r["name"],
                cuisines=r["cuisines"],
                rating=r["rating"],
                rating_count=r["rating_count"],
                cost_for_two_inr=r["cost_for_two_inr"],
                delivery_time_min=r["delivery_time_min"],
                delivery_time_max=r["delivery_time_max"],
                distance_km=r["distance_km"],
                image_url=r.get("image_url"),
                has_offer=r["has_offer"],
                offer_text=r.get("offer_text"),
                is_pure_veg=r["is_pure_veg"],
                location=Location(**r["location"]),
            )
            for r in results[:limit]
        ]

    async def get_restaurant_menu(self, restaurant_id: str) -> list[MenuItem]:
        await _simulate_latency(80, 180)
        _maybe_fail()

        menu_items = _FOOD["menus"].get(restaurant_id, [])
        return [
            MenuItem(
                provider_menu_item_id=m["provider_menu_item_id"],
                restaurant_id=restaurant_id,
                name=m["name"],
                description=m.get("description"),
                price_inr=m["price_inr"],
                is_veg=m["is_veg"],
                is_bestseller=m.get("is_bestseller", False),
                image_url=m.get("image_url"),
                category=m.get("category", "Main"),
            )
            for m in menu_items
        ]

    async def assemble_food_cart(
        self, restaurant_id: str, items: list[tuple[str, int]], location: Location
    ) -> CartHandle:
        await _simulate_latency(100, 200)
        _maybe_fail()

        cart_id = _new_id("FOOD-CART")
        cart_items = []
        menu = _FOOD["menus"].get(restaurant_id, [])
        for menu_item_id, qty in items:
            menu_item = next((m for m in menu if m["provider_menu_item_id"] == menu_item_id), None)
            if menu_item:
                # Wrap as a "fake" CanonicalSKU for unified cart handling
                fake_sku = CanonicalSKU(
                    canonical_key=f"food_{menu_item_id}",
                    display_name=menu_item["name"],
                    display_names_local={},
                    category="food_item",
                    subcategory=menu_item.get("category", ""),
                    brand=restaurant_id,
                    pack_size="1 portion",
                    unit="piece",
                    pack_quantity=1.0,
                    estimated_price_inr=menu_item["price_inr"],
                    typical_price_band_min_inr=menu_item["price_inr"],
                    typical_price_band_max_inr=menu_item["price_inr"],
                    image_url=None,
                    provider_specific_id=menu_item_id,
                    provider=ProviderName.SWIGGY_FOOD,
                    in_stock=True,
                    delivery_eta_min=None,
                )
                cart_items.append(CartItem(canonical_sku=fake_sku, quantity=qty))

        handle = CartHandle(
            provider=ProviderName.SWIGGY_FOOD,
            provider_cart_id=cart_id,
            items=cart_items,
            expires_at=datetime.utcnow() + timedelta(minutes=15),
        )
        _CARTS[cart_id] = handle
        return handle

    async def quote_food_cart(self, cart: CartHandle) -> QuoteResult:
        await _simulate_latency(80, 180)
        _maybe_fail()

        line_items = []
        for item in cart.items:
            sku = item.canonical_sku
            line_total = sku.estimated_price_inr * item.quantity
            line_items.append(
                CartLine(
                    canonical_key=sku.canonical_key,
                    display_name=sku.display_name,
                    brand=sku.brand,
                    pack_size_label=sku.pack_size,
                    qty=item.quantity,
                    unit_price_inr=sku.estimated_price_inr,
                    line_total_inr=line_total,
                    in_stock=sku.in_stock,
                    eta_min=None,
                    requested_size_label=item.notes,
                    category=sku.category,
                )
            )

        subtotal = sum(item.canonical_sku.estimated_price_inr * item.quantity for item in cart.items)
        delivery_fee = 35 if subtotal < 350 else 0
        # Restaurant offer simulation: 30% off if cart >= ₹400
        applied_offers = []
        discount = 0
        if subtotal >= 400:
            discount = min(int(subtotal * 0.30), 120)
            applied_offers.append(f"30% off up to ₹{discount}")
        handling_fee = 12
        taxes = int((subtotal - discount) * 0.05)
        total = subtotal + delivery_fee + handling_fee + taxes - discount

        return QuoteResult(
            cart_handle=cart,
            subtotal_inr=subtotal,
            delivery_fee_inr=delivery_fee,
            handling_fee_inr=handling_fee,
            taxes_inr=taxes,
            discount_inr=discount,
            applied_offers=applied_offers,
            total_inr=total,
            estimated_delivery_min=28,
            estimated_delivery_max=42,
            line_items=line_items,
        )

    async def place_food_order(
        self, cart: CartHandle, payment_ref: PaymentRef, customer: CustomerProfile
    ) -> OrderResult:
        await _simulate_latency(200, 500)
        _maybe_fail(failure_rate=0.05)

        quote = await self.quote_food_cart(cart)
        if payment_ref.amount_paid_inr < quote.total_inr:
            return OrderResult(success=False, provider_order_id=None, final_total_inr=None,
                               estimated_delivery_at=None,
                               failure_code="payment_amount_mismatch",
                               failure_reason="Insufficient payment amount")

        order_id = _new_id("FOOD-ORD")
        eta = datetime.utcnow() + timedelta(minutes=35)
        _ORDERS[order_id] = {
            "status": OrderStatusEnum.CONFIRMED,
            "customer": customer,
            "cart": cart,
            "placed_at": datetime.utcnow(),
            "eta": eta,
            "rider_name": None,
            "rider_phone": None,
            "delivered_at": None,
        }
        return OrderResult(success=True, provider_order_id=order_id,
                           final_total_inr=quote.total_inr,
                           estimated_delivery_at=eta,
                           failure_code=None, failure_reason=None)

    async def track_food_order(self, provider_order_id: str) -> OrderStatus:
        return await MockSwiggyInstamartAdapter().track_order(provider_order_id)


# ============================================================================
# IDineoutProvider — Mock Swiggy Dineout
# ============================================================================

class MockSwiggyDineoutAdapter:
    name = ProviderName.SWIGGY_DINEOUT

    async def search_dineout(
        self, query_text: Optional[str], cuisine_filter: Optional[list[str]],
        location: Location, only_with_deals: bool = False, limit: int = 10
    ) -> list[DineoutRestaurant]:
        await _simulate_latency(120, 300)
        _maybe_fail()

        results = []
        for r in _DINEOUT["restaurants"]:
            if cuisine_filter:
                if not any(c.lower() in [rc.lower() for rc in r["cuisines"]] for c in cuisine_filter):
                    continue
            if query_text:
                q = query_text.lower()
                if (q not in r["name"].lower()
                        and not any(q in c.lower() for c in r["cuisines"])):
                    continue
            if only_with_deals and not r["active_deals"]:
                continue
            enriched = {**r, "distance_km": _distance_km(location, r["location"])}
            results.append(enriched)

        # Sort by deal relevance, dynamic distance from shared location, then rating.
        results.sort(key=lambda x: (0 if x["active_deals"] else 1, x["distance_km"], -x["rating"]))

        return [
            DineoutRestaurant(
                provider_restaurant_id=r["provider_restaurant_id"],
                name=r["name"],
                cuisines=r["cuisines"],
                rating=r["rating"],
                cost_for_two_inr=r["cost_for_two_inr"],
                distance_km=r["distance_km"],
                image_url=r.get("image_url"),
                active_deals=r["active_deals"],
                location=Location(**r["location"]),
            )
            for r in results[:limit]
        ]

    async def get_available_slots(
        self, restaurant_id: str, date: str, party_size: int
    ) -> list[DineoutSlot]:
        await _simulate_latency(80, 200)
        _maybe_fail()

        # Generate deterministic slots
        slots_template = ["12:00", "12:30", "13:00", "13:30", "19:00", "19:30", "20:00", "20:30", "21:00"]
        # Use hash of (restaurant_id + date) to determine which slots are available
        seed = sum(ord(c) for c in (restaurant_id + date))
        random.seed(seed)
        out = []
        for t in slots_template:
            available = random.random() > 0.3
            slot_id = f"SLOT-{restaurant_id}-{date.replace('-', '')}-{t.replace(':', '')}"
            deal = None
            restaurant = next((r for r in _DINEOUT["restaurants"] if r["provider_restaurant_id"] == restaurant_id), None)
            if restaurant and restaurant["active_deals"]:
                deal = restaurant["active_deals"][0]
            out.append(DineoutSlot(
                slot_id=slot_id, restaurant_id=restaurant_id, date=date,
                time=t, party_size=party_size, available=available,
                deal_applicable=deal if available else None,
            ))
        random.seed()
        return out

    async def book_table(
        self, slot_id: str, party_size: int, customer: CustomerProfile,
        special_requests: Optional[str] = None
    ) -> DineoutBooking:
        await _simulate_latency(200, 500)
        _maybe_fail(failure_rate=0.04)

        # Parse slot_id to extract restaurant + date + time
        # Format: SLOT-{restaurant_id}-{YYYYMMDD}-{HHMM}
        parts = slot_id.split("-")
        restaurant_id = "-".join(parts[1:-2])
        date_str = parts[-2]
        time_str = parts[-1]
        date_formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        time_formatted = f"{time_str[:2]}:{time_str[2:4]}"

        booking_id = _new_id("DINE-BOOK")
        booking_code = f"FOODLEAF-DINE-{uuid.uuid4().hex[:4].upper()}"
        restaurant = next((r for r in _DINEOUT["restaurants"] if r["provider_restaurant_id"] == restaurant_id), None)
        deal = restaurant["active_deals"][0] if restaurant and restaurant["active_deals"] else None

        booking = DineoutBooking(
            booking_id=booking_id, restaurant_id=restaurant_id,
            confirmed=True, booking_code=booking_code,
            date=date_formatted, time=time_formatted, party_size=party_size,
            deal_applied=deal,
        )
        _DINEOUT_BOOKINGS[booking_id] = booking
        return booking

    async def get_booking_status(self, booking_id: str) -> DineoutBooking:
        await _simulate_latency(50, 150)
        if booking_id not in _DINEOUT_BOOKINGS:
            raise ValueError(f"Booking {booking_id} not found")
        return _DINEOUT_BOOKINGS[booking_id]


# ============================================================================
# Convenience: a unified factory the agent code uses
# ============================================================================

def get_grocery_provider() -> MockSwiggyInstamartAdapter:
    return MockSwiggyInstamartAdapter()

def get_food_provider() -> MockSwiggyFoodAdapter:
    return MockSwiggyFoodAdapter()

def get_dineout_provider() -> MockSwiggyDineoutAdapter:
    return MockSwiggyDineoutAdapter()
