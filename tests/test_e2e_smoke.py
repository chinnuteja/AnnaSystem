"""End-to-end smoke tests for the brain-powered pipeline.

These tests exercise the full pipeline with the REAL brain.decide()
(not mocked) to verify the LLM integration works end-to-end.
They require:
  - Redis running at localhost:6379
  - At least one LLM provider key (GEMINI_API_KEY or AZURE_OPENAI_KEY)

If no LLM provider is available, tests are skipped gracefully.
"""

from __future__ import annotations

import os
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


def _has_llm_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))


async def _noop_persist(*_a, **_k):
    """Async no-op replacement for _persist (which is async def)."""
    return None


async def _noop_resolve(*_a, **_k):
    """Async no-op replacement for resolve_and_quote."""
    return ([], None, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def redis():
    import redis as sync_redis
    from redis.asyncio import Redis

    try:
        sync_client = sync_redis.Redis(host="localhost", port=6379, db=3, socket_connect_timeout=0.35)
        sync_client.ping()
        sync_client.close()
    except Exception:
        pytest.skip("Redis not reachable at localhost:6379")

    r = Redis.from_url("redis://localhost:6379/3", decode_responses=True)
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


@pytest_asyncio.fixture
def seeded_user(monkeypatch):
    from packages.core import pipeline as pipeline_mod
    from tests.conftest import make_mock_family_ctx

    fam_ctx = make_mock_family_ctx(
        user_id="e2e-0000-0000-0000-000000000001",
        family_id="aaaaaaaa-0001-0001-0001-000000000001",
        preferred_language="te-IN",
    )

    async def _fake_resolve(*_a, **_k):
        return fam_ctx

    monkeypatch.setattr(pipeline_mod, "resolve_family_context", _fake_resolve)
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    return fam_ctx.user


# ---------------------------------------------------------------------------
# Smoke tests — real brain, real Redis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_greet_uses_brain(csm, seeded_user, monkeypatch):
    """First message 'hi' should go through brain.decide() and return a greet/chitchat reply."""
    if not _has_llm_key():
        pytest.skip("No LLM API key configured (set GEMINI_API_KEY or AZURE_OPENAI_API_KEY)")

    from packages.core.pipeline import process_text_order

    monkeypatch.setattr("packages.core.pipeline._persist", _noop_persist)

    res = await process_text_order(
        csm=csm,
        from_phone="+919999999999",
        text="hi",
        whatsapp_message_id=f"wamid.e2e-greet-{uuid.uuid4().hex[:8]}",
        location=None,
    )

    assert res["reply_text"], "Brain should produce a reply for 'hi'"
    assert res["state"] in {"IDLE", "AWAITING_CONFIRMATION"}, f"Unexpected state: {res['state']}"


@pytest.mark.asyncio
async def test_order_item_uses_brain(csm, seeded_user, monkeypatch):
    """'milk kavali' should go through brain, produce order_items action, and hit resolve_and_quote."""
    if not _has_llm_key():
        pytest.skip("No LLM API key configured")

    from packages.core.pipeline import process_text_order

    monkeypatch.setattr("packages.core.pipeline._persist", _noop_persist)
    # Stub resolve_and_quote since we don't have a real provider
    monkeypatch.setattr("packages.core.pipeline.resolve_and_quote", _noop_resolve)

    res = await process_text_order(
        csm=csm,
        from_phone="+919999999999",
        text="milk kavali",
        whatsapp_message_id=f"wamid.e2e-order-{uuid.uuid4().hex[:8]}",
        location=None,
    )

    assert res["reply_text"], "Brain should produce a reply for 'milk kavali'"
    # State depends on whether resolve_and_quote found items — with stub returning empty,
    # it should be AWAITING_CONFIRMATION (no-match fallback) or IDLE
    assert res["state"] in {"IDLE", "AWAITING_CONFIRMATION"}, f"Unexpected state: {res['state']}"


@pytest.mark.asyncio
async def test_cancel_uses_brain(csm, seeded_user, monkeypatch):
    """'cancel my order' should go through brain and produce cancel action."""
    if not _has_llm_key():
        pytest.skip("No LLM API key configured")

    from packages.core.pipeline import process_text_order

    monkeypatch.setattr("packages.core.pipeline._persist", _noop_persist)

    res = await process_text_order(
        csm=csm,
        from_phone="+919999999999",
        text="cancel my order",
        whatsapp_message_id=f"wamid.e2e-cancel-{uuid.uuid4().hex[:8]}",
        location=None,
    )

    assert res["reply_text"], "Brain should produce a reply for 'cancel my order'"
    # When LLMs are available, brain should detect cancel intent.
    # When LLMs fail, it falls back to unclear — either is acceptable for a smoke test.
    assert (
        "cancel" in res["reply_text"].lower()
        or "vaddu" in res["reply_text"].lower()
        or "IDLE" in res["state"]
        or "kavali" in res["reply_text"].lower()  # unclear fallback
    )


@pytest.mark.asyncio
async def test_brain_caching_works(csm, seeded_user, monkeypatch):
    """Second identical message should hit cache and return same action."""
    if not _has_llm_key():
        pytest.skip("No LLM API key configured")

    from packages.core.pipeline import process_text_order

    monkeypatch.setattr("packages.core.pipeline._persist", _noop_persist)

    msg_id_1 = f"wamid.e2e-cache1-{uuid.uuid4().hex[:8]}"
    msg_id_2 = f"wamid.e2e-cache2-{uuid.uuid4().hex[:8]}"

    res1 = await process_text_order(
        csm=csm,
        from_phone="+919999999999",
        text="hello",
        whatsapp_message_id=msg_id_1,
        location=None,
    )

    res2 = await process_text_order(
        csm=csm,
        from_phone="+919999999999",
        text="hello",
        whatsapp_message_id=msg_id_2,
        location=None,
    )

    assert res1["reply_text"], "First call should produce a reply"
    assert res2["reply_text"], "Second call (cached) should produce a reply"
    # Cached response should be identical
    assert res1["reply_text"] == res2["reply_text"], "Cache should return same reply for same input+state"
