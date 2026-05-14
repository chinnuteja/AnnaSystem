"""WhatsApp Cloud API webhook handler — F5 per 03_FEATURE_BUILD_ORDER.md.

Adapted for Meta WhatsApp Cloud API (not Gupshup).
The user will plug in their test number credentials in .env tomorrow.

Flow:
    Meta sends POST /webhook/whatsapp
    → Signature verification (HMAC-SHA256)
    → Idempotency check (Redis SETNX dedup:msg:{id}, 24h TTL)
    → Enqueue to Redis list `message_pipeline:incoming`
    → Return 200 immediately (< 300ms)

Required .env keys:
    WHATSAPP_VERIFY_TOKEN    — for GET webhook verification
    WHATSAPP_APP_SECRET      — for POST signature verification (HMAC)
    WHATSAPP_ACCESS_TOKEN    — for sending messages back
    WHATSAPP_PHONE_NUMBER_ID — the test phone number ID
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request, Response

from packages.core.redis_client import get_redis

logger = logging.getLogger("foodleaf.webhook")

router = APIRouter()

# Dedup TTL: 24 hours in seconds
DEDUP_TTL_SECONDS = 24 * 60 * 60

# Redis queue key
QUEUE_KEY = "message_pipeline:incoming"


# ============================================================================
# GET /webhook/whatsapp — Meta Verification Challenge
# ============================================================================

@router.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    """Meta sends a GET request to verify webhook ownership.

    We check that hub.verify_token matches our WHATSAPP_VERIFY_TOKEN,
    then echo back hub.challenge.

    Note: Meta uses dotted query keys (`hub.mode`, `hub.verify_token`). Reading
    them from `request.query_params` avoids FastAPI/Starlette binding edge cases
    where `Query(alias="hub.verify_token")` can arrive as None and break verify.
    """
    from app.core.config import settings

    params = request.query_params
    hub_mode = params.get("hub.mode") or params.get("hub_mode")
    hub_challenge = params.get("hub.challenge") or params.get("hub_challenge")
    hub_verify_token = params.get("hub.verify_token") or params.get("hub_verify_token")
    expected = (settings.whatsapp_verify_token or "").strip()
    got = (hub_verify_token or "").strip()

    if hub_mode == "subscribe" and got == expected and hub_challenge:
        logger.info("Webhook verification successful")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(
        "Webhook verification failed: mode=%r token_match=%s challenge=%r",
        hub_mode,
        got == expected,
        hub_challenge is not None,
    )
    raise HTTPException(status_code=403, detail="Verification failed")


# ============================================================================
# POST /webhook/whatsapp — Incoming Messages
# ============================================================================

@router.post("/webhook/whatsapp")
async def handle_webhook(request: Request):
    """Handle incoming WhatsApp messages from Meta Cloud API.

    Steps:
        1. Verify HMAC-SHA256 signature (if APP_SECRET is configured)
        2. Parse the webhook payload
        3. Extract message(s)
        4. Dedup check via Redis SETNX
        5. Enqueue to Redis list for async processing
        6. Return 200 immediately
    """
    start_time = time.monotonic()

    # 1. Read raw body for signature verification
    body = await request.body()
    peer = request.client.host if request.client else "?"
    logger.info(
        "WhatsApp webhook POST received from %s, %d bytes, has_sig=%s",
        peer,
        len(body),
        bool(request.headers.get("X-Hub-Signature-256")),
    )

    # 2. Verify signature (skip if APP_SECRET not configured — dev mode)
    from app.core.config import settings
    app_secret = getattr(settings, "whatsapp_app_secret", "")
    if app_secret:
        signature_header = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(body, signature_header, app_secret):
            logger.warning("Invalid webhook signature — rejecting")
            raise HTTPException(status_code=401, detail="Invalid signature")

    # 3. Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 4. Extract messages from Meta's nested structure
    messages = _extract_messages(payload)

    if not messages:
        # Status updates, read receipts, etc. — acknowledge but don't process
        return {"status": "ok", "processed": 0}

    redis = await get_redis()
    enqueued = 0

    for msg in messages:
        msg_id = msg.get("id")
        if not msg_id:
            continue

        # 5. Idempotency: SETNX — only process if we haven't seen this message
        dedup_key = f"dedup:msg:{msg_id}"
        is_new = await redis.set(dedup_key, "1", ex=DEDUP_TTL_SECONDS, nx=True)

        if not is_new:
            logger.info(f"Duplicate message {msg_id} — skipping")
            continue

        # 6. Enqueue to Redis list for async processing
        job = _build_worker_job(msg)
        await redis.lpush(QUEUE_KEY, json.dumps(job))
        enqueued += 1
        logger.info(
            "Enqueued message %s (raw_type=%s job_type=%s text_len=%d)",
            msg_id,
            msg.get("type"),
            job.get("type"),
            len(job.get("text") or ""),
        )

    elapsed_ms = (time.monotonic() - start_time) * 1000
    logger.info(f"Webhook processed in {elapsed_ms:.1f}ms — enqueued {enqueued} messages")

    return {"status": "ok", "processed": enqueued}


# ============================================================================
# Helpers
# ============================================================================

def _extract_user_text_and_canonical_type(msg: dict) -> tuple[str, str]:
    """Map Meta inbound payloads to worker `type` + `text` the pipeline understands.

    Interactive (quick-reply / list) and legacy `button` payloads become ``type=text``.
    """
    raw = (msg.get("type") or "").lower()
    if raw == "text":
        body = (msg.get("text") or {}).get("body") or ""
        return body.strip(), "text"
    if raw == "interactive":
        inter = msg.get("interactive") or {}
        itype = (inter.get("type") or "").lower()
        if itype == "button_reply":
            body = (inter.get("button_reply") or {}).get("title") or ""
        elif itype == "list_reply":
            body = (inter.get("list_reply") or {}).get("title") or ""
        else:
            body = ""
        b = body.strip()
        return (b, "text") if b else ("", raw)
    if raw == "button":
        body = (msg.get("button") or {}).get("text") or ""
        b = body.strip()
        return (b, "text") if b else ("", raw)
    return "", raw


def _build_worker_job(msg: dict) -> dict:
    """Single place to normalize fields for ``process_job``."""
    text_body, canonical_type = _extract_user_text_and_canonical_type(msg)
    raw_type = (msg.get("type") or "").lower()
    return {
        "message_id": msg.get("id"),
        "from": msg.get("from", ""),
        "timestamp": msg.get("timestamp", ""),
        "type": canonical_type,
        "raw_type": raw_type,
        "text": text_body,
        "audio": msg.get("audio", {}) if raw_type == "audio" else {},
        "media_id": msg.get("audio", {}).get("id") if raw_type == "audio" else None,
        "mime_type": msg.get("audio", {}).get("mime_type") if raw_type == "audio" else None,
        "location": _normalize_location(msg.get("location", {})) if raw_type == "location" else None,
        "enqueued_at": time.time(),
    }


def _verify_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 HMAC signature."""
    if not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[7:]  # strip "sha256=" prefix
    computed_sig = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_sig, expected_sig)


def _extract_messages(payload: dict) -> list[dict]:
    """Extract message objects from Meta's nested webhook payload.

    Meta's format:
    {
      "object": "whatsapp_business_account",
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{ "id": "...", "from": "...", "type": "text|audio", ... }]
          }
        }]
      }]
    }
    """
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            msgs = value.get("messages", [])
            messages.extend(msgs)
    return messages


def _normalize_location(location: dict) -> dict:
    """Normalize Meta WhatsApp location messages into the worker's Location payload."""
    return {
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "name": location.get("name"),
        "address": location.get("address"),
        "city": "Hyderabad",
        "pincode": "500032",
    }
