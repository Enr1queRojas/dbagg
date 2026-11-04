"""Conservative guards for explicit requests unsupported by the business tools."""

import re
import unicodedata

COMPARISON_CLARIFICATION = (
    "Para saber cuál de esos clientes tiene saldo hay que comparar sus saldos. "
    "Todavía no tengo una operación para comparar ese grupo; puedo consultar cada código "
    "por separado. Indica el código que quieres revisar."
)


RANKING_HINT = (
    "ranking_deuda es el ranking global de quienes más deben y la pregunta no lo pide. "
    "Si menciona un cliente o varios con el mismo nombre, usa saldo para uno o "
    "saldos_coincidencias para listar el saldo de cada coincidencia, con customer tal como "
    "aparece en la conversación. Si no está claro, usa pedir_aclaracion."
)


NOT_A_CUSTOMER_HINT = (
    "No hay clientes ni proveedores con ese nombre o código. Quizá no lo es: si es un apartado o "
    "sucursal de gastos, un producto o un almacén, explora ese dominio con describir_tablas y "
    "consultar_sql (por ejemplo CODIGO del catálogo de gastos). Si la pregunta sí es sobre un "
    "cliente, usa pedir_aclaracion(cliente)."
)


PERIOD_HINT = (
    "La pregunta ya indica el mes y el año: el período no es ambiguo. Usa period=rango con start "
    "y end en formato YYYY-MM-DD; si solo nombra el mes, va del día 1 al último."
)

ORDINALS = {
    "primer": 1,
    "segund": 2,
    "tercer": 3,
    "cuart": 4,
    "quint": 5,
    "sext": 6,
    "septim": 7,
    "octav": 8,
    "noven": 9,
}
MONTHS = (
    r"\b(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre"
    r"|noviembre|diciembre)\b"
)


def _plain(question):
    text = "".join(
        c
        for c in unicodedata.normalize("NFKD", question.casefold())
        if not unicodedata.combining(c)
    )
    return " ".join(text.split())


def requests_ranking(question):
    """A global debt ranking must be asked for explicitly; a named customer is not enough."""
    return bool(
        re.search(
            r"\b(?:mas|mayor(?:es)?|top|ranking|principales|primeros|deudores|morosos)\b",
            _plain(question),
        )
    )


def requests_candidate_comparison(question):
    """Recognize explicit 'which of these N' requests; this is not a general intent model."""
    text = _plain(question)
    if not re.search(r"\b(?:saldo|saldos|debe|deben|deuda|deudas)\b", text):
        return False
    return bool(
        re.search(
            r"\b(?:cual(?:es)?|quien(?:es)?)\b.*\b(?:de|entre)\s+"
            r"(?:(?:los|las)\s+(?:dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|\d+)"
            r"|esos|esas|estos|estas)\b",
            text,
        )
    )


def listed_rows(answer):
    """(entity mark, code) of a party list's rows, in order; movements carry « · » and never match."""
    rows = re.findall(
        r"^\s*(?:\d+\.|[•*])\s[^·\n]*\((?:(cliente|proveedor),\s*)?(?:c[oó]digo\s+)?([^()\n]+)\)"
        r"(?::[^·\n]*)?(?:\s·\sinactivo)?\s*$",
        answer,
        flags=re.MULTILINE,
    )
    return [(mark or None, code.strip()) for mark, code in rows]


def listed_codes(answer):
    """Codes of the rows of a party list, in order."""
    return [code for _, code in listed_rows(answer)]


def list_position(question):
    """«El segundo» points at a row; «su primer pago» or «el segundo pago» do not."""
    match = re.search(
        r"\b(?:(?:el|la|al|del)\s+(primer|segund|tercer|cuart|quint|sext|septim|octav|noven)[oa]"
        r"|(?:(?:el|la)\s+(?:numero\s+)?|numero\s+)(\d{1,2}))\b"
        r"(?=\s*(?:$|[,.?!]|cliente\b|de\s+la\s+lista\b|por\s+favor\b))",
        _plain(question),
    )
    if not match:
        return None
    return ORDINALS[match[1]] if match[1] else int(match[2])


def names_month_and_year(question):
    """A named month with its year leaves nothing to ask about the period."""
    text = _plain(question)
    return bool(re.search(MONTHS, text) and re.search(r"\b20\d{2}\b", text))
