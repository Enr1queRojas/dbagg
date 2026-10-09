"""Authenticated WhatsApp webhook and application factory."""

import hashlib
import hmac
import json
import logging
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from dbagg.config import Settings
from dbagg.agent.service import Assistant
from dbagg.services.memory import MessageGate
from dbagg.services.conversation import ConversationService, CONTROL_COMMANDS, command_name
from dbagg.integrations.meta import MetaClient
from dbagg.limits import MAX_QUESTION_CHARS

logger = logging.getLogger("uvicorn.error")


def create_app(settings=None, assistant=None, send_message=None, feedback_store=None):
    settings = settings or Settings.from_env()
    assistant = assistant or Assistant(settings)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = MessageGate()
    deliver = send_message or MetaClient(settings).send
    conversation = ConversationService(settings, assistant, deliver, feedback_store)

    @app.get("/health")
    def health():
        return {"status": "ok", "scope": "webhook only; external services not checked"}

    @app.get("/webhook", response_class=PlainTextResponse)
    def verify(request: Request):
        params = request.query_params
        if params.get("hub.mode") != "subscribe" or not hmac.compare_digest(
            params.get("hub.verify_token", ""), settings.verify_token
        ):
            raise HTTPException(403, "Verificación rechazada")
        return params.get("hub.challenge", "")

    @app.post("/webhook")
    async def webhook(request: Request, background: BackgroundTasks):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 256000:
                raise HTTPException(413, "Carga demasiado grande")
            chunks.append(chunk)
        body = b"".join(chunks)
        signature = (
            "sha256=" + hmac.new(settings.app_secret.encode(), body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(request.headers.get("x-hub-signature-256", ""), signature):
            logger.info("dbagg stage=signature_rejected")
            raise HTTPException(403, "Firma inválida")
        try:
            payload = json.loads(body)
            if payload.get("object") != "whatsapp_business_account":
                return {"status": "ignored"}
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if value.get("metadata", {}).get("phone_number_id") != settings.phone_id:
                        logger.info("dbagg stage=ignored_phone_id")
                        continue
                    for message in value.get("messages", []):
                        sender = message.get("from", "")
                        if sender not in settings.numbers or message.get("type") != "text":
                            logger.info("dbagg stage=ignored_sender_or_type")
                            continue
                        text = message.get("text", {}).get("body", "").strip()
                        message_id = message.get("id", "")
                        if (
                            message_id
                            and 0 < len(text) <= MAX_QUESTION_CHARS
                            and gate.claim(
                                message_id, sender, control=command_name(text) in CONTROL_COMMANDS
                            )
                        ):
                            logger.info("dbagg stage=message_accepted")
                            background.add_task(conversation.process, sender, text)
                        else:
                            logger.info("dbagg stage=ignored_duplicate_rate_or_length")
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Evento inválido") from None
        return {"status": "accepted"}

    return app
