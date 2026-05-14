"""
ICommerceProvider Interface — the contract every commerce adapter must implement.

This is the same interface used by:
- MockSwiggyAdapter (for development before keys arrive)
- SwiggyAdapter (real Swiggy MCP integration)
- Future: ZeptoAdapter, BigBasketAdapter, ZomatoAdapter

The mock and real adapters are swappable. Code in agents/ should never know which is in use.
"""

from __future__ import annotations
from typing import Protocol, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ============================================================================
# Core Domain Types
# ============================================================================

class ProviderName(str, Enum):
    SWIGGY_INSTAMART = "swiggy_instamart"
    SWIGGY_FOOD = "swiggy_food"
    SWIGGY_DINEOUT = "swiggy_dineout"


class OrderType(str, Enum):
    GROCERY = "grocery"
    FOOD_DELIVERY = "food_delivery"
    DINEOUT_RESERVATION = "dineout_reservation"


@dataclass
class Location:
    latitude: float
    longitude: float
    pincode: str
    city: str
    address_line: Optional[str] = None
    landmark: Optional[str] = None


@dataclass
class CanonicalSKU:
    """A grocery SKU normalized across providers."""
    canonical_key: str                  # "aashirvaad_select_atta_5kg"
    display_name: str                   # "Aashirvaad Select Atta 5kg"
    display_names_local: dict[str, list[str]]  # {"te-IN": ["godi pindi", "atta"]}
    category: str                       # "staples_flour"
    subcategory: str                    # "wheat_flour"
    brand: str                          # "aashirvaad"
    pack_size: str                      # "5kg"
    unit: str                           # "kg" / "g" / "L" / "ml" / "piece"
    pack_quantity: float                # 5.0
    estimated_price_inr: int            # Current best estimate
    typical_price_band_min_inr: int
    typical_price_band_max_inr: int
    image_url: Optional[str]
    provider_specific_id: str           # Internal ID for this provider's catalog
    provider: ProviderName
    in_stock: bool
    delivery_eta_min: Optional[int]     # Minutes


@dataclass
class SkuPreview:
    canonical_key: str
    display_name: str
    brand: str
    pack_size_label: str
    price_inr: int
    in_stock: bool
    provider_specific_id: str
    category: str
    subcategory: str = ""
    unit: str = "unit"
    pack_quantity: float = 1.0
    eta_min: Optional[int] = None


@dataclass
class CartItem:
    canonical_sku: CanonicalSKU
    quantity: int
    notes: Optional[str] = None
    substitutes: list[SkuPreview] = field(default_factory=list)


@dataclass
class CartHandle:
    provider: ProviderName
    provider_cart_id: Optional[str]
    items: list[CartItem]
    expires_at: Optional[datetime]


@dataclass
class AvailabilityResult:
    sku_id: str
    available: bool
    current_price_inr: int
    pack_size_available: list[str]      # ["1kg", "5kg", "10kg"] if multiple pack sizes
    delivery_eta_min: Optional[int]
    reason_if_unavailable: Optional[str]  # "out_of_stock" | "not_in_zone" | "discontinued"


@dataclass
class CartLine:
    """Per-line cart data the renderer uses to build itemized confirmations."""
    canonical_key: str
    display_name: str                   # "Aashirvaad Select Atta"
    brand: str                          # "aashirvaad"
    pack_size_label: str                # "5kg" — what the catalog actually carries
    qty: int
    unit_price_inr: int
    line_total_inr: int
    in_stock: bool
    eta_min: Optional[int]              # Per-SKU ETA from catalog
    requested_size_label: Optional[str] = None   # User's ask if it differed (e.g., "100g")
    category: Optional[str] = None      # For substitute lookup if OOS
    substitutes: list[SkuPreview] = field(default_factory=list)


@dataclass
class QuoteResult:
    cart_handle: CartHandle
    subtotal_inr: int
    delivery_fee_inr: int
    handling_fee_inr: int
    taxes_inr: int
    discount_inr: int
    applied_offers: list[str]           # Human-readable list of offers applied
    total_inr: int
    estimated_delivery_min: int
    estimated_delivery_max: int
    line_items: list[CartLine] = None   # Per-line breakdown for the renderer

    def __post_init__(self):
        if self.line_items is None:
            self.line_items = []


@dataclass
class CustomerProfile:
    user_id: str
    name: str
    phone_e164: str
    delivery_location: Location
    preferred_language: str             # "te-IN"


@dataclass
class PaymentRef:
    """Reference to a confirmed payment (UPI Request paid). Provider does NOT charge again."""
    payment_request_id: str
    razorpay_payment_id: str
    amount_paid_inr: int
    paid_at: datetime


@dataclass
class OrderResult:
    success: bool
    provider_order_id: Optional[str]
    final_total_inr: Optional[int]
    estimated_delivery_at: Optional[datetime]
    failure_code: Optional[str]
    failure_reason: Optional[str]


class OrderStatusEnum(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class OrderStatus:
    provider_order_id: str
    status: OrderStatusEnum
    rider_name: Optional[str]
    rider_phone: Optional[str]
    rider_lat: Optional[float]
    rider_lng: Optional[float]
    eta_minutes: Optional[int]
    last_updated_at: datetime
    delivered_at: Optional[datetime]


@dataclass
class CancellationResult:
    success: bool
    refund_amount_inr: int
    refund_eta_hours: int
    cancellation_fee_inr: int
    failure_reason: Optional[str]


# ============================================================================
# Food Delivery Specific Types
# ============================================================================

@dataclass
class Restaurant:
    """A restaurant on Swiggy Food."""
    provider_restaurant_id: str
    name: str
    cuisines: list[str]                 # ["North Indian", "Mughlai"]
    rating: float                       # 4.3
    rating_count: int
    cost_for_two_inr: int
    delivery_time_min: int
    delivery_time_max: int
    distance_km: float
    image_url: Optional[str]
    has_offer: bool
    offer_text: Optional[str]           # "30% off up to ₹100"
    is_pure_veg: bool
    location: Location


@dataclass
class MenuItem:
    provider_menu_item_id: str
    restaurant_id: str
    name: str
    description: Optional[str]
    price_inr: int
    is_veg: bool
    is_bestseller: bool
    image_url: Optional[str]
    category: str                       # "Main Course" / "Starters" / "Desserts"


# ============================================================================
# Dineout Specific Types
# ============================================================================

@dataclass
class DineoutRestaurant:
    provider_restaurant_id: str
    name: str
    cuisines: list[str]
    rating: float
    cost_for_two_inr: int
    distance_km: float
    image_url: Optional[str]
    active_deals: list[str]             # ["20% off on weekdays", "Buffet ₹699"]
    location: Location


@dataclass
class DineoutSlot:
    slot_id: str
    restaurant_id: str
    date: str                           # "2026-04-27"
    time: str                           # "19:30"
    party_size: int
    available: bool
    deal_applicable: Optional[str]


@dataclass
class DineoutBooking:
    booking_id: str
    restaurant_id: str
    confirmed: bool
    booking_code: str                   # "FOODLEAF-DINE-A4F2"
    date: str
    time: str
    party_size: int
    deal_applied: Optional[str]


# ============================================================================
# Provider Interfaces (Three: Instamart, Food, Dineout)
# ============================================================================

class IGroceryProvider(Protocol):
    """Instamart-shaped interface: groceries with cart + checkout."""
    name: ProviderName

    async def search_skus(
        self, query_text: str, language: str, location: Location, limit: int = 5
    ) -> list[CanonicalSKU]: ...

    async def check_availability(
        self, sku_ids: list[str], location: Location
    ) -> dict[str, AvailabilityResult]: ...

    async def assemble_cart(
        self, items: list[CartItem], location: Location
    ) -> CartHandle: ...

    async def quote_cart(
        self, cart: CartHandle
    ) -> QuoteResult: ...

    async def execute_checkout(
        self, cart: CartHandle, payment_ref: PaymentRef, customer: CustomerProfile
    ) -> OrderResult: ...

    async def track_order(
        self, provider_order_id: str
    ) -> OrderStatus: ...

    async def cancel_order(
        self, provider_order_id: str
    ) -> CancellationResult: ...


class IFoodProvider(Protocol):
    """Swiggy Food-shaped interface: restaurants, menus, food delivery."""
    name: ProviderName

    async def search_restaurants(
        self, query_text: Optional[str], cuisine_filter: Optional[list[str]],
        location: Location, max_delivery_min: Optional[int] = None,
        only_with_offers: bool = False, limit: int = 10
    ) -> list[Restaurant]: ...

    async def get_restaurant_menu(
        self, restaurant_id: str
    ) -> list[MenuItem]: ...

    async def assemble_food_cart(
        self, restaurant_id: str, items: list[tuple[str, int]],  # [(menu_item_id, qty)]
        location: Location
    ) -> CartHandle: ...

    async def quote_food_cart(
        self, cart: CartHandle
    ) -> QuoteResult: ...

    async def place_food_order(
        self, cart: CartHandle, payment_ref: PaymentRef, customer: CustomerProfile
    ) -> OrderResult: ...

    async def track_food_order(
        self, provider_order_id: str
    ) -> OrderStatus: ...


class IDineoutProvider(Protocol):
    """Dineout-shaped interface: discover, book table."""
    name: ProviderName

    async def search_dineout(
        self, query_text: Optional[str], cuisine_filter: Optional[list[str]],
        location: Location, only_with_deals: bool = False, limit: int = 10
    ) -> list[DineoutRestaurant]: ...

    async def get_available_slots(
        self, restaurant_id: str, date: str, party_size: int
    ) -> list[DineoutSlot]: ...

    async def book_table(
        self, slot_id: str, party_size: int, customer: CustomerProfile,
        special_requests: Optional[str] = None
    ) -> DineoutBooking: ...

    async def get_booking_status(
        self, booking_id: str
    ) -> DineoutBooking: ...
