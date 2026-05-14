import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger("foodleaf.transcriber")


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    success: bool
    language_detected: str = "te-IN"
    confidence: float | None = None
    fallback_used: str | None = None
    failure_reason: str | None = None


async def transcribe_audio(audio_data: bytes, language: str = "te-IN") -> TranscriptionResult:
    """Transcribe Telugu audio bytes into English/Telugu text using Sarvam AI.

    Local mock audio returns a deterministic transcript. Real audio failures are
    explicit so we never create an order from a failed transcription.
    """
    if audio_data == b"mock_audio_bytes":
        logger.warning("Using local mock transcription for mock audio.")
        return TranscriptionResult(
            text="paalu kavali",
            success=True,
            language_detected=language,
            confidence=0.99,
            fallback_used="local_mock",
        )

    if not settings.sarvam_api_key:
        logger.error("SARVAM_API_KEY missing; cannot transcribe real audio.")
        return TranscriptionResult(
            text="",
            success=False,
            language_detected=language,
            failure_reason="missing_sarvam_api_key",
        )
        
    url = "https://api.sarvam.ai/speech-to-text-translate"
    headers = {
        "api-subscription-key": settings.sarvam_api_key
    }
    
    # Sarvam expects multipart/form-data
    files = {
        "file": ("audio.ogg", audio_data, "audio/ogg")
    }
    data = {
        "prompt": "",
        "model": "saaras:v1"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, headers=headers, files=files, data=data, timeout=15.0)
            resp.raise_for_status()
            
            # Response format: {"transcript": "I want milk"}
            result = resp.json()
            transcript = result.get("transcript", "")
            if transcript:
                logger.info(f"Sarvam Transcription success: '{transcript}'")
                return TranscriptionResult(
                    text=transcript,
                    success=True,
                    language_detected=result.get("language_code") or language,
                    confidence=result.get("confidence"),
                )

            logger.warning("Sarvam API returned empty transcript.")
            return TranscriptionResult(
                text="",
                success=False,
                language_detected=language,
                failure_reason="empty_transcript",
            )
                
        except Exception as e:
            logger.error(f"Sarvam STT failed: {e}")
            return TranscriptionResult(
                text="",
                success=False,
                language_detected=language,
                failure_reason="sarvam_stt_failed",
            )


async def synthesize_speech(text: str, language: str = "te-IN") -> bytes | None:
    """Generate Telugu speech audio with Sarvam when credentials are available."""
    if not settings.sarvam_api_key:
        logger.info("SARVAM_API_KEY missing; skipping TTS and using text-only reply.")
        return None

    url = "https://api.sarvam.ai/text-to-speech"
    headers = {"api-subscription-key": settings.sarvam_api_key}
    payload = {
        "text": text,
        "target_language_code": language,
        "speaker": "anushka",
        "model": "bulbul:v2",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=15.0)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if content_type.startswith("audio/"):
                return resp.content

            data = resp.json()
            audio = data.get("audio") or data.get("audio_content")
            if not audio and isinstance(data.get("audios"), list) and data["audios"]:
                audio = data["audios"][0]
            if isinstance(audio, str):
                import base64

                return base64.b64decode(audio)

            logger.warning("Sarvam TTS response did not include audio.")
            return None
        except Exception as e:
            logger.error("Sarvam TTS failed: %s", e)
            return None
