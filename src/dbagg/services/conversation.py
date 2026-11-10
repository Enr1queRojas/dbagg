"""Coordinate one authorized conversation independently of HTTP transport."""

import logging
import threading
from uuid import uuid4

import httpx

from dbagg.agent.prompts import PROMPT_VERSION
from dbagg.business.statements import DocumentAttachment
from dbagg.evaluation.gaps import GapStore
from dbagg.evaluation.store import FeedbackStore, utc_now
from dbagg.integrations.meta import meta_error_codes
from dbagg.paths import project_root
from dbagg.services.memory import ConversationMemory
from dbagg.llm.usage import stage
from dbagg.services.outbox import OutboxStore
from dbagg.services.voice import DISABLED as VOICE_DISABLED, VoiceError

logger = logging.getLogger("uvicorn.error")

CONTROL_COMMANDS = frozenset({"/diagnostico", "/reiniciar", "/buena", "/mala", "/ayuda"})
RECOVERY_MAX_HOURS = 1
DOCUMENT_FAILED = "No pude enviar el PDF del estado de cuenta. Intenta de nuevo."
UNAUTHORIZED_NOTICE = (
    "Este número no está autorizado para usar este asistente. "
    "Si necesitas acceso, contacta a soporte."
)


def command_name(question):
    return question.strip().split(maxsplit=1)[0].lower() if question.strip() else ""


class ConversationService:
    def __init__(
        self,
        settings,
        assistant,
        deliver,
        feedback_store=None,
        voice=None,
        gap_store=None,
        outbox=None,
        deliver_document=None,
    ):
        self.settings = settings
        self.assistant = assistant
        self.deliver = deliver
        self.deliver_document = deliver_document
        self.voice = voice
        self.gaps = gap_store
        self.outbox = outbox
        if self.gaps is None and getattr(settings, "gap_log_enabled", False):
            self.gaps = GapStore(project_root() / "data" / "gaps.sqlite3")
        self.memory = ConversationMemory()
        self._sender_locks = {}
        self._sender_locks_guard = threading.Lock()
        self.feedback = feedback_store
        if self.feedback is None and settings.feedback_enabled:
            self.feedback = FeedbackStore(
                settings.feedback_path or project_root() / "data" / "feedback.sqlite3"
            )
        self._purge(getattr(settings, "data_retention_days", 90))

    def _purge(self, days):
        """Apply data retention at startup; a failure must not stop the service."""
        stores = (("gaps", self.gaps), ("feedback", self.feedback), ("outbox", self.outbox))
        for name, store in stores:
            if not isinstance(store, (GapStore, FeedbackStore, OutboxStore)):
                continue
            try:
                rows = store.purge(days)
                logger.info("dbagg stage=retention store=%s days=%s rows=%s", name, days, rows)
            except Exception as exc:
                logger.warning(
                    "dbagg stage=retention_failed store=%s error_type=%s", name, type(exc).__name__
                )

    def _rate(self, sender, question, command):
        if self.feedback is None:
            return "Las valoraciones no están habilitadas. El administrador puede activar FEEDBACK_ENABLED=true."
        interaction = self.memory.last_interaction(sender)
        if interaction is None:
            return "No hay una respuesta reciente para valorar. Haz una consulta primero."
        parts = question.strip().split(maxsplit=1)
        reason = parts[1] if len(parts) > 1 else ""
        reference = self.feedback.record(
            interaction, "good" if command == "/buena" else "bad", reason
        )
        return f"Valoración guardada para revisión. Referencia: {reference}"

    def notify_unauthorized(self, sender, release=None):
        """Fixed text only: no model, data, history or outbox, whatever the stranger wrote."""
        try:
            self.deliver(sender, UNAUTHORIZED_NOTICE)
            logger.info("dbagg stage=unauthorized_notified")
        except httpx.HTTPStatusError as exc:
            code, subcode = meta_error_codes(exc.response)
            logger.warning(
                "dbagg stage=unauthorized_notice_failed error_type=HTTPStatusError "
                "http_status=%s meta_code=%s meta_subcode=%s",
                exc.response.status_code,
                code,
                subcode,
            )
            # Another 4xx will fail the same way; retrying on every message only adds calls.
            if release and (exc.response.status_code == 429 or exc.response.status_code >= 500):
                release(sender)
        except Exception as exc:
            logger.warning(
                "dbagg stage=unauthorized_notice_failed error_type=%s", type(exc).__name__
            )
            if release:
                release(sender)

    @stage("conversation")
    def process(self, sender, question):
        self._handle(sender, question)

    @stage("conversation_voice")
    def process_voice(self, sender, media_id):
        """Transcribe a voice note, then answer it like text with an echo of what was heard."""
        if self.voice is None:
            self._send(sender, VOICE_DISABLED)
            return
        try:
            question = self.voice.text(media_id)
        except VoiceError as exc:
            self._send(sender, str(exc))
            return
        self._handle(sender, question, voice=True)

    @stage("delivery")
    def _send(self, sender, text, row_id=None):
        try:
            if self.outbox is not None:
                self.deliver(sender, text, outbox=self.outbox, row_id=row_id)
            else:
                self.deliver(sender, text)
            logger.info("dbagg stage=send_ok")
            return True
        except httpx.HTTPStatusError as exc:
            code, subcode = meta_error_codes(exc.response)
            logger.warning(
                "dbagg stage=send_failed error_type=HTTPStatusError "
                "http_status=%s meta_code=%s meta_subcode=%s",
                exc.response.status_code,
                code,
                subcode,
            )
        except Exception as exc:
            logger.warning("dbagg stage=send_failed error_type=%s", type(exc).__name__)
        return False

    def _take_document(self):
        take = getattr(self.assistant, "pop_document", None)
        document = take() if callable(take) else None
        return document if isinstance(document, DocumentAttachment) else None

    def _send_document(self, sender, document):
        """Never claims delivery on failure; the bytes go nowhere but Meta."""
        try:
            if self.deliver_document is None:
                raise RuntimeError("Sin canal de documentos.")
            self.deliver_document(sender, document.content, document.filename)
            logger.info("dbagg stage=document_ok")
            return True
        except httpx.HTTPStatusError as exc:
            code, subcode = meta_error_codes(exc.response)
            logger.warning(
                "dbagg stage=document_failed error_type=HTTPStatusError "
                "http_status=%s meta_code=%s meta_subcode=%s",
                exc.response.status_code,
                code,
                subcode,
            )
        except Exception as exc:
            logger.warning("dbagg stage=document_failed error_type=%s", type(exc).__name__)
        return False

    def recover_pending(self):
        """Reencola mensajes pendientes al arrancar. No-op si no hay outbox."""
        if self.outbox is None:
            return
        stale = self.outbox.fail_stale(RECOVERY_MAX_HOURS)
        rows = self.outbox.pending()
        for row in rows:
            self._send(row["sender"], row["body"], row_id=row["id"])
        logger.info("dbagg stage=outbox_recovered rows=%s stale=%s", len(rows), stale)

    def _handle(self, sender, question, voice=False):
        with self._sender_locks_guard:
            lock = self._sender_locks.setdefault(sender, threading.RLock())
        with lock:
            self._handle_turn(sender, question, voice)

    def _handle_turn(self, sender, question, voice=False):
        save_turn = False
        answer_failed = False
        history = []
        try:
            # A transcript is always a question: spoken words never run control commands.
            command = "" if voice else command_name(question)
            if command == "/diagnostico":
                answer = (
                    "dbagg: este mensaje llegó al agente local. Diagnóstico del webhook correcto."
                )
                logger.info("dbagg stage=diagnostic_ok")
            elif command == "/reiniciar":
                self.memory.clear(sender)
                answer = "dbagg: conversación reiniciada. ¿Qué quieres consultar?"
            elif command in {"/buena", "/mala"}:
                answer = self._rate(sender, question, command)
            elif command == "/ayuda":
                answer = (
                    "Pregunta por saldos, pagos, gastos o inventario, por texto o nota de voz. "
                    "/reiniciar borra el contexto; /buena o /mala motivo valoran la última "
                    "respuesta si están habilitados."
                )
            else:
                history = self.memory.history(sender)
                modular = getattr(getattr(self.settings, "llm", None), "modular", None)
                if getattr(modular, "enabled", False) is True:
                    answer = self.assistant.answer(
                        question, history=history, conversation_state=self.memory.state(sender)
                    )
                else:
                    answer = (
                        self.assistant.answer(question, history=history)
                        if history
                        else self.assistant.answer(question)
                    )
                save_turn = True
                document = self._take_document()
                if document is not None and not self._send_document(sender, document):
                    answer = DOCUMENT_FAILED
        except Exception as exc:
            answer_failed = True
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else getattr(exc, "status_code", None)
            )
            if type(status) is int and 100 <= status <= 599:
                profile = getattr(getattr(self.settings, "llm", None), "profile", None)
                provider = profile.provider if profile is not None else "openai"
                logger.warning(
                    "dbagg stage=answer_failed error_type=%s provider=%s http_status=%s",
                    type(exc).__name__,
                    provider,
                    status,
                )
            else:
                logger.warning("dbagg stage=answer_failed error_type=%s", type(exc).__name__)
            answer = (
                "No pude completar la consulta. Revisa la conexión y la configuración del servicio."
            )
        # The echo is shown to the user but kept out of the history sent to the model.
        text = f"🎙️ Entendí: «{question}»\n\n{answer}" if voice else answer
        if not self._send(sender, text):
            return
        if answer_failed:
            self.memory.invalidate_rating(sender)
        if save_turn:
            trace = getattr(self.assistant, "last_trace", None)
            trace = trace if isinstance(trace, dict) else {}
            if self.gaps is not None and trace.get("gap"):
                try:
                    self.gaps.record(question, trace, voice=voice)
                    logger.info("dbagg stage=gap_logged category=%s", trace["gap"])
                except Exception as exc:  # the answer was already delivered
                    logger.warning("dbagg stage=gap_log_failed error_type=%s", type(exc).__name__)
            self.memory.remember(
                sender,
                question,
                answer,
                {
                    "id": uuid4().hex,
                    "created_at": utc_now(),
                    "question": question,
                    "answer": answer,
                    "history": history,
                    "model": trace.get("model", self.settings.model),
                    "provider": trace.get("provider"),
                    "profile": trace.get("profile"),
                    "prompt_version": trace.get("prompt_version", PROMPT_VERSION),
                    "context_version": trace.get("context_version"),
                },
                state=getattr(self.assistant, "next_state", None),
            )
