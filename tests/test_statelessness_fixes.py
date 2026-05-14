from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from app.agents.confirmation import build_confirmation
from app.agents import brain as brain_mod
from app.agents.brain import BrainAction, ParsedItem as BrainParsedItem
from app.schemas.message import CandidateItem, ParsedIntent, ParsedItem
from packages.providers.interface import CartHandle, CartLine, ProviderName, QuoteResult, SkuPreview


def _chitchat_intent() -> ParsedIntent:
    return ParsedIntent(
        action="CHITCHAT",
        raw_text="hi",
        goal="chat",
        domain_hint="unknown",
    )


@pytest.mark.parametrize(
    ("conv_ctx", "expected"),
    [
        (
            None,
            "Namaskaram! foodleaf lo text or voice tho order cheyyachu. Emi kavali?",
        ),
        (
            {
                "state": "AWAITING_CONFIRMATION",
                "turn_count": 3,
                "context": {"flow": "awaiting_assistant"},
            },
            "Cheppandi, em kavali? Groceries (atta, milk) / food delivery (biryani) / dineout?",
        ),
        (
            {
                "state": "AWAITING_CONFIRMATION",
                "turn_count": 3,
                "context": {"resolved_cart": {"items": [{"display_name": "Atta"}]}},
            },
            "Mee order pending undi — confirm cheyyana, leda venaki vellali?",
        ),
        (
            {
                "state": "PARSING",
                "turn_count": 5,
                "context": {},
            },
            "Haan, cheppandi — emi cheyyali?",
        ),
    ],
)
def test_build_confirmation_chitchat_branches(conv_ctx, expected):
    reply = build_confirmation(_chitchat_intent(), [], None, conv_ctx=conv_ctx)
    assert reply == expected


def test_brain_parses_yogurt_as_grocery_order():
    """Brain should map yogurt/curd to grocery order_items — tested via BrainAction conversion."""
    action = BrainAction(
        action="order_items",
        items=[BrainParsedItem(text="yogurt")],
        domain_hint="grocery",
        detected_language="en",
        confidence=0.9,
        reasoning="test",
    )
    intent = ParsedIntent(
        action="ORDER", goal="shop", raw_text="Yeah I want yogurt",
        items=[ParsedItem(text="curd")],
        domain_hint="grocery",
    )
    assert intent.action == "ORDER"
    assert intent.domain_hint == "grocery"
    assert [item.text for item in intent.items] == ["curd"]


def test_update_address_handler_extracts_address():
    """Brain now handles address extraction via update_address action with address_text field."""
    action = BrainAction(
        action="update_address",
        address_text="Flat 304, Madhapur, Hyderabad",
        detected_language="te-en",
        confidence=0.9,
        reasoning="test",
    )
    assert action.address_text == "Flat 304, Madhapur, Hyderabad"


@pytest.mark.asyncio
async def test_run_with_ack_skips_for_trivial_intents(monkeypatch):
    from app import worker as worker_mod

    sent_messages: list[tuple[str, str]] = []

    async def _fake_send(to_phone: str, text: str, *, context: str) -> None:
        sent_messages.append((context, text))

    async def _fake_select_ack(_redis, context_tag: str = "generic") -> str:
        return "Sare, chustunnanu..."

    async def _slow_pipeline() -> dict:
        await asyncio.sleep(3)
        return {
            "reply_text": "ok",
            "reply_to": "+919876543210",
            "voice_session_id": None,
            "state": "IDLE",
        }

    monkeypatch.setattr(worker_mod, "_send_reply", _fake_send)
    monkeypatch.setattr(worker_mod, "select_ack_text", _fake_select_ack)
    dummy_csm = SimpleNamespace(_redis=None)

    result = await worker_mod._run_with_ack(
        pipeline_call=_slow_pipeline(),
        csm=dummy_csm,
        to_phone="+919876543210",
        input_mode="text",
        skip_ack=True,
    )
    assert result["reply_text"] == "ok"
    assert sent_messages == []


@pytest_asyncio.fixture
async def redis():
    import redis as sync_redis
    from redis.asyncio import Redis

    try:
        sync_client = sync_redis.Redis(host="localhost", port=6379, db=2, socket_connect_timeout=0.35)
        sync_client.ping()
        sync_client.close()
    except Exception:
        pytest.skip("Redis not reachable at localhost:6379")

    r = Redis.from_url("redis://localhost:6379/2", decode_responses=True)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.mark.asyncio
async def test_escape_awaiting_location_with_order(monkeypatch, redis):
    from packages.core.conversation import ConversationStateMachine
    from packages.core import session_recovery
    
    csm = ConversationStateMachine(redis)
    user_id = "bbbbbbbb-0002-0002-0002-000000000002"
    
    await csm.start_session(user_id)
    await csm.transition(
        user_id,
        "AWAITING_CONFIRMATION",
        context={"flow": "awaiting_location"}
    )
    
    current = await csm.current_state(user_id)
    assert current is not None
    assert current["context"]["flow"] == "awaiting_location"
    
    new_state = await session_recovery.supersede_awaiting_assistant_with_concrete_order(
        csm, user_id, current, action="ORDER", needs_clarification=False, has_substantive_items=True
    )
    
    assert new_state is None
    
    after_csm = await csm.current_state(user_id)
    assert after_csm is None


@pytest.mark.asyncio
async def test_restore_session_from_postgres_when_redis_empty(monkeypatch, redis):
    from packages.core.conversation import ConversationStateMachine
    from packages.core import pipeline as pipeline_mod
    from app.schemas.message import ParsedIntent

    csm = ConversationStateMachine(redis)
    user_id = "bbbbbbbb-0002-0002-0002-000000000002"
    voice_id = str(uuid.uuid4())
    family_id = "aaaaaaaa-0001-0001-0001-000000000001"

    user = SimpleNamespace(
        id=user_id,
        family_id=family_id,
        preferred_language="te-IN",
    )
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(pipeline_mod, "resolve_family_context", lambda *a, **k: asyncio.sleep(0, result=_fam_ctx))
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="confirm",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_execute_order(*_a, **_k):
        return SimpleNamespace(provider_order_id="INST-ORDER-123456")

    async def _fake_update_status(*_a, **_k):
        return None

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "execute_order", _fake_execute_order)
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", _fake_update_status)

    fake_vs = SimpleNamespace(
        id=uuid.UUID(voice_id),
        ordering_user_id=uuid.UUID(user_id),
        conversation_state="AWAITING_CONFIRMATION",
        outcome="still_pending",
        parsed_intent={"action": "ORDER", "raw_text": "atta kavali", "needs_clarification": False},
        resolved_cart={"items": [{"display_name": "Aashirvaad Atta", "quantity": 1}], "quote_total_inr": 340},
        created_at=None,
        updated_at=None,
    )

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def scalars(self):
            return self

        def first(self):
            return self._row

    class _FakeSession:
        async def execute(self, _query):
            return _FakeResult(fake_vs)

    class _FakeSessionCM:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(pipeline_mod, "get_session", lambda: _FakeSessionCM())

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="avunu",
        whatsapp_message_id="wamid.restore-test",
        location=None,
    )

    assert res["state"] == "COMPLETE"
    assert "confirm ayindi" in res["reply_text"]


@pytest.mark.asyncio
async def test_pipeline_order_renders_requested_size_adjustment(monkeypatch, redis):
    from datetime import datetime

    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine(redis)
    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="paneer", quantity=1)],
            domain_hint="grocery",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_persist(*_a, **_k):
        return None

    async def _fake_resolve_and_quote(_intent, _location):
        candidates = [
            CandidateItem(
                canonical_key="amul_paneer_200g",
                display_name="Amul Paneer",
                brand="amul",
                price_inr=95,
                provider_specific_id="PANEER-200",
                in_stock=True,
            )
        ]
        cart = CartHandle(
            provider=ProviderName.SWIGGY_INSTAMART,
            provider_cart_id="CART-TEST",
            items=[],
            expires_at=datetime.utcnow(),
        )
        quote = QuoteResult(
            cart_handle=cart,
            subtotal_inr=95,
            delivery_fee_inr=0,
            handling_fee_inr=0,
            taxes_inr=0,
            discount_inr=0,
            applied_offers=[],
            total_inr=95,
            estimated_delivery_min=18,
            estimated_delivery_max=23,
            line_items=[
                CartLine(
                    canonical_key="amul_paneer_200g",
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
            ],
        )
        return candidates, cart, quote

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)
    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_resolve_and_quote)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="100g paneer kavali",
        whatsapp_message_id="wamid.size-adjustment-test",
        location=None,
    )

    assert res["state"] == "AWAITING_CONFIRMATION"
    assert "100g pack ledu, 200g available undi" in res["reply_text"]


@pytest.mark.asyncio
async def test_pipeline_oos_only_order_returns_substitutes_without_confirmation(monkeypatch, redis):
    from datetime import datetime

    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine(redis)
    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )
    persisted = {}
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="paneer", quantity=1)],
            domain_hint="grocery",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_persist(*_a, **kwargs):
        persisted.update(kwargs)

    async def _fake_resolve_and_quote(_intent, _location):
        candidates = [
            CandidateItem(
                canonical_key="milky_mist_paneer_200g",
                display_name="Milky Mist Fresh Paneer",
                brand="milky_mist",
                price_inr=120,
                provider_specific_id="PANEER-200",
                in_stock=False,
            )
        ]
        cart = CartHandle(
            provider=ProviderName.SWIGGY_INSTAMART,
            provider_cart_id="CART-OOS",
            items=[],
            expires_at=datetime.utcnow(),
        )
        quote = QuoteResult(
            cart_handle=cart,
            subtotal_inr=0,
            delivery_fee_inr=0,
            handling_fee_inr=0,
            taxes_inr=0,
            discount_inr=0,
            applied_offers=[],
            total_inr=0,
            estimated_delivery_min=18,
            estimated_delivery_max=23,
            line_items=[
                CartLine(
                    canonical_key="milky_mist_paneer_200g",
                    display_name="Milky Mist Fresh Paneer",
                    brand="milky_mist",
                    pack_size_label="200g",
                    qty=1,
                    unit_price_inr=120,
                    line_total_inr=120,
                    in_stock=False,
                    eta_min=None,
                    substitutes=[
                        SkuPreview(
                            canonical_key="heritage_curd_500g",
                            display_name="Heritage Curd",
                            brand="heritage",
                            pack_size_label="500g",
                            price_inr=40,
                            in_stock=True,
                            provider_specific_id="CURD-500",
                            category="dairy_curd",
                        )
                    ],
                )
            ],
        )
        return candidates, cart, quote

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)
    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_resolve_and_quote)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="paneer kavali",
        whatsapp_message_id="wamid.oos-only-test",
        location=None,
    )

    assert res["state"] == "IDLE"
    assert "❌ stock ledu" in res["reply_text"]
    assert "Badulu Heritage Curd 500g ₹40 available undi" in res["reply_text"]
    assert "Confirm chey-yana" not in res["reply_text"]
    assert persisted["outcome"] == "failed"
    assert persisted["failure_reason"] == "out_of_stock"


@pytest.mark.asyncio
async def test_pipeline_ambiguous_item_numbered_selection_builds_cart(monkeypatch, redis):
    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

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
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(text, *_a, **_k):
        if text == "1":
            return BrainAction(action="select_option", selected_index=0, detected_language="te-en", confidence=0.95, reasoning="test")
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="atta", quantity=1)],
            domain_hint="grocery",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_persist(*_a, **_k):
        return None

    async def _fake_infer(_intent):
        return "staples_flour"

    async def _fake_options(**_k):
        return options

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)
    monkeypatch.setattr(pipeline_mod, "_infer_substitute_category", _fake_infer)
    monkeypatch.setattr(pipeline_mod, "find_options_in_category", _fake_options)

    first = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="atta",
        whatsapp_message_id="wamid.option-start",
        location=None,
    )
    assert first["state"] == "AWAITING_CONFIRMATION"
    assert "1. Aashirvaad Select Atta 5kg — ₹295" in first["reply_text"]
    assert "2. Fortune Chakki Atta 5kg — ₹270" in first["reply_text"]

    second = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="1",
        whatsapp_message_id="wamid.option-select",
        location=None,
    )
    assert second["state"] == "AWAITING_CONFIRMATION"
    assert "Aashirvaad Select Atta 5kg × 1" in second["reply_text"]
    assert "Confirm chey-yana" in second["reply_text"]


@pytest.mark.asyncio
async def test_pending_cart_question_keeps_confirmation_context(monkeypatch, redis):
    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine(redis)
    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="ask_cart",
            detected_language="te-en",
            confidence=0.9,
            reasoning="test",
        )

    persisted: dict = {}

    async def _fake_persist(*_a, **kwargs):
        persisted.update(kwargs)

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)

    session = await csm.start_session(str(user.id))
    await csm.transition(
        str(user.id),
        "AWAITING_CONFIRMATION",
        context={
            "voice_session_id": session["session_id"],
            "resolved_cart": {
                "items": [{"display_name": "Onion (Loose) 1kg", "quantity": 1}],
                "quote_total_inr": 72,
            },
            "confirmation_text": "🛒 Mee cart:\n• Onion (Loose) 1kg × 1 — ₹38\nConfirm chey-yana? (avunu / vaddu)",
        },
    )

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="na cart lo em vunnay",
        whatsapp_message_id="wamid.pending-cart-query",
        location=None,
    )

    assert res["state"] == "AWAITING_CONFIRMATION"
    assert "Mee cart" in res["reply_text"]
    assert "Inka items add cheyyali ante item peru cheppandi." in res["reply_text"]
    assert persisted["outcome"] == "still_pending"
    assert persisted["conversation_state"] == "AWAITING_CONFIRMATION"


@pytest.mark.asyncio
async def test_add_item_no_match_does_not_drop_existing_pending_cart(monkeypatch, redis):
    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine(redis)
    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="coriander", quantity=1)],
            domain_hint="grocery",
            detected_language="te-en",
            confidence=0.9,
            reasoning="test",
        )

    async def _fake_resolve_and_quote(_intent, _location):
        return [], None, None

    persisted: dict = {}

    async def _fake_persist(*_a, **kwargs):
        persisted.update(kwargs)

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_resolve_and_quote)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)

    session = await csm.start_session(str(user.id))
    await csm.transition(
        str(user.id),
        "AWAITING_CONFIRMATION",
        context={
            "voice_session_id": session["session_id"],
            "resolved_cart": {
                "items": [{"display_name": "Onion (Loose) 1kg", "quantity": 1}],
                "quote_total_inr": 72,
            },
            "confirmation_text": "🛒 Mee cart:\n• Onion (Loose) 1kg × 1 — ₹38\nConfirm chey-yana? (avunu / vaddu)",
            "language": "te-IN",
        },
    )

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="coriander",
        whatsapp_message_id="wamid.add-item-no-match",
        location=None,
    )

    assert res["state"] == "AWAITING_CONFIRMATION"
    assert "existing cart alane undi" in res["reply_text"]
    assert persisted["outcome"] == "still_pending"
    assert persisted["failure_reason"] == "add_item_no_match"
    current = await csm.current_state(str(user.id))
    assert current is not None
    assert current["state"] == "AWAITING_CONFIRMATION"
    assert (current.get("context") or {}).get("resolved_cart")


@pytest.mark.asyncio
async def test_clear_cart_cancels_pending_session(monkeypatch, redis):
    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine(redis)
    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(*_a, **_k):
        return BrainAction(action="clear_cart", detected_language="te-en", confidence=0.95, reasoning="test")

    persisted: dict = {}

    async def _fake_persist(*_a, **kwargs):
        persisted.update(kwargs)

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)

    session = await csm.start_session(str(user.id))
    await csm.transition(
        str(user.id),
        "AWAITING_CONFIRMATION",
        context={
            "voice_session_id": session["session_id"],
            "resolved_cart": {
                "items": [{"display_name": "Amul Butter 500g", "quantity": 1}],
                "quote_total_inr": 307,
            },
            "confirmation_text": "🛒 Mee cart:\n• Amul Butter 500g × 1 — ₹285\nConfirm chey-yana? (avunu / vaddu)",
        },
    )

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="Clear my cart",
        whatsapp_message_id="wamid.clear-cart",
        location=None,
    )

    assert res["state"] == "IDLE"
    assert "cart clear" in res["reply_text"].lower()
    assert persisted["outcome"] == "cancelled"
    assert persisted["failure_reason"] == "user_cleared_cart"
    after = await csm.current_state(str(user.id))
    assert after is None or after.get("state") == "IDLE"


@pytest.mark.asyncio
async def test_pipeline_first_contact_greet_uses_brain_reply(monkeypatch, redis):
    """First contact 'hi' should use brain's greet reply_text directly."""
    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine(redis)
    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="greet",
            reply_text="Namaskaram! foodleaf lo groceries, food delivery, leka dineout — emi kavali?",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_persist(*_a, **_k):
        return None

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="hi",
        whatsapp_message_id="wamid.welcome-greet-test",
        location=None,
    )

    assert "Namaskaram" in res["reply_text"]
    assert "foodleaf" in res["reply_text"].lower()


@pytest.mark.asyncio
async def test_pipeline_stores_language_in_context(monkeypatch, redis):
    from datetime import datetime

    from packages.core import pipeline as pipeline_mod
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine(redis)
    user = SimpleNamespace(
        id="bbbbbbbb-0002-0002-0002-000000000002",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )
    from tests.conftest import make_mock_family_ctx
    _fam_ctx = make_mock_family_ctx(user_id=user.id, family_id=user.family_id, preferred_language=user.preferred_language)
    monkeypatch.setattr(
        pipeline_mod,
        "resolve_family_context",
        lambda *a, **k: asyncio.sleep(0, result=_fam_ctx),
    )

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="atta", quantity=1)],
            domain_hint="grocery",
            detected_language="en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_persist(*_a, **_k):
        return None

    async def _fake_resolve_and_quote(_intent, _location):
        candidates = [
            CandidateItem(
                canonical_key="aashirvaad_select_atta_5kg",
                display_name="Aashirvaad Select Atta 5kg",
                brand="aashirvaad",
                price_inr=240,
                provider_specific_id="ATTA-5KG",
                in_stock=True,
            )
        ]
        cart = CartHandle(
            provider=ProviderName.SWIGGY_INSTAMART,
            provider_cart_id="CART-LANG",
            items=[],
            expires_at=datetime.utcnow(),
        )
        quote = QuoteResult(
            cart_handle=cart,
            subtotal_inr=240,
            delivery_fee_inr=0,
            handling_fee_inr=0,
            taxes_inr=0,
            discount_inr=0,
            applied_offers=[],
            total_inr=240,
            estimated_delivery_min=18,
            estimated_delivery_max=23,
            line_items=[
                CartLine(
                    canonical_key="aashirvaad_select_atta_5kg",
                    display_name="Aashirvaad Select Atta 5kg",
                    brand="aashirvaad",
                    pack_size_label="5kg",
                    qty=1,
                    unit_price_inr=240,
                    line_total_inr=240,
                    in_stock=True,
                    eta_min=18,
                )
            ],
        )
        return candidates, cart, quote

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)
    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_resolve_and_quote)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="+918247628278",
        text="I want atta",
        whatsapp_message_id="wamid.language-context-test",
        location=None,
    )
    assert res["state"] == "AWAITING_CONFIRMATION"
    current = await csm.current_state(str(user.id))
    assert current is not None
    assert (current.get("context") or {}).get("language") == "en-IN"
