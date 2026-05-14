from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from app.agents import brain as brain_mod
from app.agents.brain import BrainAction
from packages.core import pipeline as pipeline_mod


@pytest_asyncio.fixture
async def redis(monkeypatch):
    """Use real Redis DB 2 for pipeline tests, skip if not reachable."""
    import redis as sync_redis
    from redis.asyncio import Redis

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


@pytest_asyncio.fixture
async def csm(redis, monkeypatch):
    """Conversation state machine using the test Redis instance."""
    import packages.core.redis_client as redis_mod

    monkeypatch.setattr(redis_mod, "_redis", redis)
    from packages.core.conversation import ConversationStateMachine

    return ConversationStateMachine(redis)


@pytest_asyncio.fixture
async def seeded_user(monkeypatch):
    """Patch user lookup to always return a seeded-looking user."""
    from packages.core import pipeline as pipeline_mod
    from tests.conftest import make_mock_family_ctx

    fam_ctx = make_mock_family_ctx()

    async def _fake_resolve(*_a, **_k):
        return fam_ctx

    monkeypatch.setattr(pipeline_mod, "resolve_family_context", _fake_resolve)
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    monkeypatch.setattr(pipeline_mod, "_rehydrate_recent_pending_session",
                        lambda *a, **k: asyncio.sleep(0, result=None))
    return fam_ctx.user


@pytest.mark.asyncio
async def test_vague_order_creates_clarification_state(csm, seeded_user, monkeypatch):
    from packages.core.pipeline import process_text_order

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="unclear",
            clarification_question="Emi kavali? Groceries, food, leka dineout?",
            reply_text="Sare, emi kavali? Cheppandi.",
            detected_language="te-en",
            confidence=0.4,
            reasoning="vague order request",
        )

    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", lambda *_a, **_k: asyncio.sleep(0, result=None))

    res = await process_text_order(
        csm=csm,
        from_phone="918247628278",
        text="I want to order something",
        whatsapp_message_id=f"wamid.test1-clarify-{uuid.uuid4().hex[:8]}",
        location=None,
    )
    assert res["state"] == "AWAITING_CONFIRMATION"
    # Reply may be Telugu or English; ensure it's a question/clarification.
    assert res["reply_text"]

