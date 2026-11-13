"""Conservative vetoes; uncertainty restores the full interpreter, never grants access."""

import re
import unicodedata

from dbagg.agent.entity import explicit_entity


def normalize(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


SHORT_REPLY = re.compile(r"\W*(si|no|ok|okay|gracias|va|sale|claro|listo)\W*")
BANK_WORD = re.compile(r"\bbancos?\b")
ACCOUNT_WORD = re.compile(r"\b(bancos?|efectivos?|cuentas?|caja)\b")


def mentions_account_words(question):
    """Cheap check before looking up account names: only these words can pair with one."""
    return bool(ACCOUNT_WORD.search(normalize(question)))


def pre_route(question, history=None, state=None, bank_names=(), bank_domain=False):
    """«legacy» before any routing call when the outcome is already known; else None.

    Short replies keep a pending operation that only the full interpreter holds, and bank
    accounts live only in the full interpreter. This never grants access: it only skips a
    classification whose result would be legacy anyway. ``route_is_consistent`` remains as
    the safety net after a classification.
    """
    text = normalize(question)
    if SHORT_REPLY.fullmatch(text):
        return "legacy"
    if bank_domain:
        # Banks have their own modular routes; the router decides among them.
        return None
    if BANK_WORD.search(text):
        return "legacy"
    if ACCOUNT_WORD.search(text):
        for name in bank_names or ():
            term = normalize(str(name or "")).strip()
            if len(term) >= 3 and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text):
                return "legacy"
    return None


STATEMENT_WORDS = re.compile(r"\b(reportes?|estados? de cuenta)\b")


def _latest_answer(history):
    for message in reversed(history or []):
        if message.get("role") == "assistant" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def route_is_consistent(question, route, history=None):
    text = normalize(question)
    # «Reporte» or «estado de cuenta» of a person is estado_cuenta, also while answering the
    # dates the service asked for it; a supplier's movements are only asked as movements.
    if (
        route.operation == "movimientos"
        and not re.search(r"\bmovimientos?\b", text)
        and (
            STATEMENT_WORDS.search(text)
            or STATEMENT_WORDS.search(normalize(_latest_answer(history)))
        )
    ):
        return False
    # Bank tools are available only in the full interpreter in this pilot.
    # Explicit bank references must not be trapped in another domain's tools.
    if route.domain != "bancos" and re.search(r"\bbancos?\b", text):
        return False
    # The latest question may change domains. Never turn an explicit expense
    # request into another customer operation because of the preceding payment.
    # This is a veto only: the full interpreter resolves ambiguous/mixed requests.
    if route.domain in {"clientes", "proveedores"} and re.search(
        r"\b(gastos|gastamos|gastado)\b", text
    ):
        return False
    # Product routes are global: a question about one customer or supplier is not theirs.
    if route.domain == "productos" and explicit_entity(question) is not None:
        return False
    # A supplier route never answers a question that says it is about a customer.
    if route.domain == "proveedores" and explicit_entity(question) == "cliente":
        return False
    if re.fullmatch(r"\W*(si|no|ok|gracias)\W*", text):
        return False
    if route.operation == "ultimo_pago" and not re.search(r"\b(ultim[oa]|reciente)\b", text):
        return False
    return True


def needs_expense_year(question, history, state=None):
    text = normalize(question)
    months = r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b"
    if not re.search(months, text) or re.search(r"\b(?:19|20)\d{2}\b", text):
        return False
    # An unrelated year elsewhere in history is not permission to reuse it.
    # A compatible confirmed range can supply a year for an explicit continuation.
    continuation = re.match(r"^[\s¿?¡!]*(y\b|para ese\b|para esa\b)", text)
    confirmed_range = state is not None and state.period == "rango" and state.start and state.end
    if continuation and confirmed_range:
        return state.start[:4] != state.end[:4]
    return True
