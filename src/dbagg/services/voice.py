"""Turn an authorized WhatsApp voice note into question text, or a clear refusal."""

import logging

import httpx

from dbagg.integrations.meta import MediaTooLarge
from dbagg.limits import MAX_AUDIO_BYTES, MAX_QUESTION_CHARS
from dbagg.llm.transcription import Transcriber, ogg_opus_seconds

logger = logging.getLogger("uvicorn.error")

DISABLED = "Las notas de voz no están habilitadas. Escribe tu pregunta, por favor."
FAILED = "No pude transcribir la nota de voz. Intenta de nuevo o escribe tu pregunta."
EMPTY = "No entendí el audio. ¿Puedes repetirlo más despacio o escribir tu pregunta?"


class VoiceError(Exception):
    """Carries the message to send back instead of an answer."""


def build_voice(settings, media):
    """VoiceNotes when transcription is enabled and its key is present; otherwise None."""
    llm = getattr(settings, "llm", None)
    config = getattr(llm, "transcription", None)
    key = getattr(settings, "transcription_key", "")
    if config is None or not config.enabled or not key:
        return None
    return VoiceNotes(media, Transcriber(config, key), config.max_seconds)


class VoiceNotes:
    def __init__(self, media, transcriber, max_seconds):
        self.media = media
        self.transcriber = transcriber
        self.max_seconds = max_seconds

    def too_long(self):
        return (
            f"La nota de voz dura más de {self.max_seconds} segundos. "
            "Envía una más corta o escribe tu pregunta."
        )

    def text(self, media_id):
        try:
            data, mime_type = self.media.download_media(media_id, MAX_AUDIO_BYTES)
        except MediaTooLarge:
            logger.info("dbagg stage=voice_rejected reason=size")
            raise VoiceError(self.too_long()) from None
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("dbagg stage=voice_download_failed error_type=%s", type(exc).__name__)
            raise VoiceError(FAILED) from None
        seconds = ogg_opus_seconds(data)
        if seconds is not None and seconds > self.max_seconds:
            logger.info("dbagg stage=voice_rejected reason=duration")
            raise VoiceError(self.too_long())
        try:
            text = self.transcriber.transcribe(data, mime_type)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "dbagg stage=voice_transcription_failed error_type=%s http_status=%s",
                type(exc).__name__,
                status,
            )
            raise VoiceError(FAILED) from None
        if not text:
            logger.info("dbagg stage=voice_rejected reason=empty")
            raise VoiceError(EMPTY)
        logger.info("dbagg stage=voice_transcribed chars=%s", len(text))
        return text[:MAX_QUESTION_CHARS]
