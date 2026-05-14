import logging
import httpx
from pathlib import Path

from app.core.config import settings
from packages.core.phone_utils import graph_api_recipient

logger = logging.getLogger("foodleaf.whatsapp")

GRAPH_API_VERSION = "v21.0"

async def download_media(media_id: str) -> bytes | None:
    """Download media from WhatsApp Cloud API.
    
    If no token is present, returns dummy audio bytes to allow local pipeline testing.
    """
    if not settings.whatsapp_access_token:
        logger.warning("No WHATSAPP_ACCESS_TOKEN. Using mock audio for media_id=%s", media_id)
        # We can return some dummy bytes. The transcriber mock or API might fail if it's not real audio,
        # but for local MVP we will mock the transcriber later or use a real test file if available.
        
        # Check if we have a test.ogg file in the data folder to use as mock
        data_dir = Path(__file__).resolve().parents[4] / "packages" / "providers" / "data"
        test_audio = data_dir / "test.ogg"
        if test_audio.exists():
            return test_audio.read_bytes()
            
        return b"mock_audio_bytes"

    # Real WhatsApp Media Download flow
    # 1. Get Media URL
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers, timeout=10.0)
            resp.raise_for_status()
            media_url = resp.json().get("url")
            
            if not media_url:
                logger.error("No media URL returned from WhatsApp API.")
                return None
                
            # 2. Download the actual binary
            audio_resp = await client.get(media_url, headers=headers, timeout=20.0)
            audio_resp.raise_for_status()
            return audio_resp.content
            
        except Exception as e:
            logger.error(f"Failed to download WhatsApp media {media_id}: {e}")
            return None


async def send_text_message(to_phone: str, text: str) -> bool:
    """Send a WhatsApp text message, or log it in local mode."""
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        logger.info("Local mode WhatsApp text to %s: %s", to_phone, text)
        return True

    to_norm = graph_api_recipient(to_phone)
    if not to_norm:
        logger.error("Refusing WhatsApp send: invalid recipient %r", to_phone)
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_norm,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=10.0)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                logger.error(
                    "WhatsApp text send HTTP %s for %s: %s",
                    resp.status_code,
                    to_norm,
                    (resp.text or "")[:1200],
                )
                return False
            return True
        except Exception as e:
            logger.error("Failed to send WhatsApp text to %s: %s", to_norm, e)
            return False


async def send_audio_message(to_phone: str, audio_bytes: bytes, mime_type: str = "audio/ogg") -> bool:
    """Upload generated audio and send it as a WhatsApp voice/audio message."""
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        logger.info("Local mode WhatsApp audio to %s: %d bytes", to_phone, len(audio_bytes))
        return True

    to_norm = graph_api_recipient(to_phone)
    if not to_norm:
        logger.error("Refusing WhatsApp audio send: invalid recipient %r", to_phone)
        return False

    media_id = await _upload_audio(audio_bytes, mime_type)
    if not media_id:
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_norm,
        "type": "audio",
        "audio": {"id": media_id},
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=10.0)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("Failed to send WhatsApp audio to %s: %s", to_norm, e)
            return False


async def _upload_audio(audio_bytes: bytes, mime_type: str) -> str | None:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.whatsapp_phone_number_id}/media"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    data = {
        "messaging_product": "whatsapp",
        "type": mime_type,
    }
    files = {"file": ("reply.ogg", audio_bytes, mime_type)}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, headers=headers, data=data, files=files, timeout=20.0)
            resp.raise_for_status()
            media_id = resp.json().get("id")
            if not media_id:
                logger.error("WhatsApp media upload did not return an id.")
            return media_id
        except Exception as e:
            logger.error("Failed to upload WhatsApp audio: %s", e)
            return None
