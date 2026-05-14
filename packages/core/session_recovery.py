"""Recover stuck Redis conversation locks (e.g. PARSING left from a failed turn)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from packages.core.conversation import ConversationState, ConversationStateMachine

logger = logging.getLogger("foodleaf.session_recovery")

# True duplicate webhook bursts (same user, overlapping workers) — keep short.
FRESH_PARSING_LOCK_SEC = 8.0


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def state_age_seconds(current: dict | None) -> float | None:
    """Seconds since Redis `updated_at` (or `started_at`) for this conversation state, or None."""
    if not current:
        return None
    ref = _parse_iso(current.get("updated_at")) or _parse_iso(current.get("started_at"))
    if ref is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - ref.astimezone(timezone.utc)).total_seconds())


def _supersedes_fresh_parsing_burst(
    *,
    action: str,
    text: str,
    correction_phrases: tuple[str, ...],
) -> bool:
    """Intents that should break a sub-2s PARSING lock (not normal ORDER bursts)."""
    lowered = text.strip().lower()
    if any(p in lowered for p in correction_phrases):
        return True
    if action in ("CHITCHAT", "TRACK", "DISCOVER"):
        return True
    return False


async def recover_stale_parsing_if_needed(
    csm: ConversationStateMachine,
    user_id: str,
    current: dict | None,
    *,
    action: str,
    text: str,
    correction_phrases: tuple[str, ...],
) -> tuple[dict | None, bool]:
    """Return ``(current_or_none, block_with_wait_message)``.

    - If ``block_with_wait_message`` is True, return the standard
      "previous request processing" reply and do not continue the pipeline.
    """
    if not current or current.get("state") != ConversationState.PARSING.value:
        return current, False

    age = state_age_seconds(current)
    if age is None:
        logger.info(
            "session_recovery decision=cancel reason=missing_updated_at state=PARSING user=%s",
            user_id[:8],
        )
        await csm.cancel_session(user_id)
        return await csm.current_state(user_id), False

    if age > FRESH_PARSING_LOCK_SEC:
        logger.info(
            "session_recovery decision=cancel reason=stale_parsing age=%.2fs threshold=%.2fs "
            "state=PARSING user=%s action=%s",
            age,
            FRESH_PARSING_LOCK_SEC,
            user_id[:8],
            action,
        )
        await csm.cancel_session(user_id)
        return await csm.current_state(user_id), False

    if _supersedes_fresh_parsing_burst(action=action, text=text, correction_phrases=correction_phrases):
        logger.info(
            "session_recovery decision=cancel reason=supersedes_burst age=%.2fs state=PARSING "
            "user=%s action=%s",
            age,
            user_id[:8],
            action,
        )
        await csm.cancel_session(user_id)
        return await csm.current_state(user_id), False

    logger.info(
        "session_recovery decision=block reason=fresh_parsing_burst age=%.2fs state=PARSING user=%s action=%s",
        age,
        user_id[:8],
        action,
    )
    return current, True


async def supersede_awaiting_assistant_with_concrete_order(
    csm: ConversationStateMachine,
    user_id: str,
    current: dict | None,
    *,
    action: str,
    needs_clarification: bool,
    has_substantive_items: bool,
) -> dict | None:
    """If user was answering a vague bot question but now sends a clear ORDER, reset."""
    if not current or current.get("state") != ConversationState.AWAITING_CONFIRMATION.value:
        return current
    ctx = current.get("context") or {}
    flow = ctx.get("flow")
    if flow not in {"awaiting_assistant", "awaiting_location"}:
        return current

    if flow == "awaiting_location" and action in {"DISCOVER", "ORDER"}:
        logger.info(
            "session_recovery decision=cancel reason=supersede_awaiting_location "
            "state=AWAITING_CONFIRMATION user=%s",
            user_id[:8],
        )
        await csm.cancel_session(user_id)
        return None

    if action != "ORDER" or not has_substantive_items:
        return current

    # If the router flagged needs_clarification but the user already specified a domain
    # in a prior turn (suggested_domain is set), allow superseding anyway — the
    # clarification would be about domain, and we already know it.
    prior_domain = ctx.get("suggested_domain")
    domain_already_known = prior_domain and prior_domain not in ("any", "unknown")

    if needs_clarification and not domain_already_known:
        return current

    logger.info(
        "session_recovery decision=cancel reason=supersede_awaiting_assistant "
        "state=AWAITING_CONFIRMATION user=%s domain_locked=%s",
        user_id[:8],
        domain_already_known,
    )
    await csm.cancel_session(user_id)
    return await csm.current_state(user_id)
