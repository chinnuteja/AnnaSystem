"""End-to-end Discovery Agent test.

Run:
    python scripts/test_discovery_pipeline.py
"""

from __future__ import annotations

import asyncio
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
from packages.providers.interface import Location

AMMA_PHONE = "+919876543210"
LOCATION = Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad")


async def main():
    print("foodleaf Discovery Pipeline Test")
    print("=" * 60)

    redis = Redis.from_url("redis://localhost:6379/3", decode_responses=True)
    await redis.ping()
    await redis.flushdb()
    csm = ConversationStateMachine(redis)

    print('\nTest 1: "Dinner ki manchi option chudu"')
    r1 = await process_text_order(
        csm,
        AMMA_PHONE,
        "Dinner ki manchi option chudu",
        f"wamid.{uuid.uuid4().hex[:8]}",
        location=LOCATION,
    )
    print(r1["reply_text"])
    assert r1["state"] == "AWAITING_CONFIRMATION"
    assert "1." in r1["reply_text"]
    assert "Why:" in r1["reply_text"]

    print('\nTest 2: "more options"')
    r2 = await process_text_order(
        csm,
        AMMA_PHONE,
        "more options",
        f"wamid.{uuid.uuid4().hex[:8]}",
    )
    print(r2["reply_text"])
    assert r2["state"] == "AWAITING_CONFIRMATION"
    assert "Why:" in r2["reply_text"]

    print('\nTest 3: "first one"')
    r3 = await process_text_order(
        csm,
        AMMA_PHONE,
        "first one",
        f"wamid.{uuid.uuid4().hex[:8]}",
    )
    print(r3["reply_text"])
    assert r3["state"] == "AWAITING_CONFIRMATION"
    assert "Confirm chey-yana" in r3["reply_text"]

    print('\nTest 4: "avunu"')
    r4 = await process_text_order(
        csm,
        AMMA_PHONE,
        "avunu",
        f"wamid.{uuid.uuid4().hex[:8]}",
    )
    print(r4["reply_text"])
    assert r4["state"] == "COMPLETE"

    await redis.flushdb()
    await redis.aclose()

    print("\nAll discovery tests PASSED")


if __name__ == "__main__":
    asyncio.run(main())
