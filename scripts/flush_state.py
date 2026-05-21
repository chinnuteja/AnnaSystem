import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from packages.core.db import get_session
from sqlalchemy import text

async def flush():
    import redis
    r = redis.Redis()
    r.flushall()
    print("Redis flushed")
    async with get_session() as s:
        await s.execute(text("UPDATE voice_sessions SET outcome='cancelled' WHERE outcome='still_pending'"))
    print("DB stale sessions cleared")
    print("READY TO RECORD!")

asyncio.run(flush())
