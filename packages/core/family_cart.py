"""Family Cart — Redis-backed running cart scoped per family.

All family members share one running cart. The cart tracks:
  - items (name, quantity, price, source)
  - running total
  - approval status (pending_approval / approved / rejected)
  - which member added each item

Cart state is stored in Redis as JSON with a 24h TTL.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger("foodleaf.family_cart")

_CART_PREFIX = "family_cart:"
_CART_TTL = 24 * 60 * 60  # 24 hours
_APPROVAL_PREFIX = "cart_approval:"


@dataclass
class CartItem:
    """A single item in the family cart."""

    name: str
    quantity: int = 1
    unit: str | None = None
    price_inr: float | None = None
    brand: str | None = None
    added_by: str | None = None  # user_id who added
    source: str | None = None   # e.g. "swiggy_instamart"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CartItem:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class FamilyCart:
    """The running family cart."""

    cart_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    family_id: str = ""
    items: list[CartItem] = field(default_factory=list)
    total_inr: float = 0.0
    approval_status: str = "none"  # none | pending_approval | approved | rejected
    ordering_user_id: str | None = None
    ordering_user_phone: str | None = None
    payer_user_id: str | None = None

    def add_item(self, item: CartItem) -> None:
        """Add an item to the cart, merging if same name+brand already exists."""
        for existing in self.items:
            if existing.name == item.name and existing.brand == item.brand:
                existing.quantity += item.quantity
                if item.price_inr is not None:
                    existing.price_inr = item.price_inr
                self._recalc_total()
                return
        self.items.append(item)
        self._recalc_total()

    def remove_item(self, name: str, brand: str | None = None) -> bool:
        """Remove an item by name (and optionally brand). Returns True if found."""
        before = len(self.items)
        self.items = [
            i for i in self.items
            if not (i.name == name and (brand is None or i.brand == brand))
        ]
        if len(self.items) < before:
            self._recalc_total()
            return True
        return False

    def clear(self) -> None:
        """Clear all items from the cart."""
        self.items = []
        self.total_inr = 0.0
        self.approval_status = "none"

    def _recalc_total(self) -> None:
        """Recalculate the cart total from item prices."""
        total = 0.0
        for item in self.items:
            if item.price_inr is not None:
                total += item.price_inr * item.quantity
        self.total_inr = total

    def to_dict(self) -> dict:
        return {
            "cart_id": self.cart_id,
            "family_id": self.family_id,
            "items": [i.to_dict() for i in self.items],
            "total_inr": self.total_inr,
            "approval_status": self.approval_status,
            "ordering_user_id": self.ordering_user_id,
            "ordering_user_phone": self.ordering_user_phone,
            "payer_user_id": self.payer_user_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FamilyCart:
        items = [CartItem.from_dict(i) for i in data.get("items", [])]
        return cls(
            cart_id=data.get("cart_id", str(uuid.uuid4())[:8]),
            family_id=data.get("family_id", ""),
            items=items,
            total_inr=data.get("total_inr", 0.0),
            approval_status=data.get("approval_status", "none"),
            ordering_user_id=data.get("ordering_user_id"),
            ordering_user_phone=data.get("ordering_user_phone"),
            payer_user_id=data.get("payer_user_id"),
        )

    def format_items_text(self, locale: str = "hi-IN") -> str:
        """Format cart items as readable text in the given locale."""
        if not self.items:
            return "Cart khaali hai!" if locale.startswith("hi") else "Cart is empty!"
        lines = []
        for i, item in enumerate(self.items, 1):
            brand_str = f" ({item.brand})" if item.brand else ""
            price_str = f" — ₹{item.price_inr * item.quantity:.0f}" if item.price_inr else ""
            lines.append(f"  {i}. {item.name}{brand_str} × {item.quantity}{price_str}")
        total_str = f"\nTotal: ₹{self.total_inr:.0f}"
        return "\n".join(lines) + total_str


def _cart_key(family_id: str) -> str:
    return f"{_CART_PREFIX}{family_id}"


def _approval_key(cart_id: str) -> str:
    return f"{_APPROVAL_PREFIX}{cart_id}"


async def load_cart(family_id: str, redis: Redis) -> FamilyCart:
    """Load the family cart from Redis, or return an empty cart."""
    raw = await redis.get(_cart_key(family_id))
    if raw:
        try:
            return FamilyCart.from_dict(json.loads(raw))
        except Exception:
            logger.warning("Failed to parse cart for family %s", family_id)
    return FamilyCart(family_id=family_id)


async def save_cart(cart: FamilyCart, redis: Redis) -> None:
    """Save the family cart to Redis with TTL."""
    key = _cart_key(cart.family_id)
    await redis.set(key, json.dumps(cart.to_dict()), ex=_CART_TTL)


async def clear_cart(family_id: str, redis: Redis) -> None:
    """Delete the family cart from Redis."""
    await redis.delete(_cart_key(family_id))


async def set_approval_status(
    family_id: str,
    status: str,
    redis: Redis,
    *,
    payer_user_id: str | None = None,
) -> FamilyCart | None:
    """Update the approval status of a family cart.

    Returns the updated cart, or None if no cart exists.
    """
    cart = await load_cart(family_id, redis)
    if not cart.items:
        return None
    cart.approval_status = status
    if payer_user_id:
        cart.payer_user_id = payer_user_id
    await save_cart(cart, redis)
    return cart


async def check_threshold_and_notify(
    cart: FamilyCart,
    threshold_inr: int,
    redis: Redis,
) -> bool:
    """Check if cart total meets or exceeds the approval threshold.

    Returns True if threshold is met (payer should be notified).
    Sets approval_status to 'pending_approval' if so.
    """
    if cart.total_inr >= threshold_inr and cart.approval_status == "none":
        cart.approval_status = "pending_approval"
        await save_cart(cart, redis)
        # Store a flag so the pipeline knows to send notification
        await redis.set(
            _approval_key(cart.cart_id),
            json.dumps({"family_id": cart.family_id, "status": "pending_approval", "total": cart.total_inr}),
            ex=_CART_TTL,
        )
        return True
    return False


async def get_pending_approval(cart_id: str, redis: Redis) -> dict | None:
    """Get pending approval data for a cart."""
    raw = await redis.get(_approval_key(cart_id))
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None
