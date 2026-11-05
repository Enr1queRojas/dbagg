"""Decides from the wording or the conversation whether a question is about a customer or supplier.

History is plain text, so an answer's own fixed wording carries the entity forward. Only the
identities an answer shows count; an amount or a table cell never names a party.
"""

import re

from dbagg.agent.intent import _plain, list_row_details
from dbagg.business.parties import AMBIGUOUS_MARK, CUSTOMER, SUPPLIER

SUPPLIER_NOUN = re.compile(r"\bproveedor(?:es)?\b")
CUSTOMER_NOUN = re.compile(r"\bclientes?\b")
SUPPLIER_VERB = re.compile(
    r"\b(?:les? debemos|se les? debe|les? debo|debo al?|les? adeudamos|les? adeudo|debemos al?"
    r"|les? quedamos a deber|les? compramos|les? compre|les? hemos comprado"
    r"|les? pagamos|les? pague|les? hemos pagado|nos vendio|nos vendieron|nos vende)\b"
)
CUSTOMER_VERB = re.compile(
    r"\b(?:nos debe|nos deben|me debe|me deben|nos adeuda|nos adeudan|me adeuda|me adeudan"
    r"|nos pago|nos pagaron|nos ha pagado|nos han pagado"
    r"|les? vendimos|les? vendi|les? fiamos)\b"
)
SUPPLIER_HEADING = re.compile(r"^Proveedor (.+) \(código ([^()\n]+)\)$", flags=re.MULTILINE)
SUPPLIER_LIST = re.compile(
    r"^(?:Encontré varios proveedores:|Proveedores a los que|Saldo actual de cada proveedor que"
    r"|Hay \d+ proveedor(?:es)? que)",
    flags=re.MULTILINE,
)
CUSTOMER_LIST = re.compile(r"^Clientes con mayor saldo", flags=re.MULTILINE)
CUSTOMER_PROSE = re.compile(
    r"^(?:(?:Estado de cuenta|Saldo actual|Total de pagos activos|Pagos activos|Pagos de la última fecha registrada"
    r"|Notas de crédito pendientes|Hay pagos activos|Hay pagos"
    r"|No encontré pagos activos con fecha válida) (?:de|para) )?(.+?) \(código ([^()\n]+)\)",
    flags=re.MULTILINE,
)
OTHER_ENTITY = re.compile(
    r"^No encontré a (.+) como (?:cliente|proveedor)\. .+ sí aparece como (cliente|proveedor)\.$"
)
ASKED_TERM = re.compile(r"^Encontré «(.+)» " + re.escape(AMBIGUOUS_MARK), flags=re.MULTILINE)
BARE_ENTITY = re.compile(
    r"(?:(?:y|si|el|la|al|del|de|como|es|son|a|un|una|quiero|consulta|ver)\s+)*"
    r"(?:proveedor|cliente)"
)
ROW_ENTITIES = {"cliente": CUSTOMER, "proveedor": SUPPLIER}


def _without_party(question, party):
    text = _plain(question)
    if isinstance(party, str) and party.strip():
        text = text.replace(_plain(party.strip()), " ")
    return text


def explicit_entity(question, party=None):
    """Only wording that decides counts, outside the name itself; «cuánto debe X» could be either."""
    text = _without_party(question, party)
    supplier = bool(SUPPLIER_NOUN.search(text) or SUPPLIER_VERB.search(text))
    customer = bool(CUSTOMER_NOUN.search(text) or CUSTOMER_VERB.search(text))
    if supplier == customer:
        return None
    return SUPPLIER if supplier else CUSTOMER


def names_entity(question, party=None):
    """«Como cliente» names the entity; «nos debe» can also be asked of a supplier in our favor."""
    text = _without_party(question, party)
    return bool(SUPPLIER_NOUN.search(text) or CUSTOMER_NOUN.search(text))


def entity_in_answer(text):
    """Entity of an answer with fixed wording; anything else, like a table, has none."""
    if SUPPLIER_HEADING.search(text) or SUPPLIER_LIST.search(text):
        return SUPPLIER
    if CUSTOMER_LIST.search(text) or "(código " in text:
        return CUSTOMER
    return None


def _answer_parties(text):
    """(entity, code, name) of every identity an answer shows."""
    entity = entity_in_answer(text)
    parties = [
        (ROW_ENTITIES.get(mark) or entity, code, name)
        for mark, name, code in list_row_details(text)
    ]
    if entity == SUPPLIER:
        parties += [(SUPPLIER, code, name) for name, code in SUPPLIER_HEADING.findall(text)]
    elif entity == CUSTOMER:
        for line in text.splitlines():
            if not list_row_details(line):
                parties += [(CUSTOMER, code, name) for name, code in CUSTOMER_PROSE.findall(line)]
    other = OTHER_ENTITY.match(text)
    if other:
        parties.append((ROW_ENTITIES[other[2]], None, other[1]))
    return [party for party in parties if party[0]]


def _assistant_messages(history):
    for message in reversed(history or []):
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, str):
            yield content


def conversation_entity(party, history):
    """Entity of the latest answer that shows exactly this code or full name."""
    if not isinstance(party, str) or not party.strip():
        return None
    term = _plain(party.strip())
    for content in _assistant_messages(history):
        if AMBIGUOUS_MARK in content:
            continue
        entities = {
            entity
            for entity, code, name in _answer_parties(content)
            if term in (_plain(code or ""), _plain(name))
        }
        if len(entities) == 1:
            return entities.pop()
        if entities:
            return None
    return None


def listed_row(history, position, entity=None):
    """(entity, code) of a row of the latest list; with an entity, counts only that entity's rows."""
    for content in _assistant_messages(history):
        details = list_row_details(content)
        if len(details) < 2:
            continue
        default = entity_in_answer(content)
        rows = [(ROW_ENTITIES.get(mark) or default, code) for mark, _, code in details]
        if entity:
            rows = [row for row in rows if row[0] == entity]
        return rows[position - 1] if 1 <= position <= len(rows) else None
    return None


def mixed_list_choice(question, history, party):
    """«El proveedor» answers «¿cliente o proveedor?»: (entity, code if only one row, asked term).

    Applies only to a bare reply or to the same party; any other name is a change of subject.
    """
    latest = next(_assistant_messages(history), "")
    asked = ASKED_TERM.search(latest)
    entity = explicit_entity(question, party)
    if not asked or not entity:
        return None
    details = list_row_details(latest)
    same_party = isinstance(party, str) and _plain(party.strip()) in {
        _plain(asked[1]),
        *(_plain(name) for _, name, _ in details),
        *(_plain(code) for _, _, code in details),
    }
    if not same_party and not BARE_ENTITY.fullmatch(_plain(question).strip(" ¿?¡!.,")):
        return None
    codes = [code for mark, _, code in details if ROW_ENTITIES.get(mark) == entity]
    return entity, codes[0] if len(codes) == 1 else None, asked[1]


def named_row(question, history):
    """(entity, code) of the only row of the open list that the question names in full."""
    text = f" {_plain(question).strip(' ¿?¡!.,')} "
    for content in _assistant_messages(history):
        details = list_row_details(content)
        if len(details) < 2:
            continue
        default = entity_in_answer(content)
        rows = [
            (ROW_ENTITIES.get(mark) or default, code)
            for mark, name, code in details
            if f" {_plain(name)} " in text or f" {_plain(code)} " in text
        ]
        return rows[0] if len(rows) == 1 else None
    return None
