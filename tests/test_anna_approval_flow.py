"""Integration tests for the full Anna approval flow (Day 2 + Day 3).

Tests:
  1. Maa orders above threshold → payer notification dispatched
  2. Beta approves → order placed, both members notified
  3. Beta rejects → Maa notified of rejection
  4. Below threshold → auto-approves without payer notification
  5. Proactive occasion hint reaches brain

Run:  pytest tests/test_anna_approval_flow.py -v
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.core.conversation import ConversationStateMachine

# Demo constants (same as seed_anna_demo.py)
MAA_PHONE = "+919876500001"
BETA_PHONE = "+919876500002"
FAMILY_ID = "a0000000-0000-0000-0000-000000000001"
MAA_ID = "a0000000-0000-0000-0000-000000000010"
BETA_ID = "a0000000-0000-0000-0000-000000000020"


@pytest_asyncio.fixture
async def redis():
    from redis.asyncio import Redis
    r = Redis.from_url("redis://localhost:6379/2", decode_responses=True)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest_asyncio.fixture
async def csm(redis):
    return ConversationStateMachine(redis)


def _make_maa_ctx():
    """Mock FamilyContext for Maa (ordering_user)."""
    from packages.core.family_resolver import FamilyContext
    from packages.core.models import Family, User
    maa = User(
        id=MAA_ID, family_id=FAMILY_ID, role="ordering_user",
        relationship_label="Maa", display_name="Sunita Sharma",
        phone_e164=MAA_PHONE, whatsapp_phone_e164=MAA_PHONE,
        preferred_language="hi-IN",
    )
    family = Family(
        id=FAMILY_ID, display_name="Sharma Family",
        default_payer_user_id=BETA_ID,
        primary_locale="hi-IN", city="Delhi",
        approval_threshold_inr=1500, care_features_enabled=True,
    )
    beta = User(
        id=BETA_ID, family_id=FAMILY_ID, role="payer",
        relationship_label="Beta", display_name="Rahul Sharma",
        phone_e164=BETA_PHONE, whatsapp_phone_e164=BETA_PHONE,
        preferred_language="en-IN",
    )
    return FamilyContext(user=maa, family=family, payer=beta, payer_auto_approve_threshold=500)


def _make_beta_ctx():
    """Mock FamilyContext for Beta (payer)."""
    from packages.core.family_resolver import FamilyContext
    from packages.core.models import Family, User
    beta = User(
        id=BETA_ID, family_id=FAMILY_ID, role="payer",
        relationship_label="Beta", display_name="Rahul Sharma",
        phone_e164=BETA_PHONE, whatsapp_phone_e164=BETA_PHONE,
        preferred_language="en-IN",
    )
    family = Family(
        id=FAMILY_ID, display_name="Sharma Family",
        default_payer_user_id=BETA_ID,
        primary_locale="hi-IN", city="Delhi",
        approval_threshold_inr=1500, care_features_enabled=True,
    )
    maa = User(
        id=MAA_ID, family_id=FAMILY_ID, role="ordering_user",
        relationship_label="Maa", display_name="Sunita Sharma",
        phone_e164=MAA_PHONE, whatsapp_phone_e164=MAA_PHONE,
        preferred_language="hi-IN",
    )
    return FamilyContext(user=beta, family=family, payer=beta, payer_auto_approve_threshold=500)


def _setup_mocks(monkeypatch):
    """Shared mocks for all tests in this file."""
    from packages.core import pipeline as pipeline_mod

    # Family resolver
    maa_ctx = _make_maa_ctx()
    beta_ctx = _make_beta_ctx()

    async def _fake_resolve(phone, *_a, **_k):
        if phone == MAA_PHONE:
            return maa_ctx
        if phone == BETA_PHONE:
            return beta_ctx
        return None

    monkeypatch.setattr(pipeline_mod, "resolve_family_context", _fake_resolve)
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    monkeypatch.setattr(pipeline_mod, "_rehydrate_recent_pending_session",
                        lambda *a, **k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_persist", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_infer_substitute_category", lambda *_a, **_k: asyncio.sleep(0, result="staples_flour"))
    monkeypatch.setattr(pipeline_mod, "find_options_in_category", lambda **_k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(pipeline_mod, "execute_order",
                        lambda *_a, **_k: asyncio.sleep(0, result=SimpleNamespace(provider_order_id="MOCK-ORDER-123456")))


def _make_high_value_quote():
    """Build a QuoteResult with total ₹1868 (above ₹1500 threshold)."""
    from packages.providers.interface import CartHandle, CartLine, QuoteResult, ProviderName
    cart_lines = [
        CartLine(
            canonical_key="atta_5kg", display_name="Aashirvaad Atta 5kg", brand="Aashirvaad",
            pack_size_label="5kg", qty=1, unit_price_inr=300, line_total_inr=300,
            in_stock=True, eta_min=30,
        ),
        CartLine(
            canonical_key="doodh_1l", display_name="Amul Doodh 1L", brand="Amul",
            pack_size_label="1L", qty=5, unit_price_inr=300, line_total_inr=1500,
            in_stock=True, eta_min=30,
        ),
    ]
    handle = CartHandle(provider=ProviderName.SWIGGY_INSTAMART, provider_cart_id="mock-cart-hi", items=[], expires_at=datetime.now(timezone.utc))
    quote = QuoteResult(
        cart_handle=handle, subtotal_inr=1800, delivery_fee_inr=40,
        handling_fee_inr=0, taxes_inr=28, discount_inr=0,
        applied_offers=[], total_inr=1868,
        estimated_delivery_min=30, estimated_delivery_max=60,
        line_items=cart_lines,
    )
    return cart_lines, quote


def _make_low_value_quote():
    """Build a QuoteResult with total ₹85 (below ₹1500 threshold)."""
    from packages.providers.interface import CartHandle, CartLine, QuoteResult, ProviderName
    cart_lines = [
        CartLine(
            canonical_key="doodh_1l", display_name="Amul Doodh 1L", brand="Amul",
            pack_size_label="1L", qty=1, unit_price_inr=85, line_total_inr=85,
            in_stock=True, eta_min=30,
        ),
    ]
    handle = CartHandle(provider=ProviderName.SWIGGY_INSTAMART, provider_cart_id="mock-cart-lo", items=[], expires_at=datetime.now(timezone.utc))
    quote = QuoteResult(
        cart_handle=handle, subtotal_inr=85, delivery_fee_inr=0,
        handling_fee_inr=0, taxes_inr=0, discount_inr=0,
        applied_offers=[], total_inr=85,
        estimated_delivery_min=30, estimated_delivery_max=60,
        line_items=cart_lines,
    )
    return cart_lines, quote


# ============================================================================
# Test 1: Maa orders above threshold → payer notification
# ============================================================================

@pytest.mark.asyncio
async def test_maa_order_triggers_payer_notification(csm, monkeypatch):
    """Maa orders above threshold → confirm → AWAITING_APPROVAL + notify_payer dispatched.

    The pipeline flow is TWO steps:
      1. order_items → AWAITING_CONFIRMATION (cart preview)
      2. confirm → threshold check → AWAITING_APPROVAL + notify_payer
    """
    from packages.core import pipeline as pipeline_mod
    from app.agents import brain as brain_mod
    from app.agents.brain import BrainAction, ParsedItem

    _setup_mocks(monkeypatch)

    cart_lines, quote = _make_high_value_quote()

    async def _fake_rq(*_a, **_k):
        return (cart_lines, quote.cart_handle, quote)

    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_rq)

    # Brain mock: first call → order_items, second call → confirm
    call_count = {"n": 0}

    async def _fake_decide(text, *_a, **_k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return BrainAction(
                action="order_items",
                items=[ParsedItem(text="atta doodh", quantity=1)],
                domain_hint="grocery",
                detected_language="hi-en",
                confidence=0.95,
                reasoning="test",
            )
        return BrainAction(
            action="confirm",
            detected_language="hi-en",
            confidence=0.95,
            reasoning="test",
        )

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)

    # Step 1: Maa orders → AWAITING_CONFIRMATION
    res1 = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=MAA_PHONE,
        text="atta doodh laana hai",
        whatsapp_message_id="wamid.test.maa1",
        location=None,
    )
    assert res1["state"] == "AWAITING_CONFIRMATION"

    # Step 2: Maa confirms → threshold exceeded → AWAITING_APPROVAL + notify_payer
    res2 = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=MAA_PHONE,
        text="haan bhej do",
        whatsapp_message_id="wamid.test.maa2",
        location=None,
    )
    assert res2["state"] == "AWAITING_APPROVAL"
    assert "notify_payer" in res2
    assert res2["notify_payer"]["phone"] == BETA_PHONE
    assert "APPROVE" in res2["notify_payer"]["text"]


# ============================================================================
# Test 2: Beta approves → order placed, both notified
# ============================================================================

@pytest.mark.asyncio
async def test_beta_approve_places_order(csm, monkeypatch):
    """Beta approves pending cart → order placed, both members notified."""
    from packages.core import pipeline as pipeline_mod
    from app.agents import brain as brain_mod
    from app.agents.brain import BrainAction
    from packages.core.family_cart import FamilyCart, CartItem, save_cart

    _setup_mocks(monkeypatch)

    # Seed a pending family cart in Redis
    cart = FamilyCart(family_id=FAMILY_ID, ordering_user_id=MAA_ID, ordering_user_phone=MAA_PHONE, payer_user_id=BETA_ID)
    cart.add_item(CartItem(name="Aashirvaad Atta 5kg", quantity=1, price_inr=300))
    cart.add_item(CartItem(name="Amul Doodh 1L", quantity=5, price_inr=300))
    cart.approval_status = "pending_approval"
    await save_cart(cart, csm._redis)

    # Seed CSM state for Maa (so _handle_confirm's check sees AWAITING_APPROVAL)
    await csm.start_session(MAA_ID)
    await csm.transition(MAA_ID, "AWAITING_CONFIRMATION")
    await csm.transition(MAA_ID, "AWAITING_APPROVAL", context={
        "flow": "cart_confirmation",
        "resolved_cart": {"items": [], "quote_total_inr": 1800},
        "family_cart_id": cart.cart_id,
    })

    # Mock brain → approve
    async def _fake_decide(text, *_a, **_k):
        return BrainAction(
            action="approve",
            detected_language="en",
            confidence=0.95,
            reasoning="test",
        )

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)

    # Beta sends "approve"
    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=BETA_PHONE,
        text="approve",
        whatsapp_message_id="wamid.test.beta1",
        location=None,
    )

    assert res["state"] == "COMPLETE"
    assert "notify_ordering_user" in res
    assert res["notify_ordering_user"]["phone"] == MAA_PHONE
    assert "approve" in res["notify_ordering_user"]["text"].lower()


# ============================================================================
# Test 3: Beta rejects → Maa notified
# ============================================================================

@pytest.mark.asyncio
async def test_beta_reject_notifies_maa(csm, monkeypatch):
    """Beta rejects pending cart → Maa notified, state resets."""
    from packages.core import pipeline as pipeline_mod
    from app.agents import brain as brain_mod
    from app.agents.brain import BrainAction
    from packages.core.family_cart import FamilyCart, CartItem, save_cart

    _setup_mocks(monkeypatch)

    # Seed a pending family cart
    cart = FamilyCart(family_id=FAMILY_ID, ordering_user_id=MAA_ID, ordering_user_phone=MAA_PHONE, payer_user_id=BETA_ID)
    cart.add_item(CartItem(name="Aashirvaad Atta 5kg", quantity=1, price_inr=300))
    cart.approval_status = "pending_approval"
    await save_cart(cart, csm._redis)

    # Seed CSM state for Maa
    await csm.start_session(MAA_ID)
    await csm.transition(MAA_ID, "AWAITING_CONFIRMATION")
    await csm.transition(MAA_ID, "AWAITING_APPROVAL", context={
        "flow": "cart_confirmation",
        "resolved_cart": {"items": [], "quote_total_inr": 300},
        "family_cart_id": cart.cart_id,
    })

    # Mock brain → reject_approval
    async def _fake_decide(text, *_a, **_k):
        return BrainAction(
            action="reject_approval",
            detected_language="en",
            confidence=0.95,
            reasoning="test",
        )

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=BETA_PHONE,
        text="nahi",
        whatsapp_message_id="wamid.test.beta2",
        location=None,
    )

    assert res["state"] == "IDLE"
    assert "notify_ordering_user" in res
    assert res["notify_ordering_user"]["phone"] == MAA_PHONE
    assert "reject" in res["notify_ordering_user"]["text"].lower()


# ============================================================================
# Test 4: Below threshold → auto-approves, no payer notification
# ============================================================================

@pytest.mark.asyncio
async def test_below_threshold_auto_approves_no_payer_notify(csm, monkeypatch):
    """Cart below threshold → auto-confirms, no payer notification.

    Two-step flow:
      1. order_items → AWAITING_CONFIRMATION
      2. confirm → COMPLETE (auto-approve, total < threshold)
    """
    from packages.core import pipeline as pipeline_mod
    from app.agents import brain as brain_mod
    from app.agents.brain import BrainAction, ParsedItem

    _setup_mocks(monkeypatch)

    cart_lines, quote = _make_low_value_quote()

    async def _fake_rq(*_a, **_k):
        return (cart_lines, quote.cart_handle, quote)

    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_rq)

    # Brain mock: first call → order_items, second call → confirm
    call_count = {"n": 0}

    async def _fake_decide(text, *_a, **_k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return BrainAction(
                action="order_items",
                items=[ParsedItem(text="doodh", quantity=1)],
                domain_hint="grocery",
                detected_language="hi-en",
                confidence=0.95,
                reasoning="test",
            )
        return BrainAction(
            action="confirm",
            detected_language="hi-en",
            confidence=0.95,
            reasoning="test",
        )

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)

    # Step 1: Maa orders
    res1 = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=MAA_PHONE,
        text="doodh bhej do",
        whatsapp_message_id="wamid.test.maa.low1",
        location=None,
    )
    assert res1["state"] == "AWAITING_CONFIRMATION"
    assert "notify_payer" not in res1

    # Step 2: Maa confirms (below threshold → auto-executes)
    res2 = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=MAA_PHONE,
        text="haan bhej do",
        whatsapp_message_id="wamid.test.maa.low2",
        location=None,
    )
    assert res2["state"] == "COMPLETE"
    assert "notify_payer" not in res2


# ============================================================================
# Test 5: Proactive occasion hint reaches brain
# ============================================================================

@pytest.mark.asyncio
async def test_occasion_hint_injected_near_festival(csm, monkeypatch):
    """When today is near a festival, build_occasion_hint passes hint to brain."""
    from packages.core import pipeline as pipeline_mod
    from app.agents import brain as brain_mod
    from app.agents.brain import BrainAction

    _setup_mocks(monkeypatch)

    # Override build_occasion_hint to return a deterministic hint
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint",
                        lambda: "Diwali in 5 days! Suggest kaju katli.")

    captured = {}

    async def _spy_decide(*args, **kwargs):
        captured["occasion_hint"] = kwargs.get("occasion_hint")
        return BrainAction(
            action="greet",
            detected_language="hi-en",
            confidence=0.9,
            reply_text="Namaste! Kya chahiye?",
            reasoning="test",
        )

    monkeypatch.setattr(brain_mod, "decide", _spy_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _spy_decide)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=MAA_PHONE,
        text="namaste",
        whatsapp_message_id="wamid.test.hint",
        location=None,
    )

    # The key assertion: occasion_hint was passed to the brain
    assert captured.get("occasion_hint") == "Diwali in 5 days! Suggest kaju katli."
    # greet handler returns IDLE — that's fine, we only care about the hint


@pytest.mark.asyncio
async def test_no_occasion_hint_when_far_from_festival(csm, monkeypatch):
    """When today is far from any festival, occasion_hint is None."""
    from packages.core import pipeline as pipeline_mod
    from app.agents import brain as brain_mod
    from app.agents.brain import BrainAction

    _setup_mocks(monkeypatch)

    # Override build_occasion_hint to return None
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)

    captured = {}

    async def _spy_decide(*args, **kwargs):
        captured["occasion_hint"] = kwargs.get("occasion_hint")
        return BrainAction(
            action="greet",
            detected_language="hi-en",
            confidence=0.9,
            reply_text="Namaste!",
            reasoning="test",
        )

    monkeypatch.setattr(brain_mod, "decide", _spy_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _spy_decide)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone=MAA_PHONE,
        text="hello",
        whatsapp_message_id="wamid.test.nohint",
        location=None,
    )

    assert captured.get("occasion_hint") is None
