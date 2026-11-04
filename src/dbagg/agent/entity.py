"""Decides from the wording or the conversation whether a question is about a customer or supplier.

History is plain text, so an answer's own wording carries the entity forward.
"""

import re

from dbagg.agent.intent import _plain, listed_rows
from dbagg.business.parties import AMBIGUOUS_MARK, CUSTOMER, SUPPLIER

SUPPLIER_WORDS = re.compile(
    r"\b(?:proveedor(?:es)?|les? debemos|les? debo|les? adeudamos|debemos a"
    r"|les? compramos|les? hemos comprado|les? pagamos|les? hemos pagado|nos vendio)\b"
)
CUSTOMER_WORDS = re.compile(
    r"\b(?:clientes?|nos debe|nos deben|me debe|me deben|nos pago|nos pagaron|les? vendimos)\b"
)
SUPPLIER_ANSWER = re.compile(
    r"^Proveedor |^Proveedores a los que|varios proveedores:|cada proveedor que coincide"
    r"|proveedor que coincide|proveedores que coinciden|^No encontré proveedores"
    r"|^No encontré un proveedor",
    flags=re.MULTILINE,
)
ROW_ENTITIES = {"cliente": CUSTOMER, "proveedor": SUPPLIER}


def explicit_entity(question):
    """Only wording that decides counts; «cuánto debe X» could be either."""
    text = _plain(question)
    supplier, customer = bool(SUPPLIER_WORDS.search(text)), bool(CUSTOMER_WORDS.search(text))
    if supplier == customer:
        return None
    return SUPPLIER if supplier else CUSTOMER


def entity_in_answer(text):
    """Supplier answers use fixed wording; a customer named «Proveedora…» does not match it."""
    return SUPPLIER if SUPPLIER_ANSWER.search(text) else CUSTOMER


def _assistant_messages(history):
    for message in reversed(history or []):
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, str):
            yield content


def conversation_entity(party, history):
    if not isinstance(party, str) or not party.strip():
        return None
    pattern = r"(?<!\w)" + re.escape(_plain(party.strip())) + r"(?!\w)"
    for content in _assistant_messages(history):
        if AMBIGUOUS_MARK in content:
            continue
        if re.search(pattern, _plain(content)):
            return entity_in_answer(content)
    return None


def listed_row(history, position):
    """Entity and code of a row of the latest list; rows of a mixed list name their own."""
    for content in _assistant_messages(history):
        rows = listed_rows(content)
        if len(rows) >= 2:
            if not 1 <= position <= len(rows):
                return None
            mark, code = rows[position - 1]
            return ROW_ENTITIES.get(mark) or entity_in_answer(content), code
    return None


def mixed_list_choice(question, history):
    """«El proveedor» answers «¿cliente o proveedor?»: (entity, code if only one row is left)."""
    entity = explicit_entity(question)
    latest = next(_assistant_messages(history), "")
    if not entity or AMBIGUOUS_MARK not in latest:
        return None
    codes = [code for mark, code in listed_rows(latest) if ROW_ENTITIES.get(mark) == entity]
    return entity, codes[0] if len(codes) == 1 else None
