"""Meta delivery and privacy-preserving error metadata."""

import httpx


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
            chunks, size = [], 0
            with client.stream("GET", meta["url"], headers=headers) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise MediaTooLarge()
                    chunks.append(chunk)
        return b"".join(chunks), str(meta.get("mime_type", "audio/ogg"))

    def send(self, sender, text):
        with httpx.Client(timeout=15, transport=self.transport) as client:
            response = client.post(
                f"https://graph.facebook.com/{self.settings.graph_version}/{self.settings.phone_id}/messages",
                headers={"Authorization": "Bearer " + self.settings.meta_token},
                json={
                    "messaging_product": "whatsapp",
                    "to": sender,
                    "type": "text",
                    "text": {"body": text, "preview_url": False},
                },
            )
            response.raise_for_status()
