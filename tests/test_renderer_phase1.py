from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from app.agents.renderer.cart import render_cart_confirmation
from app.agents.renderer.chitchat import render_chitchat_rotating
from app.agents.renderer.stock import render_substitutes
from app.agents.renderer.templates._registry import templates_for
from app.agents.renderer.templates.en_in import EnInTemplates
from app.schemas.message import CandidateItem
from packages.providers.adapters.mock_swiggy_adapter import MockSwiggyInstamartAdapter
from packages.providers.interface import (
    CartHandle,
    CartItem,
    CartLine,
    CanonicalSKU,
    Location,
    ProviderName,
    QuoteResult,
    SkuPreview,
)


def _dummy_quote(line_items: list[CartLine], *, language: str = "te-IN") -> QuoteResult:
    dummy_sku = CanonicalSKU(
        canonical_key="dummy",
        display_name="Dummy",
        display_names_local={language: ["dummy"]},
        category="test",
        subcategory="test",
        brand="test",
        pack_size="1pc",
        unit="piece",
        pack_quantity=1.0,
        estimated_price_inr=1,
        typical_price_band_min_inr=1,
        typical_price_band_max_inr=1,
        image_url=None,
        provider_specific_id="dummy-1",
        provider=ProviderName.SWIGGY_INSTAMART,
        in_stock=True,
        delivery_eta_min=12,
    )
    cart = CartHandle(
        provider=ProviderName.SWIGGY_INSTAMART,
        provider_cart_id="dummy-cart",
        items=[CartItem(canonical_sku=dummy_sku, quantity=1)],
        expires_at=datetime.utcnow(),
    )
    return QuoteResult(
        cart_handle=cart,
        subtotal_inr=sum(li.line_total_inr for li in line_items if li.in_stock),
        delivery_fee_inr=0,
        handling_fee_inr=0,
        taxes_inr=0,
        discount_inr=0,
        applied_offers=[],
        total_inr=sum(li.line_total_inr for li in line_items if li.in_stock),
        estimated_delivery_min=18,
        estimated_delivery_max=23,
        line_items=line_items,
    )


def test_render_cart_confirmation_te_in():
    quote = _dummy_quote(
        [
            CartLine(
                canonical_key="atta",
                display_name="Aashirvaad Select Atta",
                brand="aashirvaad",
                pack_size_label="5kg",
                qty=1,
                unit_price_inr=240,
                line_total_inr=240,
                in_stock=True,
                eta_min=18,
            ),
            CartLine(
                canonical_key="milk",
                display_name="Heritage Toned Milk",
                brand="heritage",
                pack_size_label="1L",
                qty=2,
                unit_price_inr=34,
                line_total_inr=68,
                in_stock=True,
                eta_min=20,
            ),
        ]
    )
    text = render_cart_confirmation(quote, address_label="Kondapur", language="te-IN")
    assert "🛒 Mee cart:" in text
    assert "• Aashirvaad Select Atta 5kg × 1 — ₹240" in text
    assert "• Heritage Toned Milk 1L × 2 — ₹68" in text
    assert "Subtotal: ₹308 | Delivery: ₹0 | Total: ₹308" in text
    assert "Delivery: ~18-23 min (Kondapur)" in text
    assert "Confirm chey-yana? (avunu / vaddu)" in text


def test_render_cart_confirmation_oos_item():
    quote = _dummy_quote(
        [
            CartLine(
                canonical_key="paneer",
                display_name="Amul Paneer",
                brand="amul",
                pack_size_label="200g",
                qty=1,
                unit_price_inr=95,
                line_total_inr=95,
                in_stock=False,
                eta_min=19,
            )
        ]
    )
    text = render_cart_confirmation(quote, language="te-IN")
    assert "❌ stock ledu" in text


def test_render_cart_confirmation_oos_item_with_substitute():
    quote = _dummy_quote(
        [
            CartLine(
                canonical_key="paneer",
                display_name="Milky Mist Fresh Paneer",
                brand="milky_mist",
                pack_size_label="200g",
                qty=1,
                unit_price_inr=95,
                line_total_inr=95,
                in_stock=False,
                eta_min=None,
                substitutes=[
                    SkuPreview(
                        canonical_key="curd",
                        display_name="Heritage Curd",
                        brand="heritage",
                        pack_size_label="500g",
                        price_inr=40,
                        in_stock=True,
                        provider_specific_id="curd-1",
                        category="dairy_curd",
                    )
                ],
            )
        ]
    )
    text = render_cart_confirmation(quote, language="te-IN")
    assert "Milky Mist Fresh Paneer 200g × 1 — ❌ stock ledu" in text
    assert "Badulu Heritage Curd 500g ₹40 available undi" in text


def test_render_cart_confirmation_size_adjustment_te_in():
    quote = _dummy_quote(
        [
            CartLine(
                canonical_key="paneer",
                display_name="Amul Paneer",
                brand="amul",
                pack_size_label="200g",
                qty=1,
                unit_price_inr=95,
                line_total_inr=95,
                in_stock=True,
                eta_min=19,
                requested_size_label="100g",
            )
        ]
    )
    text = render_cart_confirmation(quote, language="te-IN")
    assert "100g pack ledu, 200g available undi" in text


def test_render_cart_confirmation_size_adjustment_en_in():
    quote = _dummy_quote(
        [
            CartLine(
                canonical_key="paneer",
                display_name="Amul Paneer",
                brand="amul",
                pack_size_label="200g",
                qty=1,
                unit_price_inr=95,
                line_total_inr=95,
                in_stock=True,
                eta_min=19,
                requested_size_label="100g",
            )
        ]
    )
    text = render_cart_confirmation(quote, language="en-IN")
    assert "100g pack not available, using 200g" in text


@pytest.mark.asyncio
async def test_quote_cart_populates_line_items(monkeypatch):
    import packages.providers.adapters.mock_swiggy_adapter as mock_mod

    # Avoid flaky random failure in tests.
    monkeypatch.setattr(mock_mod, "_maybe_fail", lambda *args, **kwargs: None)

    adapter = MockSwiggyInstamartAdapter()
    location = Location(latitude=17.45, longitude=78.38, pincode="500032", city="Hyderabad")
    skus = await adapter.search_skus("atta", "en-IN", location, limit=1)
    assert skus
    cart = await adapter.assemble_cart(
        [CartItem(canonical_sku=skus[0], quantity=1)],
        location,
    )
    quote = await adapter.quote_cart(cart)
    assert len(quote.line_items) == len(cart.items)
    assert quote.line_items[0].eta_min == skus[0].delivery_eta_min


def test_templates_for_fallback():
    t = templates_for("fr-FR")
    assert isinstance(t, EnInTemplates)


def test_render_cart_confirmation_fallback_uses_candidates():
    quote = _dummy_quote([])
    text = render_cart_confirmation(
        quote,
        language="te-IN",
        candidates=[CandidateItem(
            canonical_key="atta",
            display_name="Aashirvaad Select Atta 5kg",
            brand="aashirvaad",
            price_inr=240,
            provider_specific_id="x1",
            in_stock=True,
        )],
    )
    assert "Aashirvaad Select Atta 5kg cart lo pettanu" in text


def test_render_substitutes_options():
    text = render_substitutes(
        requested_text="almonds 250g",
        substitutes=[
            SkuPreview(
                canonical_key="cashew",
                display_name="Premium Cashews",
                brand="generic",
                pack_size_label="250g",
                price_inr=220,
                in_stock=True,
                provider_specific_id="cashew-1",
                category="dry_fruits",
            )
        ],
        language="en-IN",
    )
    assert "I couldn't find an exact match for almonds 250g." in text
    assert "Instead, Premium Cashews 250g is available for ₹220" in text


@pytest.mark.asyncio
async def test_render_chitchat_rotating_mid_order():
    class FakeRedis:
        def __init__(self):
            self.value = 0
            self.keys: list[str] = []

        async def incr(self, key: str) -> int:
            self.keys.append(key)
            self.value += 1
            return self.value

    ctx = {"state": "AWAITING_CONFIRMATION", "context": {"resolved_cart": {"items": []}}}
    redis = FakeRedis()
    first = await render_chitchat_rotating(redis, conv_ctx=ctx, user_id="user-1")
    second = await render_chitchat_rotating(redis, conv_ctx=ctx, user_id="user-1")
    third = await render_chitchat_rotating(redis, conv_ctx=ctx, user_id="user-1")
    assert first == "Mee order pending undi — confirm cheyyana, leda venaki vellali?"
    assert second != third
    assert redis.keys == [
        "chitchat:rotation:user-1:mid_order",
        "chitchat:rotation:user-1:mid_order",
        "chitchat:rotation:user-1:mid_order",
    ]


@pytest.mark.asyncio
async def test_render_chitchat_rotating_en_in_mid_order():
    class FakeRedis:
        async def incr(self, _key: str) -> int:
            return 1

    ctx = {"state": "AWAITING_CONFIRMATION", "context": {"resolved_cart": {"items": []}}}
    text = await render_chitchat_rotating(FakeRedis(), conv_ctx=ctx, user_id="user-1", language="en-IN")
    assert text == "Your order is pending — should I confirm it or go back?"


@pytest.mark.asyncio
async def test_quote_cart_requested_size_roundtrip(monkeypatch):
    import packages.providers.adapters.mock_swiggy_adapter as mock_mod

    monkeypatch.setattr(mock_mod, "_maybe_fail", lambda *args, **kwargs: None)
    adapter = MockSwiggyInstamartAdapter()
    location = Location(latitude=17.45, longitude=78.38, pincode="500032", city="Hyderabad")
    skus = await adapter.search_skus("atta", "en-IN", location, limit=1)
    assert skus
    cart = CartHandle(
        provider=ProviderName.SWIGGY_INSTAMART,
        provider_cart_id="manual-cart",
        items=[CartItem(canonical_sku=skus[0], quantity=1, notes="100g")],
        expires_at=datetime.utcnow(),
    )
    quote = await adapter.quote_cart(cart)
    assert quote.line_items
    assert quote.line_items[0].requested_size_label == "100g"
