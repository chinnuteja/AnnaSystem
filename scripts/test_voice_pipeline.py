import asyncio
import sys
import uuid
import logging
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from packages.core.conversation import ConversationStateMachine
from packages.core.db import get_session
from packages.core.models import VoiceSession
from packages.core.redis_client import get_redis
from app.worker import process_job

async def test_voice_pipeline():
    print("🧪 foodleaf Voice Pipeline Worker Test")
    print("=" * 60)
    
    redis = await get_redis()
    await redis.flushdb()
    csm = ConversationStateMachine(redis)
    
    job = {
        "message_id": f"wamid.{uuid.uuid4().hex[:8]}",
        "type": "audio",
        "media_id": "mock_media_id_123",
        "from": "+919876543210" # Amma's phone
    }
    
    print("\n📱 Sending mock audio job to worker...")
    await process_job(job, csm)

    # Fetch by WhatsApp message id because the conversation session id is generated internally.
    async with get_session() as session:
        result = await session.execute(
            select(VoiceSession).where(VoiceSession.whatsapp_message_id == job["message_id"])
        )
        voice_session = result.scalar_one()

    assert voice_session.input_mode == "voice", f"Expected voice input_mode, got {voice_session.input_mode}"
    assert voice_session.transcription_raw == "paalu kavali"
    print("   ✅ DB voice_session persisted with input_mode=voice")
    
    print("\n✅ Voice Pipeline Test complete.")
    
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_voice_pipeline())
