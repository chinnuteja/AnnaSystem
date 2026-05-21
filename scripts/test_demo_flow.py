"""Test demo flow for Swiggy founders presentation.

Complex multi-turn, multilingual flow testing:
- Telugu + Hindi + English switching
- Multi-item ordering
- Mid-flow corrections
- Amendments (adding items)
- Cancel before confirm
- Re-order after cancel
- Context awareness across turns
"""
import asyncio
import sys
import uuid
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from redis.asyncio import Redis
from sqlalchemy import text as sql_text
from packages.core.conversation import ConversationStateMachine
from packages.core.pipeline import process_text_order
from packages.core.db import get_session


async def run_flow(name, steps):
    redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    csm = ConversationStateMachine(redis)
    phone = "+918247628278"
    rid = str(uuid.uuid4())[:6]
    await redis.flushall()
    # Clear stale DB sessions so greeting is clean
    try:
        async with get_session() as session:
            await session.execute(sql_text("UPDATE voice_sessions SET outcome='cancelled' WHERE outcome='still_pending'"))
    except Exception:
        pass

    print(f"\n{'#'*60}")
    print(f"  DEMO: {name}")
    print(f"{'#'*60}")

    for i, (msg, label) in enumerate(steps, 1):
        print(f"\n  {'—'*50}")
        print(f"  [{i}] {label}")
        print(f"  USER: {msg}")
        print(f"  {'—'*50}")
        r = await process_text_order(
            csm=csm, from_phone=phone, text=msg,
            whatsapp_message_id=f"{rid}-{i}",
        )
        print(f"  STATE: {r['state']}")
        print(f"  ANNA:")
        for line in r["reply_text"].split("\n"):
            if line.strip():
                print(f"    {line}")

    await redis.aclose()
    print(f"\n  === {name} COMPLETE ===\n")


async def main():
    # ===================================================================
    # FULL COMPLEX DEMO: Multilingual family grocery order
    # ===================================================================
    await run_flow("COMPLEX MULTILINGUAL DEMO WITH ADDRESS", [
        # 1. Telugu greeting
        ("Hi Anna",
         "Telugu greeting — clean start"),

        # 2. Vague request in Telugu
        ("Naku groceries kavali",
         "Vague request — bot asks what items"),

        # 3. Multi-item order with mixed quantities in Telugu
        ("Milk 2, atta, Yippee noodles 4 packets, bread kavali",
         "Multi-item with quantities — 4 items in one message"),

        # 4. User hesitates — CANCEL in Hindi (language switch!)
        ("Ruko ruko, cancel karo. Mujhe sochna hai",
         "CANCEL in Hindi — shows language switch mid-flow"),

        # 5. Re-order in Hindi with DIFFERENT items
        ("Ok sorry, order karo — milk 2, atta, bread, curd, eggs",
         "RE-ORDER in Hindi — 5 items, different from before"),

        # 6. Confirm cart — triggers ADDRESS confirmation
        ("Avunu confirm cheyyi",
         "CONFIRM triggers ADDRESS confirmation step"),

        # 7. User says NO — address is wrong
        ("Kadu, address change cheyyi",
         "User says NO — wants to change address"),

        # 8. User provides partial address (no flat number)
        ("Kukatpally, Saikrupa Apartments, 500072",
         "Partial address — bot should ask for flat number"),

        # 9. User gives flat number
        ("Flat 302",
         "Gives flat number — order should be placed now"),
    ])


if __name__ == "__main__":
    asyncio.run(main())
