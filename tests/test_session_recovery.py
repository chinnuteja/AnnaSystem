from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from app.agents import brain as brain_mod
from app.agents.brain import BrainAction, ParsedItem as BrainParsedItem

from packages.core.session_recovery import (
    recover_stale_parsing_if_needed,
    supersede_awaiting_assistant_with_concrete_order,
)


@pytest.fixture
def correction_phrases():
    from app.agents.message_parser import CORRECTION_PHRASES

    return CORRECTION_PHRASES


@pytest.mark.asyncio
async def test_stale_parsing_is_canceled(redis, csm, correction_phrases):
    uid = "bbbbbbbb-0002-0002-0002-000000000002"
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    await redis.hset(
        f"conv:{uid}:state",
        mapping={
            "state": "PARSING",
            "session_id": str(uuid.uuid4()),
            "user_id": uid,
            "started_at": old,
            "updated_at": old,
        },
    )
    cur = await csm.current_state(uid)
    new, block = await recover_stale_parsing_if_needed(
        csm, uid, cur, action="ORDER", text="milk 1L", correction_phrases=correction_phrases
    )
    assert block is False
    assert new is None
    assert await csm.current_state(uid) is None


@pytest.mark.asyncio
async def test_fresh_parsing_burst_blocks_duplicate_order(redis, csm, correction_phrases):
    uid = "bbbbbbbb-0002-0002-0002-000000000002"
    await csm.start_session(uid)
    cur = await csm.current_state(uid)
    assert cur["state"] == "PARSING"
    new, block = await recover_stale_parsing_if_needed(
        csm, uid, cur, action="ORDER", text="milk 1L", correction_phrases=correction_phrases
    )
    assert block is True
    assert new["state"] == "PARSING"


@pytest.mark.asyncio
async def test_fresh_parsing_burst_superseded_by_chitchat(redis, csm, correction_phrases):
    uid = "bbbbbbbb-0002-0002-0002-000000000002"
    await csm.start_session(uid)
    cur = await csm.current_state(uid)
    new, block = await recover_stale_parsing_if_needed(
        csm, uid, cur, action="CHITCHAT", text="hi", correction_phrases=correction_phrases
    )
    assert block is False
    assert new is None


@pytest.mark.asyncio
async def test_supersede_awaiting_assistant_clears_state(redis, csm):
    uid = "bbbbbbbb-0002-0002-0002-000000000002"
    await csm.start_session(uid)
    await csm.transition(
        uid,
        "AWAITING_CONFIRMATION",
        context={
            "flow": "awaiting_assistant",
            "voice_session_id": str(uuid.uuid4()),
            "last_bot_message": "Which domain?",
        },
    )
    cur = await csm.current_state(uid)
    new = await supersede_awaiting_assistant_with_concrete_order(
        csm,
        uid,
        cur,
        action="ORDER",
        needs_clarification=False,
        has_substantive_items=True,
    )
    assert new is None


@pytest.mark.asyncio
async def test_pipeline_after_stale_parsing_starts_session(monkeypatch, redis, csm):
    """After canceling stale PARSING, process_text_order must not return the wait reply."""
    from packages.core import pipeline as pipeline_mod

    uid = "bbbbbbbb-0002-0002-0002-000000000002"
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    await redis.hset(
        f"conv:{uid}:state",
        mapping={
            "state": "PARSING",
            "session_id": str(uuid.uuid4()),
            "user_id": uid,
            "started_at": old,
            "updated_at": old,
        },
    )

    user = SimpleNamespace(
        id=uid,
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )

    from tests.conftest import make_mock_family_ctx
    fam_ctx = make_mock_family_ctx(user_id=uid, family_id="aaaaaaaa-0001-0001-0001-000000000001")

    async def _fake_resolve(*_a, **_k):
        return fam_ctx

    from app.schemas.message import ParsedIntent, ParsedItem

    async def _fake_decide(*_a, **_k):
        return BrainAction(
            action="order_items",
            items=[BrainParsedItem(text="milk", quantity=1)],
            domain_hint="grocery",
            detected_language="te-en",
            confidence=0.95,
            reasoning="test",
        )

    async def _fake_persist(*_a, **_k):
        return None

    monkeypatch.setattr(pipeline_mod, "resolve_family_context", _fake_resolve)
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    monkeypatch.setattr(pipeline_mod, "_rehydrate_recent_pending_session",
                        lambda *a, **k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_update_voice_session_status", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pipeline_mod, "_infer_substitute_category", lambda *_a, **_k: asyncio.sleep(0, result="staples_flour"))
    monkeypatch.setattr(pipeline_mod, "find_options_in_category", lambda **_k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(brain_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "decide", _fake_decide)
    monkeypatch.setattr(pipeline_mod, "_persist", _fake_persist)
    async def _fake_rq(*_a, **_k):
        return ([], None, None)

    monkeypatch.setattr(pipeline_mod, "resolve_and_quote", _fake_rq)

    res = await pipeline_mod.process_text_order(
        csm=csm,
        from_phone="918247628278",
        text="milk",
        whatsapp_message_id=f"wamid.stale-{uuid.uuid4().hex[:8]}",
        location=None,
    )
    assert "previous request process" not in res["reply_text"]


@pytest_asyncio.fixture
async def redis(monkeypatch):
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


@pytest_asyncio.fixture
async def csm(redis, monkeypatch):
    import packages.core.redis_client as redis_mod

    monkeypatch.setattr(redis_mod, "_redis", redis)
    from packages.core.conversation import ConversationStateMachine

    return ConversationStateMachine(redis)
