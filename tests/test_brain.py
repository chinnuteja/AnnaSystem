"""Unit tests for the FoodLeaf Brain — tests decide(), confidence gate, caching, and prompt versioning.

All LLM calls are mocked — no real API keys needed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from app.agents.brain import (
    BrainAction,
    ParsedItem,
    _cache_fingerprint,
    _cache_key,
    _CONFIDENCE_THRESHOLD,
    decide,
)
from app.agents.brain_prompts import get_prompt_builder, ACTIVE_VERSION, PROMPT_VERSIONS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_action(**overrides) -> BrainAction:
    defaults = dict(
        action="greet",
        reply_text="Namaskaram!",
        detected_language="te-en",
        confidence=0.9,
        reasoning="test",
    )
    defaults.update(overrides)
    return BrainAction(**defaults)


# ---------------------------------------------------------------------------
# Prompt Versioning
# ---------------------------------------------------------------------------

class TestPromptVersioning:
    def test_active_version_is_v2(self):
        assert ACTIVE_VERSION == "v2"

    def test_all_versions_have_builders(self):
        for v in PROMPT_VERSIONS:
            builder, returned_v = get_prompt_builder(v)
            assert returned_v == v
            prompt = builder("test state", "test history", "te-IN")
            assert "foodleaf" in prompt or "Anna" in prompt
            assert "test state" in prompt
            assert "test history" in prompt

    def test_v2_has_edge_case_rules(self):
        builder, _ = get_prompt_builder("v2")
        prompt = builder("state", "history", "te-IN")
        assert "AMENDMENTS ADD TO CART" in prompt
        assert "REMOVALS ARE CORRECTIONS" in prompt
        assert "NO CONFIRM WITHOUT PENDING CART" in prompt
        assert "MID-FLOW GREETINGS" in prompt
        assert "PRESERVE TELUGU PRODUCT NAMES" in prompt

    def test_v1_lacks_edge_case_rules(self):
        builder, _ = get_prompt_builder("v1")
        prompt = builder("state", "history", "te-IN")
        assert "AMENDMENTS ADD TO CART" not in prompt

    def test_invalid_version_raises(self):
        with pytest.raises(ValueError, match="Unknown prompt version"):
            get_prompt_builder("v99")


# ---------------------------------------------------------------------------
# Cache Helpers
# ---------------------------------------------------------------------------

class TestCacheHelpers:
    def test_fingerprint_idle(self):
        assert _cache_fingerprint(None) == "idle"

    def test_fingerprint_with_state(self):
        state = {
            "state": "AWAITING_CONFIRMATION",
            "context": {
                "flow": "option_selection",
                "visible_options": [{"name": "a"}, {"name": "b"}],
                "options": [{"name": "x"}],
                "resolved_cart": {"items": [{"name": "atta"}, {"name": "milk"}]},
            },
        }
        fp = _cache_fingerprint(state)
        assert "AWAITING_CONFIRMATION" in fp
        assert "option_selection" in fp
        # 2 visible, 1 option, 2 cart items
        assert "2|1|2" in fp

    def test_cache_key_deterministic(self):
        k1 = _cache_key("hello", "idle")
        k2 = _cache_key("hello", "idle")
        assert k1 == k2
        assert k1.startswith("brain_cache:")

    def test_cache_key_differs_for_different_text(self):
        k1 = _cache_key("hello", "idle")
        k2 = _cache_key("goodbye", "idle")
        assert k1 != k2

    def test_cache_key_differs_for_different_state(self):
        k1 = _cache_key("hello", "idle")
        k2 = _cache_key("hello", "AWAITING_CONFIRMATION|option_selection|2|0|1")
        assert k1 != k2


# ---------------------------------------------------------------------------
# Confidence Gate
# ---------------------------------------------------------------------------

class TestConfidenceGate:
    @pytest.mark.asyncio
    async def test_low_confidence_overridden_to_unclear(self):
        """Action with confidence < threshold should be overridden to unclear."""
        low_conf = _make_action(action="order_items", confidence=0.3, items=[ParsedItem(text="atta")])

        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=low_conf):
            result = await decide("atta", conversation_history=[], current_state=None)

        assert result.action == "unclear"
        assert result.confidence == 0.3
        assert "overridden" in result.reasoning.lower() or "low confidence" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_greet_exempt_from_confidence_gate(self):
        """greet with low confidence should NOT be overridden."""
        low_greet = _make_action(action="greet", confidence=0.3, reply_text="Hi!")

        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=low_greet):
            result = await decide("hi", conversation_history=[], current_state=None)

        assert result.action == "greet"

    @pytest.mark.asyncio
    async def test_unclear_exempt_from_confidence_gate(self):
        """unclear with low confidence should stay as unclear (no double-override)."""
        already_unclear = _make_action(
            action="unclear", confidence=0.2,
            clarification_question="Emi kavali?",
        )

        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=already_unclear):
            result = await decide("???", conversation_history=[], current_state=None)

        assert result.action == "unclear"

    @pytest.mark.asyncio
    async def test_high_confidence_passes_through(self):
        """Action with confidence >= threshold should pass through unchanged."""
        high_conf = _make_action(action="order_items", confidence=0.9, items=[ParsedItem(text="milk")])

        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=high_conf):
            result = await decide("milk kavali", conversation_history=[], current_state=None)

        assert result.action == "order_items"
        assert result.items[0].text == "milk"


# ---------------------------------------------------------------------------
# Brain.decide() — Core Dispatch
# ---------------------------------------------------------------------------

class TestDecideCore:
    @pytest.mark.asyncio
    async def test_greet_action(self):
        action = _make_action(action="greet", reply_text="Namaskaram!")
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
            result = await decide("hi", conversation_history=[], current_state=None)
        assert result.action == "greet"
        assert result.reply_text is not None

    @pytest.mark.asyncio
    async def test_order_items_extraction(self):
        action = _make_action(
            action="order_items",
            confidence=0.95,
            items=[
                ParsedItem(text="atta", quantity=1, unit="5kg", brand_hint="aashirvaad"),
                ParsedItem(text="milk", quantity=2, unit="L"),
            ],
        )
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
            result = await decide("2kg atta and 1L milk", conversation_history=[], current_state=None)
        assert result.action == "order_items"
        assert len(result.items) == 2
        assert result.items[0].text == "atta"
        assert result.items[1].text == "milk"
        assert result.items[1].quantity == 2

    @pytest.mark.asyncio
    async def test_select_option_by_index(self):
        action = _make_action(action="select_option", selected_index=1, confidence=0.9)
        state = {"state": "AWAITING_CONFIRMATION", "context": {"flow": "discovery", "visible_options": [{"name": "A"}, {"name": "B"}]}}
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
            result = await decide("second one", conversation_history=[], current_state=state)
        assert result.action == "select_option"
        assert result.selected_index == 1

    @pytest.mark.asyncio
    async def test_select_option_by_name(self):
        action = _make_action(action="select_option", selected_name="Tatva", confidence=0.85)
        state = {"state": "AWAITING_CONFIRMATION", "context": {"flow": "discovery", "visible_options": [{"name": "Tatva"}, {"name": "Spice Kitchen"}]}}
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
            result = await decide("Tatva", conversation_history=[], current_state=state)
        assert result.action == "select_option"
        assert result.selected_name == "Tatva"

    @pytest.mark.asyncio
    async def test_correction_not_cancel(self):
        action = _make_action(
            action="correct", confidence=0.85,
            reply_text="Okay, selecting Tatva instead.",
            selected_name="Tatva",
        )
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
            result = await decide("no i meant Tatva", conversation_history=[], current_state=None)
        assert result.action == "correct"
        assert result.action != "cancel"

    @pytest.mark.asyncio
    async def test_chitchat_not_greet_midflow(self):
        action = _make_action(action="chitchat", reply_text="You have atta in your cart. Want to add more?", confidence=0.8)
        state = {"state": "AWAITING_CONFIRMATION", "context": {"resolved_cart": {"items": [{"display_name": "Atta"}]}}}
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
            result = await decide("what happened", conversation_history=[], current_state=state)
        assert result.action == "chitchat"

    @pytest.mark.asyncio
    async def test_fallback_to_azure_on_gemini_failure(self):
        azure_action = _make_action(action="order_items", confidence=0.7, items=[ParsedItem(text="rice")])
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=None), \
             patch("app.agents.brain._call_azure_openai", new_callable=AsyncMock, return_value=azure_action):
            result = await decide("rice", conversation_history=[], current_state=None)
        assert result.action == "order_items"
        assert result.items[0].text == "rice"

    @pytest.mark.asyncio
    async def test_both_llms_fail_returns_unclear(self):
        """When both Gemini and Azure fail internally, the Azure fallback returns unclear."""
        # _call_azure_openai has its own try/except that returns unclear on failure.
        # Simulate by having Gemini return None and Azure return its fallback unclear.
        fallback_unclear = BrainAction(
            action="unclear",
            clarification_question="Sare, emi kavali?",
            reply_text="Sare, emi kavali? Cheppandi.",
            detected_language="te-en",
            confidence=0.3,
            reasoning="Both LLM calls failed",
        )
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=None), \
             patch("app.agents.brain._call_azure_openai", new_callable=AsyncMock, return_value=fallback_unclear):
            result = await decide("???", conversation_history=[], current_state=None)
        assert result.action == "unclear"


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class TestCaching:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_action(self):
        """Second call with same text+state should return cached result."""
        action = _make_action(action="greet", reply_text="Hi!", confidence=0.9)
        call_count = 0

        async def _mock_gemini(*_a, **_k):
            nonlocal call_count
            call_count += 1
            return action

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)  # first call: cache miss
        redis.set = AsyncMock()

        # First call: cache miss, hits LLM
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=_mock_gemini):
            result1 = await decide("hi", conversation_history=[], current_state=None, redis=redis)
        assert call_count == 1
        assert result1.action == "greet"

        # Simulate cache now has the result
        redis.get = AsyncMock(return_value=action.model_dump_json())

        # Second call: cache hit, no LLM call
        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=_mock_gemini):
            result2 = await decide("hi", conversation_history=[], current_state=None, redis=redis)
        assert call_count == 1  # no additional LLM call
        assert result2.action == "greet"

    @pytest.mark.asyncio
    async def test_cache_miss_different_state(self):
        """Different state fingerprint should cause cache miss."""
        action = _make_action(action="greet", reply_text="Hi!", confidence=0.9)

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()

        call_count = 0

        async def _mock_gemini(*_a, **_k):
            nonlocal call_count
            call_count += 1
            return action

        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, side_effect=_mock_gemini):
            await decide("hi", conversation_history=[], current_state=None, redis=redis)
            await decide("hi", conversation_history=[], current_state={"state": "AWAITING_CONFIRMATION", "context": {}}, redis=redis)

        assert call_count == 2  # both went to LLM

    @pytest.mark.asyncio
    async def test_side_effect_actions_not_cached(self):
        """confirm, cancel, update_address should never be cached."""
        confirm_action = _make_action(action="confirm", confidence=0.95)

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()

        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=confirm_action):
            await decide("confirm", conversation_history=[], current_state=None, redis=redis)

        # redis.set should NOT have been called for confirm action
        redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_redis_no_caching(self):
        """Without redis, caching is skipped entirely."""
        action = _make_action(action="greet", confidence=0.9)

        with patch("app.agents.brain._call_gemini", new_callable=AsyncMock, return_value=action):
            result = await decide("hi", conversation_history=[], current_state=None, redis=None)

        assert result.action == "greet"
