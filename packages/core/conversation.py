"""Conversation State Machine — F4 per 03_FEATURE_BUILD_ORDER.md.

Redis-backed with Postgres mirror for durability.
Atomic transitions via Lua scripts — no race conditions.
TTL auto-expiry resets stale sessions to IDLE.

States (from 02_AGENTS_AND_EDGE_CASES.md):
    IDLE → PARSING → AWAITING_CONFIRMATION → EXECUTING → COMPLETE
                   ↗                       ↘
         AWAITING_APPROVAL                  IDLE (cancel/fail)

Usage:
    from packages.core.conversation import ConversationStateMachine

    csm = ConversationStateMachine()
    session = await csm.start_session(user_id, voice_session_id)
    await csm.transition(user_id, "AWAITING_CONFIRMATION", context={...})
    state = await csm.current_state(user_id)
    await csm.cancel_session(user_id)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from redis.asyncio import Redis


# ============================================================================
# States & Transitions
# ============================================================================

class ConversationState(str, Enum):
    IDLE = "IDLE"
    PARSING = "PARSING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    COMPLETE = "COMPLETE"


# Legal transition table: {from_state: [allowed_to_states]}
LEGAL_TRANSITIONS: dict[ConversationState, list[ConversationState]] = {
    ConversationState.IDLE: [
        ConversationState.PARSING,
    ],
    ConversationState.PARSING: [
        ConversationState.AWAITING_CONFIRMATION,
        ConversationState.IDLE,  # parse failed, chitchat, track
    ],
    ConversationState.AWAITING_CONFIRMATION: [
        ConversationState.EXECUTING,          # user confirmed ("avunu")
        ConversationState.AWAITING_APPROVAL,  # high-value → needs payer OK
        ConversationState.AWAITING_CONFIRMATION,  # amendment — new items replace pending cart
        ConversationState.PARSING,            # amendment — user changed the order mid-flow
        ConversationState.IDLE,               # user cancelled ("vaddu")
    ],
    ConversationState.AWAITING_APPROVAL: [
        ConversationState.EXECUTING,  # payer approved
        ConversationState.IDLE,       # payer rejected or expired
    ],
    ConversationState.EXECUTING: [
        ConversationState.COMPLETE,  # order placed successfully
        ConversationState.IDLE,      # execution failed
    ],
    ConversationState.COMPLETE: [
        ConversationState.IDLE,  # session ends, ready for next order
    ],
}

# TTL per state in seconds (PARSING=2min, CONFIRMING=6h, APPROVAL=60min, EXECUTING=2min, COMPLETE=60s)
# IDLE is handled inline in _LUA_TRANSITION: state_key kept 5min (preserves turn_count), data_key DEL'd.
STATE_TTL: dict[ConversationState, int] = {
    ConversationState.IDLE: 0,                     # handled by Lua (state_key: 5min TTL, data_key: DEL)
    ConversationState.PARSING: 2 * 60,             # 2 minutes
    ConversationState.AWAITING_CONFIRMATION: 6 * 60 * 60,  # 6 hours
    ConversationState.AWAITING_APPROVAL: 60 * 60,  # 60 minutes
    ConversationState.EXECUTING: 2 * 60,           # 2 minutes
    ConversationState.COMPLETE: 60,                # 60-second cooldown
}


# ============================================================================
# Custom Errors
# ============================================================================

class InvalidTransitionError(Exception):
    """Raised when attempting an illegal state transition."""
    def __init__(self, from_state: str, to_state: str):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid transition: {from_state} → {to_state}. "
            f"Legal targets from {from_state}: "
            f"{[s.value for s in LEGAL_TRANSITIONS.get(ConversationState(from_state), [])]}"
        )


class StaleStateError(Exception):
    """Raised when a concurrent transition beat us — the state changed under us."""
    def __init__(self, expected: str, actual: str):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Stale state: expected {expected}, but current state is {actual}. "
            f"Another process transitioned first."
        )


class NoActiveSessionError(Exception):
    """Raised when trying to transition but user has no active session."""
    pass


# ============================================================================
# Lua Scripts for Atomic Operations
# ============================================================================

# Atomic transition: compare current state → if matches expected → set new state + TTL
# Returns: 1 = success, 0 = stale (state changed), -1 = no session
_LUA_TRANSITION = """
local state_key = KEYS[1]
local data_key = KEYS[2]
local expected_state = ARGV[1]
local new_state = ARGV[2]
local ttl = tonumber(ARGV[3])
local timestamp = ARGV[4]
local context_json = ARGV[5]

local current = redis.call('HGET', state_key, 'state')
if current == false then
    return -1
end
if current ~= expected_state then
    return current
end

local turn_count = tonumber(redis.call('HGET', state_key, 'turn_count') or '0')
if new_state == 'PARSING' and expected_state ~= 'PARSING' then
    turn_count = turn_count + 1
end

redis.call('HSET', state_key,
    'state', new_state,
    'updated_at', timestamp,
    'last_turn_at', timestamp,
    'turn_count', tostring(turn_count)
)
if context_json ~= '' then
    redis.call('SET', data_key, context_json)
end

if new_state == 'IDLE' then
    local tc = redis.call('HGET', state_key, 'turn_count') or '0'
    redis.call('HSET', state_key,
        'state', 'IDLE',
        'updated_at', timestamp,
        'turn_count', tc
    )
    redis.call('DEL', data_key)
    redis.call('EXPIRE', state_key, 300)
    return 1
end

if ttl > 0 then
    redis.call('EXPIRE', state_key, ttl)
    redis.call('EXPIRE', data_key, ttl)
else
    redis.call('PERSIST', state_key)
    redis.call('PERSIST', data_key)
end

return 1
"""

# Atomic start: only succeeds if no active session (IDLE or missing)
_LUA_START = """
local state_key = KEYS[1]
local data_key = KEYS[2]
local session_id = ARGV[1]
local user_id = ARGV[2]
local timestamp = ARGV[3]
local ttl = tonumber(ARGV[4])

local current = redis.call('HGET', state_key, 'state')
if current ~= false and current ~= 'IDLE' then
    return current
end

local turn_count = 1
if current == 'IDLE' then
    local prior = tonumber(redis.call('HGET', state_key, 'turn_count')) or 0
    if prior > 0 then turn_count = prior + 1 end
else
    redis.call('DEL', data_key)
end

redis.call('HSET', state_key,
    'state', 'PARSING',
    'session_id', session_id,
    'user_id', user_id,
    'started_at', timestamp,
    'updated_at', timestamp,
    'last_turn_at', timestamp,
    'turn_count', tostring(turn_count)
)

if ttl > 0 then
    redis.call('EXPIRE', state_key, ttl)
    if redis.call('EXISTS', data_key) == 1 then
        redis.call('EXPIRE', data_key, ttl)
    end
end

return 1
"""


# Atomic turn bump: increment turn_count + update last_turn_at without changing state or data.
_LUA_BUMP_TURN = """
local state_key = KEYS[1]
local timestamp = ARGV[1]
local current = redis.call('HGET', state_key, 'state')
if current == false then return 0 end
local tc = tonumber(redis.call('HGET', state_key, 'turn_count') or '0')
redis.call('HSET', state_key, 'turn_count', tostring(tc + 1), 'last_turn_at', timestamp)
return 1
"""


# ============================================================================
# Redis Key Helpers
# ============================================================================

def _state_key(user_id: str) -> str:
    return f"conv:{user_id}:state"


def _data_key(user_id: str) -> str:
    return f"conv:{user_id}:data"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# Conversation State Machine
# ============================================================================

class ConversationStateMachine:
    """Per-user conversation state machine backed by Redis.

    Each user has exactly one active conversation at a time.
    Transitions are atomic via Lua scripts — two concurrent requests
    will never both succeed.
    """

    def __init__(self, redis: Redis):
        self._redis = redis
        # Register Lua scripts on first use
        self._transition_script = self._redis.register_script(_LUA_TRANSITION)
        self._start_script = self._redis.register_script(_LUA_START)
        self._bump_turn_script = self._redis.register_script(_LUA_BUMP_TURN)

    async def start_session(
        self,
        ordering_user_id: str,
        voice_session_id: str | None = None,
    ) -> dict:
        """Start a new conversation session. Moves from IDLE → PARSING.

        If user already has an active (non-IDLE) session, raises InvalidTransitionError.
        Returns the session info dict.
        """
        session_id = voice_session_id or str(uuid.uuid4())
        ttl = STATE_TTL[ConversationState.PARSING]

        result = await self._start_script(
            keys=[_state_key(ordering_user_id), _data_key(ordering_user_id)],
            args=[session_id, ordering_user_id, _utcnow_iso(), ttl],
        )

        if result != 1:
            # User has an active session — can't start a new one
            raise InvalidTransitionError(str(result), "PARSING")

        return {
            "session_id": session_id,
            "user_id": ordering_user_id,
            "state": ConversationState.PARSING.value,
        }

    async def transition(
        self,
        ordering_user_id: str,
        to_state: str | ConversationState,
        context: dict[str, Any] | None = None,
    ) -> dict:
        """Atomically transition user's conversation to a new state.

        Validates the transition is legal. Uses Lua script for atomicity —
        if another process changed the state between our read and write,
        this raises StaleStateError.

        Args:
            ordering_user_id: The user whose conversation state to change.
            to_state: Target state (string or ConversationState enum).
            context: Optional dict to store alongside the state (parsed intent, cart, etc.)

        Returns:
            Dict with new state info.

        Raises:
            InvalidTransitionError: If this transition is not in the legal table.
            StaleStateError: If another process beat us to the transition.
            NoActiveSessionError: If user has no session in Redis.
        """
        if isinstance(to_state, str):
            to_state = ConversationState(to_state)

        # Get current state
        current_raw = await self._redis.hget(_state_key(ordering_user_id), "state")
        if current_raw is None:
            raise NoActiveSessionError(
                f"No active session for user {ordering_user_id}"
            )

        current_state = ConversationState(current_raw)

        # Validate transition is legal
        legal_targets = LEGAL_TRANSITIONS.get(current_state, [])
        if to_state not in legal_targets:
            raise InvalidTransitionError(current_state.value, to_state.value)

        # Compute TTL for new state
        ttl = STATE_TTL[to_state]
        context_json = json.dumps(context) if context else ""

        # Atomic compare-and-swap via Lua
        result = await self._transition_script(
            keys=[_state_key(ordering_user_id), _data_key(ordering_user_id)],
            args=[
                current_state.value,
                to_state.value,
                ttl,
                _utcnow_iso(),
                context_json,
            ],
        )

        if result == -1:
            raise NoActiveSessionError(
                f"No active session for user {ordering_user_id}"
            )
        if result != 1:
            # Another process changed the state — result is the actual current state
            raise StaleStateError(current_state.value, str(result))

        return {
            "user_id": ordering_user_id,
            "previous_state": current_state.value,
            "state": to_state.value,
        }

    async def current_state(self, ordering_user_id: str) -> dict | None:
        """Get current conversation state for a user.

        Returns None if no session exists (user is IDLE / never started).
        Returns dict with state, session_id, timestamps.
        """
        state_data = await self._redis.hgetall(_state_key(ordering_user_id))
        if not state_data:
            return None

        context_raw = await self._redis.get(_data_key(ordering_user_id))
        context = json.loads(context_raw) if context_raw else None

        return {
            "user_id": ordering_user_id,
            "state": state_data.get("state", "IDLE"),
            "session_id": state_data.get("session_id"),
            "started_at": state_data.get("started_at"),
            "updated_at": state_data.get("updated_at"),
            "last_turn_at": state_data.get("last_turn_at"),
            "turn_count": int(state_data.get("turn_count") or 0),
            "context": context,
        }

    async def restore_session(
        self,
        ordering_user_id: str,
        voice_session_id: str,
        state: str | ConversationState,
        context: dict[str, Any] | None,
        *,
        turn_count: int = 1,
        started_at: str | None = None,
        last_turn_at: str | None = None,
    ) -> dict:
        """Rehydrate conversation keys from durable storage when Redis state is missing."""
        to_state = ConversationState(state) if isinstance(state, str) else state
        if to_state == ConversationState.IDLE:
            await self.cancel_session(ordering_user_id)
            return {
                "user_id": ordering_user_id,
                "state": ConversationState.IDLE.value,
                "session_id": voice_session_id,
                "restored": True,
            }

        now = _utcnow_iso()
        started = started_at or now
        last_turn = last_turn_at or now
        ttl = STATE_TTL[to_state]
        state_key = _state_key(ordering_user_id)
        data_key = _data_key(ordering_user_id)

        await self._redis.hset(
            state_key,
            mapping={
                "state": to_state.value,
                "session_id": voice_session_id,
                "user_id": ordering_user_id,
                "started_at": started,
                "updated_at": now,
                "last_turn_at": last_turn,
                "turn_count": str(max(1, int(turn_count))),
            },
        )
        if context is not None:
            await self._redis.set(data_key, json.dumps(context))
        if ttl > 0:
            await self._redis.expire(state_key, ttl)
            if context is not None:
                await self._redis.expire(data_key, ttl)

        return {
            "user_id": ordering_user_id,
            "state": to_state.value,
            "session_id": voice_session_id,
            "restored": True,
        }

    async def bump_turn(self, ordering_user_id: str) -> None:
        """Atomically increment turn_count and update last_turn_at without changing state or context."""
        await self._bump_turn_script(
            keys=[_state_key(ordering_user_id)],
            args=[_utcnow_iso()],
        )

    async def cancel_session(self, ordering_user_id: str) -> dict:
        """Force-cancel the current session, resetting to IDLE.

        Works from any state. Used when user says "vaddu" (no) or
        when the system needs to abort.
        """
        current = await self.current_state(ordering_user_id)
        previous = current["state"] if current else "IDLE"

        # Delete state and data keys — user returns to IDLE
        await self._redis.delete(
            _state_key(ordering_user_id),
            _data_key(ordering_user_id),
        )

        return {
            "user_id": ordering_user_id,
            "previous_state": previous,
            "state": ConversationState.IDLE.value,
            "cancelled": True,
        }

    async def get_ttl(self, ordering_user_id: str) -> int:
        """Get remaining TTL in seconds for user's current state. -1 if no TTL, -2 if no key."""
        return await self._redis.ttl(_state_key(ordering_user_id))
