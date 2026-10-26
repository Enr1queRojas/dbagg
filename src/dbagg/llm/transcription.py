"""Voice-note transcription. Audio stays in memory; transcripts are never logged here."""

import httpx

OPUS_SAMPLE_RATE = 48000
# Short domain hint; it biases spelling of common words, it never adds instructions.
VOCABULARY_HINT = "Consultas de saldos, pagos, clientes, gastos, inventario y maíz."


def ogg_opus_seconds(data):
    """Duration of an Ogg Opus file from its last page granule, or None if not Ogg Opus."""
    if not data.startswith(b"OggS"):
        return None
    head = data.find(b"OpusHead")
    last = data.rfind(b"OggS")
    if head < 0 or last < 0 or len(data) < last + 14 or len(data) < head + 12:
        return None
    granule = int.from_bytes(data[last + 6 : last + 14], "little", signed=True)
    pre_skip = int.from_bytes(data[head + 10 : head + 12], "little")
    if granule < 0:
        return None
    return max(0, granule - pre_skip) / OPUS_SAMPLE_RATE


class Transcriber:
    def __init__(self, config, api_key, transport=None):
        self.config = config
        self.api_key = api_key
        self.transport = transport

    def transcribe(self, data, mime_type):
        """Return the transcript text; raises httpx errors on provider failures."""
        extension = "ogg" if "ogg" in mime_type else "mp3" if "mpeg" in mime_type else "m4a"
        with httpx.Client(timeout=self.config.timeout_seconds, transport=self.transport) as client:
            response = client.post(
                self.config.base_url.rstrip("/") + "/audio/transcriptions",
                headers={"Authorization": "Bearer " + self.api_key},
                files={"file": (f"nota.{extension}", data, mime_type.split(";")[0])},
                data={
                    "model": self.config.model,
                    "language": self.config.language,
                    "response_format": "json",
                    "prompt": " ".join(filter(None, (VOCABULARY_HINT, self.config.hint))),
                },
            )
            response.raise_for_status()
            text = response.json().get("text", "")
        return " ".join(str(text).split())
