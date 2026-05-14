"""Unit tests for F4 — Conversation State Machine.

Tests every acceptance criterion from 03_FEATURE_BUILD_ORDER.md:
  1. Unit tests for every legal transition
  2. Unit tests for every illegal transition (must raise)
  3. TTL expiry test: state auto-resets to IDLE after timeout
  4. Concurrent transition test: two simultaneous → exactly one wins
  5. Mid-flow amendment handling

Run:
    python -m pytest tests/test_conversation.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Ensure project root is on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redis.asyncio import Redis

from packages.core.conversation import (
    ConversationState,
    ConversationStateMachine,
    InvalidTransitionError,
    NoActiveSessionError,
    StaleStateError,
    LEGAL_TRANSITIONS,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def redis():
    """Connect to local Redis, use DB 1 for tests (not DB 0 which is dev)."""
    r = Redis.from_url("redis://localhost:6379/1", decode_responses=True)
    await r.flushdb()  # Clean slate for each test
    yield r
    await r.flushdb()
    await r.aclose()


@pytest_asyncio.fixture
async def csm(redis):
    """Create a ConversationStateMachine backed by test Redis."""
    return ConversationStateMachine(redis)


USER_ID = "test-user-amma-001"
USER_ID_2 = "test-user-kiran-002"


# ============================================================================
# 1. Legal Transitions — every single one
# ============================================================================

class TestLegalTransitions:
    """Test every legal transition defined in the transition table."""

    @pytest.mark.asyncio
    async def test_idle_to_parsing(self, csm):
        """IDLE → PARSING via start_session()."""
        result = await csm.start_session(USER_ID, "voice-session-001")
        assert result["state"] == "PARSING"
        state = await csm.current_state(USER_ID)
        assert state["state"] == "PARSING"
        assert state["session_id"] == "voice-session-001"

    @pytest.mark.asyncio
    async def test_parsing_to_awaiting_confirmation(self, csm):
        """PARSING → AWAITING_CONFIRMATION (parse succeeded, quote ready)."""
        await csm.start_session(USER_ID)
        result = await csm.transition(USER_ID, "AWAITING_CONFIRMATION", context={
            "parsed_intent": {"action": "ORDER", "items": [{"text": "atta"}]},
            "quote_total": 597,
        })
        assert result["state"] == "AWAITING_CONFIRMATION"
        assert result["previous_state"] == "PARSING"

        # Verify context was stored
        state = await csm.current_state(USER_ID)
        assert state["context"]["quote_total"] == 597

    @pytest.mark.asyncio
    async def test_parsing_to_idle(self, csm):
        """PARSING → IDLE (chitchat or parse failure)."""
        await csm.start_session(USER_ID)
        result = await csm.transition(USER_ID, "IDLE")
        assert result["state"] == "IDLE"
        # State key survives 5 min (preserving turn_count), so current_state returns IDLE data
        current = await csm.current_state(USER_ID)
        assert current is None or current["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_confirmation_to_executing(self, csm):
        """AWAITING_CONFIRMATION → EXECUTING (user said 'avunu')."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        result = await csm.transition(USER_ID, "EXECUTING")
        assert result["state"] == "EXECUTING"

    @pytest.mark.asyncio
    async def test_confirmation_to_approval(self, csm):
        """AWAITING_CONFIRMATION → AWAITING_APPROVAL (high-value order)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        result = await csm.transition(USER_ID, "AWAITING_APPROVAL")
        assert result["state"] == "AWAITING_APPROVAL"

    @pytest.mark.asyncio
    async def test_confirmation_to_idle_cancel(self, csm):
        """AWAITING_CONFIRMATION → IDLE (user said 'vaddu')."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION", context={
            "pending_cart": {"items": ["atta"]},
        })
        result = await csm.transition(USER_ID, "IDLE")
        assert result["state"] == "IDLE"
        # State key survives 5 min (preserving turn_count), data_key is deleted
        current = await csm.current_state(USER_ID)
        assert current is None or current["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_approval_to_executing(self, csm):
        """AWAITING_APPROVAL → EXECUTING (payer approved)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "AWAITING_APPROVAL")
        result = await csm.transition(USER_ID, "EXECUTING")
        assert result["state"] == "EXECUTING"

    @pytest.mark.asyncio
    async def test_approval_to_idle_rejected(self, csm):
        """AWAITING_APPROVAL → IDLE (payer rejected)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "AWAITING_APPROVAL")
        result = await csm.transition(USER_ID, "IDLE")
        assert result["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_executing_to_complete(self, csm):
        """EXECUTING → COMPLETE (order placed)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "EXECUTING")
        result = await csm.transition(USER_ID, "COMPLETE")
        assert result["state"] == "COMPLETE"

    @pytest.mark.asyncio
    async def test_executing_to_idle_failed(self, csm):
        """EXECUTING → IDLE (execution failed)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "EXECUTING")
        result = await csm.transition(USER_ID, "IDLE")
        assert result["state"] == "IDLE"
        # State key survives 5 min (preserving turn_count), data_key is deleted
        current = await csm.current_state(USER_ID)
        assert current is None or current["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_complete_to_idle(self, csm):
        """COMPLETE → IDLE (ready for next order)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "EXECUTING")
        await csm.transition(USER_ID, "COMPLETE")
        result = await csm.transition(USER_ID, "IDLE")
        assert result["state"] == "IDLE"


# ============================================================================
# 2. Illegal Transitions — every one must raise
# ============================================================================

class TestIllegalTransitions:
    """Every transition NOT in the legal table must raise InvalidTransitionError."""

    @pytest.mark.asyncio
    async def test_parsing_to_executing_illegal(self, csm):
        """PARSING → EXECUTING is illegal (must go through CONFIRMATION)."""
        await csm.start_session(USER_ID)
        with pytest.raises(InvalidTransitionError) as exc:
            await csm.transition(USER_ID, "EXECUTING")
        assert exc.value.from_state == "PARSING"
        assert exc.value.to_state == "EXECUTING"

    @pytest.mark.asyncio
    async def test_parsing_to_complete_illegal(self, csm):
        """PARSING → COMPLETE is illegal."""
        await csm.start_session(USER_ID)
        with pytest.raises(InvalidTransitionError):
            await csm.transition(USER_ID, "COMPLETE")

    @pytest.mark.asyncio
    async def test_parsing_to_approval_illegal(self, csm):
        """PARSING → AWAITING_APPROVAL is illegal."""
        await csm.start_session(USER_ID)
        with pytest.raises(InvalidTransitionError):
            await csm.transition(USER_ID, "AWAITING_APPROVAL")

    @pytest.mark.asyncio
    async def test_confirmation_to_complete_illegal(self, csm):
        """AWAITING_CONFIRMATION → COMPLETE is illegal (must go through EXECUTING)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        with pytest.raises(InvalidTransitionError):
            await csm.transition(USER_ID, "COMPLETE")

    @pytest.mark.asyncio
    async def test_executing_to_confirmation_illegal(self, csm):
        """EXECUTING → AWAITING_CONFIRMATION is illegal (can't go backwards)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "EXECUTING")
        with pytest.raises(InvalidTransitionError):
            await csm.transition(USER_ID, "AWAITING_CONFIRMATION")

    @pytest.mark.asyncio
    async def test_complete_to_executing_illegal(self, csm):
        """COMPLETE → EXECUTING is illegal."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "EXECUTING")
        await csm.transition(USER_ID, "COMPLETE")
        with pytest.raises(InvalidTransitionError):
            await csm.transition(USER_ID, "EXECUTING")

    @pytest.mark.asyncio
    async def test_double_start_illegal(self, csm):
        """Starting a session when one is already active must raise."""
        await csm.start_session(USER_ID)
        with pytest.raises(InvalidTransitionError):
            await csm.start_session(USER_ID)

    @pytest.mark.asyncio
    async def test_no_session_transition_raises(self, csm):
        """Transitioning a user who has no session raises NoActiveSessionError."""
        with pytest.raises(NoActiveSessionError):
            await csm.transition("nonexistent-user", "PARSING")


# ============================================================================
# 3. TTL Expiry — auto-resets to IDLE
# ============================================================================

class TestTTLExpiry:
    """State auto-resets after TTL expires (key disappears from Redis)."""

    @pytest.mark.asyncio
    async def test_parsing_ttl_set(self, csm):
        """PARSING state should have a TTL of ~120 seconds."""
        await csm.start_session(USER_ID)
        ttl = await csm.get_ttl(USER_ID)
        # TTL should be between 115 and 120 (accounting for test execution time)
        assert 110 <= ttl <= 120

    @pytest.mark.asyncio
    async def test_confirmation_ttl_set(self, csm):
        """AWAITING_CONFIRMATION should have a TTL of ~21600 seconds (6 hours)."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        ttl = await csm.get_ttl(USER_ID)
        assert 21580 <= ttl <= 21600

    @pytest.mark.asyncio
    async def test_complete_ttl_set(self, csm):
        """COMPLETE should have a 60-second cooldown TTL."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "EXECUTING")
        await csm.transition(USER_ID, "COMPLETE")
        ttl = await csm.get_ttl(USER_ID)
        assert 55 <= ttl <= 60

    @pytest.mark.asyncio
    async def test_ttl_expiry_means_idle(self, csm, redis):
        """After TTL expires, current_state returns None (user is effectively IDLE)."""
        await csm.start_session(USER_ID)
        # Manually set a 1-second TTL to simulate fast expiry
        await redis.expire(f"conv:{USER_ID}:state", 1)
        await asyncio.sleep(1.5)
        state = await csm.current_state(USER_ID)
        # State is None — key expired, user is back to IDLE
        assert state is None

    @pytest.mark.asyncio
    async def test_can_start_new_session_after_expiry(self, csm, redis):
        """After TTL expiry, user can start a fresh session."""
        await csm.start_session(USER_ID)
        # Expire both state and data keys
        await redis.expire(f"conv:{USER_ID}:state", 1)
        await redis.expire(f"conv:{USER_ID}:data", 1)
        await asyncio.sleep(2)
        # Should be able to start fresh
        result = await csm.start_session(USER_ID, "new-session-002")
        assert result["state"] == "PARSING"
        assert result["session_id"] == "new-session-002"


# ============================================================================
# 4. Concurrent Transitions — exactly one wins
# ============================================================================

class TestConcurrency:
    """Two simultaneous transitions: exactly one succeeds, other gets StaleStateError."""

    @pytest.mark.asyncio
    async def test_concurrent_transition_one_wins(self, csm):
        """Two coroutines racing to transition — one succeeds, one fails."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")

        results = {"success": 0, "stale": 0}

        async def try_transition():
            try:
                await csm.transition(USER_ID, "EXECUTING")
                results["success"] += 1
            except (StaleStateError, InvalidTransitionError):
                results["stale"] += 1

        # Fire both at the same time
        await asyncio.gather(try_transition(), try_transition())

        # Exactly one should succeed
        assert results["success"] == 1
        assert results["stale"] == 1

    @pytest.mark.asyncio
    async def test_two_users_independent(self, csm):
        """Two different users transitioning simultaneously — both succeed."""
        await csm.start_session(USER_ID)
        await csm.start_session(USER_ID_2)

        r1 = await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        r2 = await csm.transition(USER_ID_2, "AWAITING_CONFIRMATION")

        assert r1["state"] == "AWAITING_CONFIRMATION"
        assert r2["state"] == "AWAITING_CONFIRMATION"


# ============================================================================
# 5. Mid-flow Amendment
# ============================================================================

class TestAmendment:
    """Mid-flow text or voice message (amendment) handled correctly."""

    @pytest.mark.asyncio
    async def test_amendment_from_confirmation(self, csm):
        """User changes order while in AWAITING_CONFIRMATION → goes back to PARSING."""
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION", context={
            "items": [{"text": "atta", "quantity": 2}],
        })

        # User sends a new message to amend: "atta kaadu, paalu kavali"
        result = await csm.transition(USER_ID, "PARSING", context={
            "amendment": True,
            "original_items": [{"text": "atta"}],
        })

        assert result["state"] == "PARSING"
        assert result["previous_state"] == "AWAITING_CONFIRMATION"

        # Verify context was updated
        state = await csm.current_state(USER_ID)
        assert state["context"]["amendment"] is True


# ============================================================================
# 6. Cancel Session
# ============================================================================

class TestCancelSession:
    """cancel_session() works from any state."""

    @pytest.mark.asyncio
    async def test_cancel_from_parsing(self, csm):
        await csm.start_session(USER_ID)
        result = await csm.cancel_session(USER_ID)
        assert result["state"] == "IDLE"
        assert result["previous_state"] == "PARSING"
        assert result["cancelled"] is True

    @pytest.mark.asyncio
    async def test_cancel_from_confirmation(self, csm):
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        result = await csm.cancel_session(USER_ID)
        assert result["state"] == "IDLE"
        assert result["previous_state"] == "AWAITING_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_cancel_from_executing(self, csm):
        await csm.start_session(USER_ID)
        await csm.transition(USER_ID, "AWAITING_CONFIRMATION")
        await csm.transition(USER_ID, "EXECUTING")
        result = await csm.cancel_session(USER_ID)
        assert result["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_user(self, csm):
        """Cancelling a user with no session is a no-op, returns IDLE."""
        result = await csm.cancel_session("ghost-user")
        assert result["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_can_start_after_cancel(self, csm):
        """After cancel, user can start a new session."""
        await csm.start_session(USER_ID)
        await csm.cancel_session(USER_ID)
        result = await csm.start_session(USER_ID, "fresh-session")
        assert result["state"] == "PARSING"
        assert result["session_id"] == "fresh-session"


class TestRestoreAndTurnCount:
    @pytest.mark.asyncio
    async def test_start_session_preserves_data_key_from_idle(self, csm, redis):
        """IDLE -> PARSING should preserve data_key and increment turn_count."""
        await redis.hset(
            f"conv:{USER_ID}:state",
            mapping={
                "state": "IDLE",
                "session_id": "old-session",
                "user_id": USER_ID,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "turn_count": "5",
            },
        )
        await redis.set(
            f"conv:{USER_ID}:data",
            '{"flow":"awaiting_assistant","turn_count":5,"confirmation_text":"pending"}',
        )

        await csm.start_session(USER_ID, "new-session")
        current = await csm.current_state(USER_ID)
        assert current is not None
        assert current["state"] == "PARSING"
        assert current["turn_count"] == 6
        assert current["last_turn_at"] is not None

        raw = await redis.get(f"conv:{USER_ID}:data")
        assert raw is not None
        assert "awaiting_assistant" in raw


# ============================================================================
# 7. Full Flow — Happy Path
# ============================================================================

class TestFullFlow:
    """Complete happy-path: IDLE → PARSING → CONFIRM → EXECUTING → COMPLETE → IDLE."""

    @pytest.mark.asyncio
    async def test_full_order_flow(self, csm):
        """Simulate Amma ordering atta end-to-end."""
        # 1. Amma sends "atta teesuko"
        s1 = await csm.start_session(USER_ID, "voice-sess-amma-001")
        assert s1["state"] == "PARSING"

        # 2. Parser extracts intent, SKU mapper resolves, quote ready
        s2 = await csm.transition(USER_ID, "AWAITING_CONFIRMATION", context={
            "parsed_intent": {"action": "ORDER", "items": [{"text": "atta", "qty": 2}]},
            "sku_matched": "aashirvaad_select_atta_5kg",
            "quote_total_inr": 597,
            "confirmation_text": "Sare, Aashirvaad atta cart lo pettanu. Total 597. Confirm chey-yana?",
        })
        assert s2["state"] == "AWAITING_CONFIRMATION"

        # 3. Amma replies "avunu"
        s3 = await csm.transition(USER_ID, "EXECUTING")
        assert s3["state"] == "EXECUTING"

        # 4. Executor places order on mock Swiggy
        s4 = await csm.transition(USER_ID, "COMPLETE", context={
            "provider_order_id": "INST-ORD-ABC123",
            "estimated_delivery_min": 20,
        })
        assert s4["state"] == "COMPLETE"

        # Verify stored context
        state = await csm.current_state(USER_ID)
        assert state["context"]["provider_order_id"] == "INST-ORD-ABC123"

        # 5. Session ends
        s5 = await csm.transition(USER_ID, "IDLE")
        assert s5["state"] == "IDLE"
        # State key survives 5 min (preserving turn_count), data_key is deleted
        current = await csm.current_state(USER_ID)
        assert current is None or current["state"] == "IDLE"
