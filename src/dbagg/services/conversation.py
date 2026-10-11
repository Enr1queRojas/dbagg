"""Coordinate one authorized conversation independently of HTTP transport."""

import logging
import threading
from uuid import uuid4

import httpx

from dbagg.agent.prompts import PROMPT_VERSION
from dbagg.evaluation.store import FeedbackStore, utc_now
from dbagg.integrations.meta import meta_error_codes
from dbagg.paths import project_root
from dbagg.services.memory import ConversationMemory

logger = logging.getLogger("uvicorn.error")

CONTROL_COMMANDS = frozenset({"/diagnostico", "/reiniciar", "/buena", "/mala", "/ayuda"})


def command_name(question):
    return question.strip().split(maxsplit=1)[0].lower() if question.strip() else ""


class ConversationService:
    def __init__(self, settings, assistant, deliver, feedback_store=None):
        self.settings = settings
        self.assistant = assistant
        self.deliver = deliver
        self.memory = ConversationMemory()
        self.lock = threading.Lock()
        self.feedback = feedback_store
        if self.feedback is None and settings.feedback_enabled:
            self.feedback = FeedbackStore(
                settings.feedback_path or project_root() / "data" / "feedback.sqlite3"
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

    def process(self, sender, question):
        if not self.lock.acquire(blocking=False):
            logger.info("dbagg stage=ignored_busy")
            return
        try:
            save_turn = False
            answer_failed = False
            history = []
            try:
                command = command_name(question)
                if command == "/diagnostico":
                    answer = "dbagg: este mensaje llegó al agente local. Diagnóstico del webhook correcto."
                    logger.info("dbagg stage=diagnostic_ok")
                elif command == "/reiniciar":
                    self.memory.clear(sender)
                    answer = "dbagg: conversación reiniciada. ¿Qué quieres consultar?"
                elif command in {"/buena", "/mala"}:
                    answer = self._rate(sender, question, command)
                elif command == "/ayuda":
                    answer = (
                        "Pregunta por saldos o pagos. /reiniciar borra el contexto; "
                        "/buena o /mala motivo valoran la última respuesta si están habilitados."
                    )
                else:
                    history = self.memory.history(sender)
                    answer = (
                        self.assistant.answer(question, history=history)
                        if history
                        else self.assistant.answer(question)
                    )
                    save_turn = True
            except Exception as exc:
                answer_failed = True
                logger.warning("dbagg stage=answer_failed error_type=%s", type(exc).__name__)
                answer = "No pude completar la consulta. Revisa la conexión y la configuración del servicio."
            try:
                self.deliver(sender, answer)
                if answer_failed:
                    self.memory.invalidate_rating(sender)
                if save_turn:
                    trace = getattr(self.assistant, "last_trace", None)
                    trace = trace if isinstance(trace, dict) else {}
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
                            "model": self.settings.model,
                            "prompt_version": PROMPT_VERSION,
                            "context_version": trace.get("context_version"),
                        },
                    )
                logger.info("dbagg stage=send_ok")
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
        finally:
            self.lock.release()
