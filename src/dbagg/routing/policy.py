"""Conservative vetoes; uncertainty restores the full interpreter, never grants access."""

import re
import unicodedata


def normalize(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


def route_is_consistent(question, route):
    text = normalize(question)
    # The latest question may change domains. Never turn an explicit expense
    # request into another customer operation because of the preceding payment.
    # This is a veto only: the full interpreter resolves ambiguous/mixed requests.
    if route.domain == "clientes" and re.search(r"\b(gastos|gastamos|gastado)\b", text):
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
