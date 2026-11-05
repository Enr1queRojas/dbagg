"""Meta delivery and privacy-preserving error metadata."""

import logging
import time
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("uvicorn.error")

# The media download carries the access token, so it only goes to Meta's own hosts.
MEDIA_HOSTS = ("fbsbx.com", "facebook.com", "fbcdn.net", "whatsapp.net")

_MAX_ATTEMPTS = 3


def is_meta_media_url(url):
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == domain or host.endswith("." + domain) for domain in MEDIA_HOSTS
    )


def meta_error_codes(response):
    """Extract numeric API codes only; response messages can contain private data."""
    try:
        body = response.json()
    except ValueError:
        return None, None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None
    return tuple(
        value if type(value) is int and 0 <= value <= 999999999 else None
        for value in (error.get("code"), error.get("error_subcode"))
    )


class MediaTooLarge(Exception):
    """The media exceeds the configured download limit."""


class MetaClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def download_media(self, media_id, max_bytes):
        """Download a WhatsApp media object into memory; returns (bytes, mime_type)."""
        headers = {"Authorization": "Bearer " + self.settings.meta_token}
        with httpx.Client(timeout=20, transport=self.transport) as client:
            info = client.get(
                f"https://graph.facebook.com/{self.settings.graph_version}/{media_id}",
                headers=headers,
            )
            info.raise_for_status()
            meta = info.json()
            declared = meta.get("file_size")
            if type(declared) is int and declared > max_bytes:
                raise MediaTooLarge()
            if not is_meta_media_url(meta["url"]):
                raise ValueError("URL de medio fuera de los dominios de Meta.")
            chunks, size = [], 0
            with client.stream("GET", meta["url"], headers=headers) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise MediaTooLarge()
                    chunks.append(chunk)
        return b"".join(chunks), str(meta.get("mime_type", "audio/ogg"))

    def _post_with_retries(self, path, timeout, **request):
        """POST to the phone's Graph path, retrying 429, 5xx and timeouts; raises on failure."""
        last_exc = None
        for attempt in range(_MAX_ATTEMPTS):
            if attempt > 0:
                sleep_secs = 2 ** (attempt - 1)
                logger.info(
                    "dbagg stage=document_retry attempt=%s backoff_secs=%s", attempt, sleep_secs
                )
                time.sleep(sleep_secs)
            try:
                with httpx.Client(timeout=timeout, transport=self.transport) as client:
                    response = client.post(
                        f"https://graph.facebook.com/{self.settings.graph_version}"
                        f"/{self.settings.phone_id}/{path}",
                        headers={"Authorization": "Bearer " + self.settings.meta_token},
                        **request,
                    )
            except httpx.TimeoutException as exc:
                last_exc = exc
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                continue
            response.raise_for_status()
            return response
        raise last_exc

    def send_document(self, sender, content, filename):
        """Upload a PDF to Meta and send it; the bytes stay in memory and out of the logs."""
        upload = self._post_with_retries(
            "media",
            timeout=60,
            data={"messaging_product": "whatsapp", "type": "application/pdf"},
            files={"file": (filename, content, "application/pdf")},
        )
        media_id = upload.json().get("id")
        if not media_id:
            raise ValueError("Meta no devolvió un identificador para el documento.")
        self._post_with_retries(
            "messages",
            timeout=15,
            json={
                "messaging_product": "whatsapp",
                "to": sender,
                "type": "document",
                "document": {"id": media_id, "filename": filename},
            },
        )

    def send(self, sender, text, outbox=None, row_id=None):
        """Envía un mensaje de texto por WhatsApp.

        Si se proporciona `outbox`, encola el mensaje antes de intentar el envío
        y aplica reintentos con backoff exponencial para 429, 5xx y timeouts.
        """
        if outbox is not None and row_id is None:
            row_id = outbox.enqueue(sender, text)

        last_exc = None
        for attempt in range(_MAX_ATTEMPTS):
            if attempt > 0:
                sleep_secs = 2 ** (attempt - 1)  # 1 s, 2 s
                logger.info(
                    "dbagg stage=send_retry attempt=%s backoff_secs=%s", attempt, sleep_secs
                )
                time.sleep(sleep_secs)
            if outbox is not None:
                outbox.increment_attempts(row_id)
            try:
                with httpx.Client(timeout=15, transport=self.transport) as client:
                    response = client.post(
                        f"https://graph.facebook.com/{self.settings.graph_version}"
                        f"/{self.settings.phone_id}/messages",
                        headers={"Authorization": "Bearer " + self.settings.meta_token},
                        json={
                            "messaging_product": "whatsapp",
                            "to": sender,
                            "type": "text",
                            "text": {"body": text, "preview_url": False},
                        },
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        last_exc = httpx.HTTPStatusError(
                            f"HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        continue
                    if response.is_error and outbox is not None:
                        # Otro 4xx no se resuelve reintentando, tampoco al reiniciar.
                        outbox.mark_failed(row_id)
                    response.raise_for_status()
                    # Éxito
                    if outbox is not None and row_id is not None:
                        try:
                            wamid = response.json()["messages"][0]["id"]
                        except (KeyError, IndexError, ValueError, TypeError):
                            wamid = None
                        outbox.mark_sent(row_id, wamid)
                    return
            except httpx.TimeoutException as exc:
                last_exc = exc
                continue

        # Intentos agotados
        if outbox is not None and row_id is not None:
            outbox.mark_failed(row_id)
        raise last_exc
