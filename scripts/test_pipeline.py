"""End-to-end pipeline test — simulates WhatsApp text messages through the full system.

Tests the pipeline WITHOUT needing the API server running.
Requires Docker (Postgres + Redis) to be running.

Run:
    python scripts/test_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from redis.asyncio import Redis

from packages.core.conversation import ConversationStateMachine
from packages.core.pipeline import process_text_order


# Amma's phone from seed.py
AMMA_PHONE = "+919876543210"
UNKNOWN_PHONE = "+910000000000"


async def main():
    print("🧪 foodleaf Pipeline Integration Test")
    print("=" * 60)

    # Connect to Redis DB 3 (test-only)
    redis = Redis.from_url("redis://localhost:6379/3", decode_responses=True)
    try:
        await redis.ping()
    except Exception:
        print("❌ Redis not running. Start Docker Desktop first: docker-compose up -d")
        return

    await redis.flushdb()
    csm = ConversationStateMachine(redis)

    # ─── Test 1: Amma orders atta ───────────────────────────
    print(f"\n📱 Test 1: Amma says \"atta teesuko\"")
    r1 = await process_text_order(csm, AMMA_PHONE, "atta teesuko", f"wamid.{uuid.uuid4().hex[:8]}")
    print(f"   State: {r1['state']}")
    print(f"   Reply: \"{r1['reply_text']}\"")
    assert r1["state"] == "AWAITING_CONFIRMATION", f"Expected AWAITING_CONFIRMATION, got {r1['state']}"
    assert "atta" in r1["reply_text"].lower() or "Atta" in r1["reply_text"]
    print("   ✅ PASSED — Order parsed, SKU matched, state=AWAITING_CONFIRMATION")

    # ─── Test 2: Amma confirms the order ─────────────────────
    print(f"\n📱 Test 2: Amma says \"avunu\" (Confirm)")
    r2 = await process_text_order(csm, AMMA_PHONE, "avunu", f"wamid.{uuid.uuid4().hex[:8]}")
    print(f"   State: {r2['state']}")
    print(f"   Reply: \"{r2['reply_text']}\"")
    assert r2["state"] == "COMPLETE", f"Expected COMPLETE, got {r2['state']}"
    print("   ✅ PASSED — Order confirmed, state=COMPLETE")

    # Cancel any residual state for next tests
    for key in await redis.keys("conv:*"):
        await redis.delete(key)

    # ─── Test 3: Amma says hello (chitchat) ──────────────────
    print(f"\n📱 Test 3: Amma says \"hello\"")
    r3 = await process_text_order(csm, AMMA_PHONE, "hello", f"wamid.{uuid.uuid4().hex[:8]}")
    print(f"   State: {r3['state']}")
    print(f"   Reply: \"{r3['reply_text']}\"")
    assert r3["state"] == "IDLE", f"Expected IDLE, got {r3['state']}"
    print("   ✅ PASSED — Chitchat detected, state=IDLE")

    # ─── Test 4: Unknown phone number ────────────────────────
    print(f"\n📱 Test 4: Unknown number \"{UNKNOWN_PHONE}\" says \"atta\"")
    r4 = await process_text_order(csm, UNKNOWN_PHONE, "atta", f"wamid.{uuid.uuid4().hex[:8]}")
    print(f"   State: {r4['state']}")
    print(f"   Reply: \"{r4['reply_text']}\"")
    assert r4["state"] == "IDLE"
    assert "register" in r4["reply_text"].lower()
    print("   ✅ PASSED — Unknown user rejected gracefully")

    # ─── Test 5: Amma orders milk, then cancels ──────────────
    print(f"\n📱 Test 5: Amma says \"paalu kavali\"")
    r5 = await process_text_order(csm, AMMA_PHONE, "paalu kavali", f"wamid.{uuid.uuid4().hex[:8]}")
    print(f"   State: {r5['state']}")
    print(f"   Reply: \"{r5['reply_text']}\"")
    assert r5["state"] == "AWAITING_CONFIRMATION"
    print("   ✅ PASSED — Milk order, state=AWAITING_CONFIRMATION")

    print(f"\n📱 Test 6: Amma says \"vaddu\" (Cancel)")
    r6 = await process_text_order(csm, AMMA_PHONE, "vaddu", f"wamid.{uuid.uuid4().hex[:8]}")
    print(f"   State: {r6['state']}")
    print(f"   Reply: \"{r6['reply_text']}\"")
    assert r6["state"] == "IDLE"
    print("   ✅ PASSED — Order cancelled, state=IDLE")

    # ─── Test 7: Amendment flow ──────────────────────────────
    print(f"\n📱 Test 7: Amma orders atta, then amends with milk")
    r7_a = await process_text_order(csm, AMMA_PHONE, "atta teesuko", f"wamid.{uuid.uuid4().hex[:8]}")
    assert r7_a["state"] == "AWAITING_CONFIRMATION"
    
    r7_b = await process_text_order(csm, AMMA_PHONE, "paalu kavali", f"wamid.{uuid.uuid4().hex[:8]}")
    print(f"   State: {r7_b['state']}")
    print(f"   Reply: \"{r7_b['reply_text']}\"")
    assert r7_b["state"] == "AWAITING_CONFIRMATION"
    assert "Milk" in r7_b["reply_text"] or "milk" in r7_b["reply_text"]
    print("   ✅ PASSED — Amendment processed correctly")

    # Cleanup
    await redis.flushdb()
    await redis.aclose()

    print("\n" + "=" * 60)
    print("🎉 All 7 pipeline tests PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
