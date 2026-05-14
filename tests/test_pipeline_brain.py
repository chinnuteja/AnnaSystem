"""Pipeline integration tests — tests the full process_text_order flow with brain mocked.

Requires Redis at localhost:6379 (db=2). No real LLM calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from packages.core.conversation import ConversationStateMachine
from packages.core import pipeline as pipeline_mod
from app.agents.brain import BrainAction, ParsedItem
from app.schemas.message import CandidateItem, ParsedIntent, ParsedItem as SchemasParsedItem
from packages.providers.interface import (
    CanonicalSKU, CartItem, CartHandle, CartLine, ProviderName, QuoteResult, SkuPreview,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def redis():
    import redis as sync_redis
    try:
        sync_client = sync_redis.Redis(host="localhost", port=6379, db=2, socket_connect_timeout=0.35)
        sync_client.ping()
        sync_client.close()
    except Exception:
        pytest.skip("Redis not reachable at localhost:6379 (start Redis / docker compose)")
    r = Redis.from_url("redis://localhost:6379/2", decode_responses=True)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


def _user(uid="bbbbbbbb-0002-0002-0002-000000000002"):
    return SimpleNamespace(
        id=uid,
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )


def _mock_persist(monkeypatch):
    from tests.conftest import mock_pipeline_user_lookup
    mock_pipeline_user_lookup(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "_persist", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", lambda *_a, **_k: asyncio.sleep(0, result=None))
    # Mock DB-dependent catalog helpers
    monkeypatch.setattr(pipeline_mod, "_infer_substitute_category", lambda *_a, **_k: asyncio.sleep(0, result="staples_flour"))
    monkeypatch.setattr(pipeline_mod, "find_options_in_category", lambda **_k: asyncio.sleep(0, result=[]))


def _mock_resolve_and_quote(monkeypatch, items_in_cart=None):
    """Mock resolve_and_quote to return a simple cart with the requested items."""
    if items_in_cart is None:
        items_in_cart = ["Atta 5kg"]

    from packages.providers.interface import CartHandle

    lines = []
    for name in items_in_cart:
        lines.append(CartLine(
            canonical_key=name.lower().replace(" ", "_"),
            display_name=name,
            brand="test",
            pack_size_label="1",
            qty=1,
            unit_price_inr=100,
            line_total_inr=100,
            in_stock=True,
            eta_min=20,
        ))

    sku = CanonicalSKU(
        canonical_key=lines[0].canonical_key,
        display_name=lines[0].display_name,
        display_names_local={},
        category="staples",
        subcategory="flour",
        brand=lines[0].brand,
        pack_size=lines[0].pack_size_label,
        unit="kg",
        pack_quantity=1.0,
        estimated_price_inr=100,
        typical_price_band_min_inr=90,
        typical_price_band_max_inr=110,
        image_url=None,
        provider_specific_id="TEST-1",
        provider=ProviderName.SWIGGY_INSTAMART,
        in_stock=True,
        delivery_eta_min=20,
    )

    cart_item = CartItem(canonical_sku=sku, quantity=1)
    cart = CartHandle(
        provider=ProviderName.SWIGGY_INSTAMART,
        provider_cart_id="TEST-CART-1",
        items=[cart_item],
        expires_at=None,
    )

    quote = QuoteResult(
        cart_handle=cart,
        subtotal_inr=sum(l.line_total_inr for l in lines),
        delivery_fee_inr=25,
        handling_fee_inr=0,
        taxes_inr=0,
        discount_inr=0,
        applied_offers=[],
        total_inr=sum(l.line_total_inr for l in lines) + 25,
        estimated_delivery_min=15,
        estimated_delivery_max=25,
        line_items=lines,
    )

    candidates = [CandidateItem(
        canonical_key=lines[0].canonical_key,
        display_name=lines[0].display_name,
        brand=lines[0].brand,
        price_inr=100,
        provider_specific_id="TEST-1",
        in_stock=True,
    )]

    async def _fake_resolve(intent, loc):
        return candidates, cart, quote

    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_resolve)


def _mock_execute(monkeypatch):
    monkeypatch.setattr(
        pipeline_mod, "execute_order",
        lambda *_a, **_k: asyncio.sleep(0, result=SimpleNamespace(provider_order_id="INST-ORDER-123456")),
    )


def _brain_action(**overrides) -> BrainAction:
    defaults = dict(
        action="greet",
        detected_language="te-en",
        confidence=0.9,
        reasoning="test",
    )
    defaults.update(overrides)
    return BrainAction(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_greet_to_order_flow(monkeypatch, redis):
    """greet → order_items → confirm: full happy path."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)
    _mock_resolve_and_quote(monkeypatch)
    _mock_execute(monkeypatch)

    actions = iter([
        _brain_action(action="greet", reply_text="Namaskaram!"),
        _brain_action(action="order_items", confidence=0.95, items=[ParsedItem(text="atta", quantity=1, unit="5kg")]),
        _brain_action(action="confirm", confidence=0.95),
    ])

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=lambda *_a, **_k: next(actions)):
        # Step 1: greet
        r1 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="hi",
            whatsapp_message_id="wamid.1",
        )
        assert r1["state"] == "IDLE"
        assert "Namaskaram" in r1["reply_text"]

        # Step 2: order items
        r2 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="atta 5kg",
            whatsapp_message_id="wamid.2",
        )
        assert r2["state"] == "AWAITING_CONFIRMATION"

        # Step 3: confirm
        r3 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="confirm",
            whatsapp_message_id="wamid.3",
        )
        assert r3["state"] == "COMPLETE"


@pytest.mark.asyncio
async def test_cancel_flow(monkeypatch, redis):
    """order → cancel: state returns to IDLE."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)
    _mock_resolve_and_quote(monkeypatch)

    actions = iter([
        _brain_action(action="order_items", confidence=0.9, items=[ParsedItem(text="milk", quantity=1, unit="1L")]),
        _brain_action(action="cancel", confidence=0.95),
    ])

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=lambda *_a, **_k: next(actions)):
        r1 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="milk",
            whatsapp_message_id="wamid.c1",
        )
        assert r1["state"] == "AWAITING_CONFIRMATION"

        r2 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="cancel",
            whatsapp_message_id="wamid.c2",
        )
        assert r2["state"] == "IDLE"
        assert "cancel" in r2["reply_text"].lower()


@pytest.mark.asyncio
async def test_clear_cart_flow(monkeypatch, redis):
    """order → clear_cart → new order: old cart gone, fresh start."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)
    _mock_resolve_and_quote(monkeypatch)

    actions = iter([
        _brain_action(action="order_items", confidence=0.9, items=[ParsedItem(text="rice", quantity=1)]),
        _brain_action(action="clear_cart", confidence=0.95),
        _brain_action(action="order_items", confidence=0.9, items=[ParsedItem(text="milk", quantity=1)]),
    ])

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=lambda *_a, **_k: next(actions)):
        r1 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="rice",
            whatsapp_message_id="wamid.cl1",
        )
        assert r1["state"] == "AWAITING_CONFIRMATION"

        r2 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="clear cart",
            whatsapp_message_id="wamid.cl2",
        )
        assert r2["state"] == "IDLE"

        r3 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="milk",
            whatsapp_message_id="wamid.cl3",
        )
        assert r3["state"] == "AWAITING_CONFIRMATION"
        # New cart should be fresh (mock resolve_and_quote returns Atta by default)
        assert "atta" in r3["reply_text"].lower() or "cart" in r3["reply_text"].lower()


@pytest.mark.asyncio
async def test_ask_cart_with_pending_cart(monkeypatch, redis):
    """order → ask_cart: returns cart contents."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)
    _mock_resolve_and_quote(monkeypatch, items_in_cart=["Atta 5kg"])

    actions = iter([
        _brain_action(action="order_items", confidence=0.9, items=[ParsedItem(text="atta", quantity=1)]),
        _brain_action(action="ask_cart", confidence=0.95),
    ])

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=lambda *_a, **_k: next(actions)):
        r1 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="atta",
            whatsapp_message_id="wamid.ac1",
        )
        assert r1["state"] == "AWAITING_CONFIRMATION"

        r2 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="what's in my cart",
            whatsapp_message_id="wamid.ac2",
        )
        assert r2["state"] == "AWAITING_CONFIRMATION"
        # Should mention cart contents
        assert "atta" in r2["reply_text"].lower() or "Atta" in r2["reply_text"]


@pytest.mark.asyncio
async def test_correction_flow(monkeypatch, redis):
    """order → wrong item shown → correct: brain emits correct with selected_name."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)

    # First order returns options for atta
    options = [
        SkuPreview(
            canonical_key="aashirvaad_atta_5kg", display_name="Aashirvaad Atta",
            brand="aashirvaad", pack_size_label="5kg", price_inr=295,
            in_stock=True, provider_specific_id="ATTA-5", category="staples_flour",
            unit="kg", pack_quantity=5.0, eta_min=18,
        ),
        SkuPreview(
            canonical_key="fortune_atta_5kg", display_name="Fortune Atta",
            brand="fortune", pack_size_label="5kg", price_inr=270,
            in_stock=True, provider_specific_id="ATTA-F5", category="staples_flour",
            unit="kg", pack_quantity=5.0, eta_min=20,
        ),
    ]

    async def _fake_options(**_k):
        return options

    monkeypatch.setattr(pipeline_mod, "find_options_in_category", _fake_options)

    actions = iter([
        _brain_action(action="order_items", confidence=0.9, items=[ParsedItem(text="atta", quantity=1)]),
        _brain_action(action="select_option", confidence=0.9, selected_index=0),
        _brain_action(action="correct", confidence=0.85, reply_text="Okay, selecting Fortune instead.", selected_index=1),
    ])

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=lambda *_a, **_k: next(actions)):
        # Step 1: order atta → gets options
        r1 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="atta",
            whatsapp_message_id="wamid.cr1",
        )
        assert "1." in r1["reply_text"]

        # Step 2: select first option
        r2 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="first one",
            whatsapp_message_id="wamid.cr2",
        )
        assert "Aashirvaad" in r2["reply_text"]

        # Step 3: correct → select second option
        r3 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="no i meant fortune",
            whatsapp_message_id="wamid.cr3",
        )
        # correct with selected_index delegates to _handle_select_option → _handle_catalog_selection
        assert "Fortune" in r3["reply_text"] or "fortune" in r3["reply_text"].lower()


@pytest.mark.asyncio
async def test_track_order_flow(monkeypatch, redis):
    """track_order: returns tracking prompt."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)

    action = _brain_action(action="track_order", confidence=0.9)

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
        r = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="where is my order",
            whatsapp_message_id="wamid.tr1",
        )
        assert r["state"] == "IDLE"
        assert "order" in r["reply_text"].lower() or "track" in r["reply_text"].lower()


@pytest.mark.asyncio
async def test_low_confidence_clarifies(monkeypatch, redis):
    """Ambiguous text with low confidence → unclear action → clarification question."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)

    # Brain returns order_items with low confidence → confidence gate overrides to unclear
    low_conf = _brain_action(
        action="order_items", confidence=0.3,
        items=[ParsedItem(text="something")],
    )

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=low_conf):
        r = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="hmm maybe",
            whatsapp_message_id="wamid.lc1",
        )
        # Confidence gate overrides to unclear → handler asks clarification
        assert "kavali" in r["reply_text"].lower() or "cheppandi" in r["reply_text"].lower() or "clarify" in r["reply_text"].lower() or "?" in r["reply_text"]


@pytest.mark.asyncio
async def test_chitchat_mid_flow_preserves_cart(monkeypatch, redis):
    """order → chitchat → cart should still be there."""
    csm = ConversationStateMachine(redis)
    _mock_persist(monkeypatch)
    _mock_resolve_and_quote(monkeypatch, items_in_cart=["Atta 5kg"])

    actions = iter([
        _brain_action(action="order_items", confidence=0.9, items=[ParsedItem(text="atta", quantity=1)]),
        _brain_action(action="chitchat", confidence=0.8, reply_text="Haan, mee cart lo atta undi!"),
    ])

    with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=lambda *_a, **_k: next(actions)):
        r1 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="atta",
            whatsapp_message_id="wamid.ct1",
        )
        assert r1["state"] == "AWAITING_CONFIRMATION"

        r2 = await pipeline_mod.process_text_order(
            csm=csm, from_phone="+918247628278", text="what happened",
            whatsapp_message_id="wamid.ct2",
        )
        # Chitchat with pending cart should preserve AWAITING_CONFIRMATION
        assert r2["state"] == "AWAITING_CONFIRMATION"
