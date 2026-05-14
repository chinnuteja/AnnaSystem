"""Message Pipeline Worker — picks jobs from Redis queue and processes them.

Run:
    python -m app.worker

Picks WhatsApp messages off the Redis queue and runs them through:
    message_parser → sku_mapper → confirmation → state machine → DB persist
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
import time
from typing import Awaitable
from urllib.parse import urlparse

# Ensure project root AND apps/api are on path
ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from packages.core.conversation import ConversationStateMachine
from packages.core.redis_client import REDIS_URL, get_redis
from packages.core.pipeline import mark_ack_message_sent, process_location_message, process_text_order
from packages.providers.interface import Location

from app.integrations.whatsapp import download_media, send_audio_message, send_text_message
from app.agents.acknowledgement import ACK_DELAY_SECONDS, HARD_TIMEOUT_SECONDS, select_ack_text
# message_parser removed — brain handles all intent parsing now
from app.agents.transcriber import synthesize_speech, transcribe_audio
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("foodleaf.worker")

QUEUE_KEY = "message_pipeline:incoming"
POLL_TIMEOUT_SECONDS = 5  # BRPOP timeout


def _redis_log_target(url: str) -> str:
    """Log Redis host/path without credentials."""
    u = urlparse(url)
    host = u.hostname or "?"
    port = f":{u.port}" if u.port else ""
    path = u.path or "/0"
    return f"{host}{port}{path}"


async def _send_reply(to_phone: str, text: str, *, context: str) -> None:
    """Send outbound text; log clearly if Graph API rejects (common: expired token)."""
    if not to_phone or to_phone == "unknown":
        logger.error("%s: missing recipient; not sending reply", context)
        return
    ok = await send_text_message(to_phone, text)
    if not ok:
        logger.error(
            "%s: WhatsApp send failed for %s — verify WHATSAPP_ACCESS_TOKEN and "
            "WHATSAPP_PHONE_NUMBER_ID (tokens expire; regenerate in Meta Developer).",
            context,
            to_phone,
        )


async def _dispatch_extra_notifications(result: dict) -> None:
    """Send payer notification and ordering-user confirmation if present in result."""
    if result.get("notify_payer"):
        payer_info = result["notify_payer"]
        phone = payer_info.get("phone")
        text = payer_info.get("text")
        if phone and text:
            logger.info("Dispatching payer notification to %s (family=%s, state=%s)",
                        phone, result.get("family_id", "?"), result.get("state", "?"))
            await _send_reply(phone, text, context="payer_notification")
    if result.get("notify_ordering_user"):
        ou_info = result["notify_ordering_user"]
        phone = ou_info.get("phone")
        text = ou_info.get("text")
        if phone and text:
            logger.info("Dispatching ordering-user notification to %s (family=%s, state=%s)",
                        phone, result.get("family_id", "?"), result.get("state", "?"))
            await _send_reply(phone, text, context="ordering_user_notification")


async def process_job(job: dict, csm: ConversationStateMachine):
    """Process a single incoming WhatsApp message job."""
    msg_id = job.get("message_id", "unknown")
    msg_type = (job.get("type") or "unknown").lower()
    from_phone = job.get("from", "unknown")
    text = (job.get("text") or "").strip()

    # Worker-side dedup safety net (webhook dedup is primary)
    redis = csm._redis  # noqa: SLF001
    dedup_key = f"dedup:worker:msg:{msg_id}"
    is_new = await redis.set(dedup_key, "1", ex=86400, nx=True)
    if not is_new:
        logger.info(f"Worker dedup: skipping duplicate message {msg_id}")
        return

    logger.info(f"Processing message {msg_id} from {from_phone} (type={msg_type})")

    if msg_type == "text" and text:
        # Brain handles all parsing — no trivial parse needed
        skip_ack = True
        location = _location_from_job(job)
        result = await _run_with_ack(
            pipeline_call=process_text_order(
                csm=csm,
                from_phone=from_phone,
                text=text,
                whatsapp_message_id=msg_id,
                location=location,
            ),
            csm=csm,
            to_phone=from_phone,
            input_mode="text",
            skip_ack=skip_ack,
        )

        logger.info(
            f"Pipeline result: state={result['state']}, "
            f"session={result['voice_session_id']}"
        )
        logger.info(f"Reply to {result['reply_to']}: \"{result['reply_text']}\"")

        await _send_reply(result["reply_to"], result["reply_text"], context="text_pipeline")
        await _dispatch_extra_notifications(result)

    elif msg_type == "text" and not text:
        logger.warning(
            "Empty text body for message %s (raw_type=%s)",
            msg_id,
            job.get("raw_type"),
        )
        await _send_reply(
            from_phone,
            "Message body clear ga raanattu undi. Dayachesi malli type cheyyandi.",
            context="empty_text",
        )

    elif msg_type == "audio":
        media_id = job.get("media_id") or job.get("audio", {}).get("id")
        if not media_id:
            logger.warning("Audio message missing media_id")
            return
            
        logger.info(f"Downloading audio media_id={media_id} for F6 STT")
        t_dl0 = time.monotonic()
        audio_bytes = await download_media(media_id)
        media_download_ms = int((time.monotonic() - t_dl0) * 1000)

        if not audio_bytes:
            logger.error("Failed to download audio bytes. Skipping.")
            return
            
        logger.info("Transcribing audio with Sarvam AI")
        t_stt0 = time.monotonic()
        transcription = await transcribe_audio(audio_bytes)
        stt_ms = int((time.monotonic() - t_stt0) * 1000)
        
        if not transcription.success or not transcription.text.strip():
            reply_text = "Voice note clear ga vinipinchaledu. Dayachesi malli cheppandi leka text lo pampandi."
            logger.warning(
                "Audio transcription failed for %s: %s",
                msg_id,
                transcription.failure_reason,
            )
            await _send_reply(from_phone, reply_text, context="stt_failed")
            return

        transcribed_text = transcription.text.strip()
        logger.info(f"Audio Transcribed to Text: '{transcribed_text}'")
        
        # Route the transcribed text back into the text pipeline!
        client_timings = {
            "media_download_ms": media_download_ms,
            "stt_ms": stt_ms,
        }
        result = await _run_with_ack(
            pipeline_call=process_text_order(
                csm=csm,
                from_phone=from_phone,
                text=transcribed_text,
                whatsapp_message_id=msg_id,
                input_mode="voice",
                audio_r2_key=f"whatsapp_media/{media_id}",
                transcription_raw=transcribed_text,
                transcription_confidence=transcription.confidence,
                location=_location_from_job(job),
                client_timings=client_timings,
            ),
            csm=csm,
            to_phone=from_phone,
            input_mode="voice",
        )

        logger.info(
            f"Voice Pipeline result: state={result['state']}, "
            f"session={result['voice_session_id']}"
        )
        logger.info(f"Reply to {result['reply_to']}: \"{result['reply_text']}\"")

        # Text-first reply; optional Sarvam TTS follow-up (does not block the text path).
        await _send_reply(result["reply_to"], result["reply_text"], context="voice_pipeline")
        await _dispatch_extra_notifications(result)
        if settings.voice_followup_tts:
            audio_reply = await synthesize_speech(result["reply_text"], transcription.language_detected)
            if audio_reply:
                await send_audio_message(result["reply_to"], audio_reply)
    elif msg_type == "location":
        location = _location_from_job(job)
        if location is None:
            await _send_reply(
                from_phone,
                "Location details ravaledu. Dayachesi malli share cheyyandi.",
                context="location_invalid",
            )
            return

        result = await _run_with_ack(
            pipeline_call=process_location_message(
                csm=csm,
                from_phone=from_phone,
                whatsapp_message_id=msg_id,
                location=location,
            ),
            csm=csm,
            to_phone=from_phone,
            input_mode="text",
        )
        logger.info(f"Location Pipeline result: state={result['state']}, session={result['voice_session_id']}")
        await _send_reply(result["reply_to"], result["reply_text"], context="location_pipeline")
        await _dispatch_extra_notifications(result)

    else:
        logger.info(f"Unsupported message type: {msg_type}")


def _location_from_job(job: dict) -> Location | None:
    raw = job.get("location")
    if not raw:
        return None
    try:
        return Location(
            latitude=float(raw["latitude"]),
            longitude=float(raw["longitude"]),
            pincode=raw.get("pincode") or "500032",
            city=raw.get("city") or "Hyderabad",
            address_line=raw.get("address") or raw.get("address_line"),
            landmark=raw.get("name") or raw.get("landmark"),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Invalid location payload in job: %s", raw)
        return None


async def _run_with_ack(
    pipeline_call: Awaitable[dict],
    csm: ConversationStateMachine,
    to_phone: str,
    input_mode: str,
    skip_ack: bool = False,
) -> dict:
    """Send an acknowledgement if processing crosses the perceived-latency budget."""
    task = asyncio.create_task(pipeline_call)
    ack_sent = False

    try:
        if skip_ack and input_mode != "voice":
            return await asyncio.wait_for(task, timeout=max(0.1, HARD_TIMEOUT_SECONDS))

        done, _pending = await asyncio.wait({task}, timeout=ACK_DELAY_SECONDS)
        if not done:
            ack_text = await select_ack_text(csm._redis)  # noqa: SLF001 - CSM owns the active Redis client.
            await _send_reply(to_phone, ack_text, context="slow_path_ack")
            if input_mode == "voice" and settings.voice_ack_audio:
                ack_audio = await synthesize_speech(ack_text)
                if ack_audio:
                    await send_audio_message(to_phone, ack_audio)
            ack_sent = True

        result = await asyncio.wait_for(task, timeout=max(0.1, HARD_TIMEOUT_SECONDS - ACK_DELAY_SECONDS))
    except asyncio.TimeoutError:
        task.cancel()
        failure_text = "Sorry, konchem technical delay vachindi. Dayachesi malli try cheyandi."
        return {
            "reply_text": failure_text,
            "reply_to": to_phone,
            "voice_session_id": None,
            "state": "IDLE",
        }
    except Exception:
        logger.exception("Pipeline crashed for recipient %s", to_phone)
        failure_text = (
            "Sorry, ippudu oka technical issue vachindi. Konchem tarvata malli 'hi' ani try cheyandi."
        )
        return {
            "reply_text": failure_text,
            "reply_to": to_phone,
            "voice_session_id": None,
            "state": "IDLE",
        }

    if ack_sent:
        await mark_ack_message_sent(result.get("voice_session_id"))
    return result


async def run_worker():
    """Main worker loop — BRPOP from Redis queue, process each job."""
    redis = await get_redis()
    csm = ConversationStateMachine(redis)

    logger.info(
        "Worker started — Redis %s queue=%s (API must use the same REDIS_URL)",
        _redis_log_target(REDIS_URL),
        QUEUE_KEY,
    )

    while True:
        try:
            # BRPOP blocks until a job is available (or timeout)
            result = await redis.brpop(QUEUE_KEY, timeout=POLL_TIMEOUT_SECONDS)

            if result is None:
                continue

            _key, raw_job = result
            job = json.loads(raw_job)
            try:
                await process_job(job, csm)
            except Exception:
                logger.exception("Unhandled error for job %s", job.get("message_id"))
                to = job.get("from") or ""
                if to:
                    await _send_reply(
                        to,
                        "Sorry, oka unexpected error vachindi. Team ki log vellindi; malli try cheyandi.",
                        context="job_fatal",
                    )

        except asyncio.CancelledError:
            logger.info("Worker shutting down")
            break
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            await asyncio.sleep(1)


if __name__ == "__main__":
    logger.info("Starting foodleaf message pipeline worker...")
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
