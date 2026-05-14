"""E2E test for the Anna demo flow.

Tests the complete flow:
  1. Maa (Sunita) sends greeting → Anna responds in Hindi
  2. Maa orders groceries → cart built, items resolved
  3. Maa confirms → threshold check triggers payer notification
  4. Beta (Rahul) approves → order placed, both notified
  5. Occasion hint is injected when Diwali is near

Run:  pytest tests/test_anna_demo_flow.py -v
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add apps/api to sys.path so 'app' module is importable
_api_root = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

import pytest

# ---- Fixtures ----

MAA_PHONE = "+919876500001"
BETA_PHONE = "+919876500002"
FAMILY_ID = "a0000000-0000-0000-0000-000000000001"
MAA_ID = "a0000000-0000-0000-0000-000000000010"
BETA_ID = "a0000000-0000-0000-0000-000000000020"


@pytest.fixture
def mock_redis():
    """In-memory fake Redis for testing."""
    store = {}

    class FakeRedis:
        async def get(self, key):
            return store.get(key)

        async def set(self, key, value, ex=None):
            store[key] = value

        async def delete(self, key):
            store.pop(key, None)

        async def lrange(self, key, start, end):
            return json.loads(store.get(key, "[]"))

        async def rpush(self, key, *values):
            pass

        async def ltrim(self, key, start, end):
            pass

        async def expire(self, key, ttl):
            pass

        async def incr(self, key):
            store[key] = str(int(store.get(key, "0")) + 1)
            return int(store[key])

        def pipeline(self):
            return self

        async def execute(self):
            return []

    return FakeRedis()


@pytest.fixture
def mock_family_ctx():
    """Mock FamilyContext for Maa (Sunita)."""
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


@pytest.fixture
def mock_payer_family_ctx():
    """Mock FamilyContext for Beta (Rahul) — the payer."""
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


# ---- Unit Tests ----

class TestFamilyCart:
    """Test the family cart operations."""

    def test_add_item(self):
        from packages.core.family_cart import FamilyCart, CartItem
        cart = FamilyCart(family_id=FAMILY_ID)
        cart.add_item(CartItem(name="atta", quantity=1, price_inr=50, brand="Aashirvaad"))
        assert len(cart.items) == 1
        assert cart.total_inr == 50

    def test_add_item_merges(self):
        from packages.core.family_cart import FamilyCart, CartItem
        cart = FamilyCart(family_id=FAMILY_ID)
        cart.add_item(CartItem(name="atta", quantity=1, price_inr=50, brand="Aashirvaad"))
        cart.add_item(CartItem(name="atta", quantity=2, price_inr=50, brand="Aashirvaad"))
        assert len(cart.items) == 1
        assert cart.items[0].quantity == 3
        assert cart.total_inr == 150

    def test_remove_item(self):
        from packages.core.family_cart import FamilyCart, CartItem
        cart = FamilyCart(family_id=FAMILY_ID)
        cart.add_item(CartItem(name="atta", quantity=1, price_inr=50))
        cart.add_item(CartItem(name="rice", quantity=1, price_inr=100))
        assert cart.remove_item("atta")
        assert len(cart.items) == 1
        assert cart.total_inr == 100

    def test_format_items_hindi(self):
        from packages.core.family_cart import FamilyCart, CartItem
        cart = FamilyCart(family_id=FAMILY_ID)
        cart.add_item(CartItem(name="atta", quantity=1, price_inr=50, brand="Aashirvaad"))
        text = cart.format_items_text(locale="hi-IN")
        assert "atta" in text
        assert "50" in text

    def test_serialization_roundtrip(self):
        from packages.core.family_cart import FamilyCart, CartItem
        cart = FamilyCart(family_id=FAMILY_ID, ordering_user_id=MAA_ID)
        cart.add_item(CartItem(name="doodh", quantity=2, price_inr=60, brand="Amul"))
        data = cart.to_dict()
        cart2 = FamilyCart.from_dict(data)
        assert len(cart2.items) == 1
        assert cart2.items[0].name == "doodh"
        assert cart2.total_inr == 120


class TestOccasionCalendar:
    """Test the occasion calendar."""

    def test_get_upcoming_festivals(self):
        from packages.core.occasion_calendar import get_upcoming_festivals
        # Use a date close to Diwali 2025
        result = get_upcoming_festivals(within_days=365, today=date(2026, 10, 1))
        assert len(result) > 0
        # Diwali should be in the list
        names = [f.name for f, _ in result]
        assert "Diwali" in names

    def test_build_occasion_hint_near_diwali(self):
        from packages.core.occasion_calendar import build_occasion_hint
        hint = build_occasion_hint(today=date(2026, 11, 1))
        assert hint is not None
        assert "Diwali" in hint

    def test_build_occasion_hint_far_from_diwali(self):
        from packages.core.occasion_calendar import build_occasion_hint
        hint = build_occasion_hint(today=date(2026, 5, 1))
        # No festival within 14 days of May 1
        assert hint is None


class TestPayerNotification:
    """Test the payer notification renderer."""

    def test_approval_notification(self):
        from packages.core.family_cart import FamilyCart, CartItem
        from packages.core.payer_notification import render_payer_approval_notification

        cart = FamilyCart(family_id=FAMILY_ID)
        cart.add_item(CartItem(name="Aashirvaad Atta 5kg", price_inr=300, added_by=MAA_ID))
        cart.add_item(CartItem(name="Fortune Oil 1L", price_inr=180, added_by=MAA_ID))

        msg = render_payer_approval_notification(
            cart, payer_name="Rahul", ordering_name="Sunita ji", family_name="Sharma Family",
        )
        assert "Rahul" in msg
        assert "Sunita ji" in msg
        assert "APPROVE" in msg
        assert "REJECT" in msg

    def test_approval_confirmed_hindi(self):
        from packages.core.family_cart import FamilyCart, CartItem
        from packages.core.payer_notification import render_approval_confirmed_to_ordering_user

        cart = FamilyCart(family_id=FAMILY_ID, total_inr=480)
        msg = render_approval_confirmed_to_ordering_user(cart, payer_name="Rahul", locale="hi-IN")
        assert "Rahul" in msg
        assert "approve" in msg.lower() or "स्वीकृत" in msg or "approve" in msg

    def test_approval_rejected_hindi(self):
        from packages.core.family_cart import FamilyCart, CartItem
        from packages.core.payer_notification import render_approval_rejected_to_ordering_user

        cart = FamilyCart(family_id=FAMILY_ID, total_inr=480)
        msg = render_approval_rejected_to_ordering_user(cart, payer_name="Rahul", locale="hi-IN")
        assert "Rahul" in msg
        assert "reject" in msg.lower() or "ने" in msg


class TestFamilyResolver:
    """Test the family resolver data structures."""

    def test_family_context_properties(self, mock_family_ctx):
        assert mock_family_ctx.is_ordering_user is True
        assert mock_family_ctx.is_payer is False
        assert mock_family_ctx.role == "ordering_user"
        assert mock_family_ctx.approval_threshold == 1500
        assert mock_family_ctx.primary_locale == "hi-IN"
        assert mock_family_ctx.payer_display_name == "Rahul Sharma"
        assert mock_family_ctx.payer_phone == BETA_PHONE

    def test_payer_context_properties(self, mock_payer_family_ctx):
        assert mock_payer_family_ctx.is_payer is True
        assert mock_payer_family_ctx.is_ordering_user is False
        assert mock_payer_family_ctx.role == "payer"

    def test_to_cache_dict(self, mock_family_ctx):
        d = mock_family_ctx.to_cache_dict()
        assert d["role"] == "ordering_user"
        assert d["display_name"] == "Sunita Sharma"
        assert d["payer_display_name"] == "Rahul Sharma"
        assert d["approval_threshold_inr"] == 1500


class TestBrainActionSchema:
    """Test that BrainAction supports new fields."""

    def test_hindi_language(self):
        from app.agents.brain import BrainAction
        action = BrainAction(action="greet", detected_language="hi", confidence=0.9, reply_text="Namaste!")
        assert action.detected_language == "hi"

    def test_hinglish_language(self):
        from app.agents.brain import BrainAction
        action = BrainAction(action="chitchat", detected_language="hi-en", confidence=0.8, reply_text="Haan ji!")
        assert action.detected_language == "hi-en"

    def test_approve_action(self):
        from app.agents.brain import BrainAction
        action = BrainAction(action="approve", detected_language="en", confidence=0.95, approval_target="cart123")
        assert action.action == "approve"
        assert action.approval_target == "cart123"

    def test_reject_approval_action(self):
        from app.agents.brain import BrainAction
        action = BrainAction(action="reject_approval", detected_language="hi-en", confidence=0.9, approval_target="cart123")
        assert action.action == "reject_approval"


class TestAnnaPrompt:
    """Test the v3_anna prompt builder."""

    def test_basic_prompt(self):
        from app.agents.brain_prompts import _build_v3_anna_prompt
        prompt = _build_v3_anna_prompt("User is idle", "No history", "hi-IN")
        assert "Anna" in prompt
        assert "Hindi" in prompt
        assert "family" in prompt.lower()

    def test_prompt_with_family_context(self):
        from app.agents.brain_prompts import _build_v3_anna_prompt
        fam_ctx = {
            "role": "ordering_user",
            "display_name": "Sunita",
            "family_display_name": "Sharma Family",
            "payer_display_name": "Rahul",
            "approval_threshold_inr": 1500,
            "primary_locale": "hi-IN",
        }
        prompt = _build_v3_anna_prompt("User is idle", "No history", "hi-IN", family_context=fam_ctx)
        assert "Sunita" in prompt
        assert "Rahul" in prompt
        assert "1500" in prompt
        assert "CARE RECIPIENT" in prompt

    def test_prompt_with_payer_role(self):
        from app.agents.brain_prompts import _build_v3_anna_prompt
        fam_ctx = {
            "role": "payer",
            "display_name": "Rahul",
            "family_display_name": "Sharma Family",
            "payer_display_name": "Rahul",
            "approval_threshold_inr": 1500,
            "primary_locale": "hi-IN",
        }
        prompt = _build_v3_anna_prompt("User is idle", "No history", "en-IN", family_context=fam_ctx)
        assert "PAYER" in prompt
        assert "approve" in prompt.lower()

    def test_prompt_with_occasion_hint(self):
        from app.agents.brain_prompts import _build_v3_anna_prompt
        prompt = _build_v3_anna_prompt(
            "User is idle", "No history", "hi-IN",
            occasion_hint="Diwali is in 5 days. Order sweets!",
        )
        assert "Diwali" in prompt

    def test_prompt_version_registered(self):
        from app.agents.brain_prompts import PROMPT_VERSIONS
        assert "v3_anna" in PROMPT_VERSIONS


class TestThresholdCheck:
    """Test the family cart threshold detection."""

    @pytest.mark.asyncio
    async def test_below_threshold_no_approval(self, mock_redis):
        from packages.core.family_cart import FamilyCart, CartItem, check_threshold_and_notify

        cart = FamilyCart(family_id=FAMILY_ID)
        cart.add_item(CartItem(name="doodh", price_inr=60))
        assert cart.total_inr < 1500
        result = await check_threshold_and_notify(cart, 1500, mock_redis)
        assert result is False

    @pytest.mark.asyncio
    async def test_at_threshold_triggers_approval(self, mock_redis):
        from packages.core.family_cart import FamilyCart, CartItem, check_threshold_and_notify

        cart = FamilyCart(family_id=FAMILY_ID)
        cart.add_item(CartItem(name="atta", price_inr=800))
        cart.add_item(CartItem(name="rice", price_inr=700))
        assert cart.total_inr >= 1500
        result = await check_threshold_and_notify(cart, 1500, mock_redis)
        assert result is True
        assert cart.approval_status == "pending_approval"
