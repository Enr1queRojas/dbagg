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


class MetaClient:
    def __init__(self, settings):
        self.settings = settings

    def send(self, sender, text):
        with httpx.Client(timeout=15) as client:
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
