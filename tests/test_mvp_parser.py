"""Tests for the message_parser shim — backward compat layer over brain.

The old deterministic parser is gone; these tests verify the shim
delegates to brain.decide() and converts BrainAction → ParsedIntent correctly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from app.agents.brain import BrainAction, ParsedItem
from app.agents.message_parser import (
    CORRECTION_PHRASES,
    _brain_action_to_parsed_intent,
    _deterministic_parse,
    parse_text_message,
    post_process_intent,
)
from app.schemas.message import ParsedIntent


# ---------------------------------------------------------------------------
# Shim tests
# ---------------------------------------------------------------------------

class TestShimDelegation:
    @pytest.mark.asyncio
    async def test_parse_text_message_calls_brain(self):
        """parse_text_message should delegate to brain.decide()."""
        action = BrainAction(
            action="order_items",
            items=[ParsedItem(text="biryani")],
            domain_hint="food_delivery",
            detected_language="te-en",
            confidence=0.9,
            reasoning="test",
        )
        with patch("app.agents.message_parser.decide", new_callable=AsyncMock, return_value=action):
            intent = await parse_text_message("biryani kavali")
        assert intent.action == "ORDER"
        assert intent.items[0].text == "biryani"
        assert intent.domain_hint == "food_delivery"

    @pytest.mark.asyncio
    async def test_parse_text_message_unclear(self):
        """Unclear brain action maps to UNCLEAR ParsedIntent."""
        action = BrainAction(
            action="unclear",
            clarification_question="Emi kavali?",
            detected_language="te-en",
            confidence=0.3,
            reasoning="test",
        )
        with patch("app.agents.message_parser.decide", new_callable=AsyncMock, return_value=action):
            intent = await parse_text_message("I want to order something")
        assert intent.action == "UNCLEAR"
        assert intent.needs_clarification is True


class TestBrainActionConversion:
    def test_greet_maps_to_chitchat(self):
        action = BrainAction(action="greet", detected_language="en", confidence=0.9, reasoning="test")
        intent = _brain_action_to_parsed_intent(action, "hi", "en-IN")
        assert intent.action == "CHITCHAT"
        assert intent.goal == "chat"

    def test_order_items_maps_to_order(self):
        action = BrainAction(
            action="order_items", items=[ParsedItem(text="milk")],
            domain_hint="grocery", detected_language="te-en",
            confidence=0.9, reasoning="test",
        )
        intent = _brain_action_to_parsed_intent(action, "milk kavali", "te-IN")
        assert intent.action == "ORDER"
        assert intent.goal == "shop"
        assert intent.items[0].text == "milk"

    def test_track_order_maps_to_track(self):
        action = BrainAction(action="track_order", detected_language="en", confidence=0.9, reasoning="test")
        intent = _brain_action_to_parsed_intent(action, "order status", "en-IN")
        assert intent.action == "TRACK"
        assert intent.goal == "track"

    def test_unclear_sets_needs_clarification(self):
        action = BrainAction(
            action="unclear", clarification_question="Emi kavali?",
            detected_language="te-en", confidence=0.3, reasoning="test",
        )
        intent = _brain_action_to_parsed_intent(action, "???", "te-IN")
        assert intent.needs_clarification is True
        assert intent.clarification_question == "Emi kavali?"

    def test_language_mapping(self):
        action = BrainAction(action="greet", detected_language="te", confidence=0.9, reasoning="test")
        intent = _brain_action_to_parsed_intent(action, "namaste", "te-IN")
        assert intent.language_detected == "te-IN"


class TestDeadStubs:
    def test_deterministic_parse_returns_none(self):
        assert _deterministic_parse("anything") is None

    def test_post_process_intent_passthrough(self):
        intent = ParsedIntent(action="ORDER", raw_text="test", goal="shop")
        assert post_process_intent(intent, "test") is intent

    def test_correction_phrases_still_exported(self):
        assert len(CORRECTION_PHRASES) > 0
        assert "not that" in CORRECTION_PHRASES
