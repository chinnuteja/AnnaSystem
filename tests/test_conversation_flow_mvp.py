import asyncio
import sys
from pathlib import Path
import pytest

from types import SimpleNamespace
import pytest_asyncio
from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from packages.core.conversation import ConversationStateMachine
from packages.core import pipeline as pipeline_mod
from app.agents import brain as brain_mod
from app.agents.brain import BrainAction, ParsedItem as BrainParsedItem
from app.schemas.message import ParsedIntent, ParsedItem
from packages.providers.interface import (
    CanonicalSKU, CartItem, CartHandle, CartLine, ProviderName, QuoteResult, SkuPreview,
)


@pytest_asyncio.fixture
async def redis(monkeypatch):
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

@pytest.mark.asyncio
async def test_case5_6_8_flow_with_address_update(monkeypatch, redis):
    csm = ConversationStateMachine(redis)

    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )

    options = [
        SkuPreview(
            canonical_key="aashirvaad_select_atta_5kg",
            display_name="Aashirvaad Select Atta",
            brand="aashirvaad",
            pack_size_label="5kg",
            price_inr=295,
            in_stock=True,
            provider_specific_id="ATTA-5",
            category="staples_flour",
            unit="kg",
            pack_quantity=5.0,
            eta_min=18,
        ),
        SkuPreview(
            canonical_key="fortune_chakki_atta_5kg",
            display_name="Fortune Chakki Atta",
            brand="fortune",
            pack_size_label="5kg",
            price_inr=270,
            in_stock=True,
            provider_specific_id="ATTA-F5",
            category="staples_flour",
            unit="kg",
            pack_quantity=5.0,
            eta_min=20,
        ),
    ]

    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(pipeline_mod, "resolve_family_context", lambda *a, **k: asyncio.sleep(0, result=_fam_ctx))
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    monkeypatch.setattr(pipeline_mod, "_rehydrate_recent_pending_session",
                        lambda *a, **k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_persist", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(
        pipeline_mod,
        "execute_order",
        lambda *_a, **_k: asyncio.sleep(0, result=SimpleNamespace(provider_order_id="INST-ORDER-123456")),
    )

    # Mock provider router for _handle_catalog_selection
    from packages.providers.router import provider_router

    _mock_sku = CanonicalSKU(
        canonical_key="aashirvaad_select_atta_5kg", display_name="Aashirvaad Select Atta 5kg",
        display_names_local={}, category="staples", subcategory="flour", brand="aashirvaad",
        pack_size="5kg", unit="kg", pack_quantity=5.0, estimated_price_inr=295,
        typical_price_band_min_inr=280, typical_price_band_max_inr=310, image_url=None,
        provider_specific_id="ATTA-5", provider=ProviderName.SWIGGY_INSTAMART,
        in_stock=True, delivery_eta_min=18,
    )
    _mock_cart = CartHandle(provider=ProviderName.SWIGGY_INSTAMART, provider_cart_id="TEST-CART",
                             items=[CartItem(canonical_sku=_mock_sku, quantity=1)], expires_at=None)
    _mock_quote = QuoteResult(
        cart_handle=_mock_cart, subtotal_inr=295, delivery_fee_inr=25, handling_fee_inr=0,
        taxes_inr=0, discount_inr=0, applied_offers=[], total_inr=320,
        estimated_delivery_min=15, estimated_delivery_max=25,
        line_items=[CartLine(canonical_key="aashirvaad_select_atta_5kg", display_name="Aashirvaad Select Atta 5kg",
                             brand="aashirvaad", pack_size_label="5kg", qty=1, unit_price_inr=295,
                             line_total_inr=295, in_stock=True, eta_min=18)],
    )

    class _MockProvider:
        async def assemble_cart(self, items, loc=None):
            return _mock_cart
        async def quote_cart(self, cart):
            return _mock_quote

    monkeypatch.setattr(provider_router, "grocery", lambda: _MockProvider())

    async def _fake_decide(text, *_a, **_k):
        t = text.strip().lower()
        if t in {"1", "first"}:
            return BrainAction(action="select_option", selected_index=0, detected_language="te-en", confidence=0.95, reasoning="test")
        if t in {"ok", "confirm", "checkout", "place order"}:
            return BrainAction(action="confirm", detected_language="te-en", confidence=0.95, reasoning="test")
        if t.startswith("deliver to") or t.startswith("address is") or t.startswith("change address"):
            return BrainAction(action="update_address", address_text=text, detected_language="te-en", confidence=0.9, reasoning="test")
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="atta", quantity=1)],
            domain_hint="grocery",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_infer(_intent):
        return "staples_flour"

    async def _fake_options(**_k):
        return options

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_infer_substitute_category", _fake_infer)
    monkeypatch.setattr(pipeline_mod, "find_options_in_category", _fake_options)

    # Step 1: ambiguous staple → numbered options
    first = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="atta",
        whatsapp_message_id="wamid.m1",
        location=None,
    )
    assert first["state"] == "AWAITING_CONFIRMATION"
    assert "1. Aashirvaad Select Atta 5kg" in first["reply_text"]
    assert "2. Fortune Chakki Atta 5kg" in first["reply_text"]

    # Step 2: select option → cart confirmation
    second = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="1",
        whatsapp_message_id="wamid.m2",
        location=None,
    )
    assert second["state"] == "AWAITING_CONFIRMATION"
    assert "Aashirvaad" in second["reply_text"]
    assert "Confirm chey-yana" in second["reply_text"]

    # Step 3: address update → ACK
    third = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="deliver to Flat 9B, Inorbit Road, Madhapur, Hyderabad 500081",
        whatsapp_message_id="wamid.m3",
        location=None,
    )
    assert third["state"] == "AWAITING_CONFIRMATION"
    assert "Delivery address updated" in third["reply_text"]
    assert "Madhapur" in third["reply_text"]

    # Step 4: checkout/confirm → order placed (mock)
    fourth = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="checkout",
        whatsapp_message_id="wamid.m4",
        location=None,
    )
    assert fourth["state"] == "COMPLETE"
    assert "mee order confirm ayindi" in fourth["reply_text"].lower()


@pytest.mark.asyncio
async def test_case4_not_in_catalog_offers_substitutes(monkeypatch, redis):
    csm = ConversationStateMachine(redis)

    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000003",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )

    subs = [
        SkuPreview(
            canonical_key="apple_shimla_1kg",
            display_name="Apple Shimla",
            brand="",
            pack_size_label="1kg",
            price_inr=195,
            in_stock=True,
            provider_specific_id="APL-1",
            category="fruits",
            unit="kg",
            pack_quantity=1.0,
            eta_min=15,
        ),
        SkuPreview(
            canonical_key="banana_robusta_12pc",
            display_name="Banana Robusta",
            brand="",
            pack_size_label="12 pcs",
            price_inr=60,
            in_stock=True,
            provider_specific_id="BAN-12",
            category="fruits",
            unit="pcs",
            pack_quantity=12,
            eta_min=12,
        ),
    ]

    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(pipeline_mod, "resolve_family_context", lambda *a, **k: asyncio.sleep(0, result=_fam_ctx))
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    monkeypatch.setattr(pipeline_mod, "_rehydrate_recent_pending_session",
                        lambda *a, **k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_persist", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", lambda *_a, **_k: asyncio.sleep(0, result=None))

    async def _fake_decide(text, *_a, **_k):
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="almonds", quantity=1)],
            domain_hint="grocery",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_infer(_intent):
        return "fruits"

    async def _fake_options(**_k):
        return subs

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_infer_substitute_category", _fake_infer)
    monkeypatch.setattr(pipeline_mod, "find_options_in_category", _fake_options)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="Almonds 250g",
        whatsapp_message_id="wamid.x1",
        location=None,
    )
    assert res["state"] == "IDLE"  # non-confirmable substitutes only
    # Should contain substitute suggestions
    assert "Badulu" in res["reply_text"] or "Closest available" in res["reply_text"]


@pytest.mark.asyncio
async def test_case1_greeting_welcome(monkeypatch, redis):
    csm = ConversationStateMachine(redis)

    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000004",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )

    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(pipeline_mod, "resolve_family_context", lambda *a, **k: asyncio.sleep(0, result=_fam_ctx))
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    monkeypatch.setattr(pipeline_mod, "_rehydrate_recent_pending_session",
                        lambda *a, **k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_persist", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", lambda *_a, **_k: asyncio.sleep(0, result=None))

    async def _fake_decide(text, *_a, **_k):
        return BrainAction(action="greet", reply_text="Namaskaram! foodleaf lo text or voice tho order cheyyachu. Emi kavali?", detected_language="te-en", confidence=0.95, reasoning="test")

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="hi",
        whatsapp_message_id="wamid.g1",
        location=None,
    )
    assert res["state"] in {"IDLE", "AWAITING_CONFIRMATION"}
    # Should advertise capabilities in welcome
    assert (
        ("Groceries" in res["reply_text"])
        or ("food delivery" in res["reply_text"].lower())
        or ("order" in res["reply_text"].lower())
        or ("kavali" in res["reply_text"].lower())
    )
