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
    "No hay clientes con ese nombre o código. Quizá no es un cliente: si es un apartado o "
    "sucursal de gastos, un producto o un almacén, explora ese dominio con describir_tablas y "
    "consultar_sql (por ejemplo CODIGO del catálogo de gastos). Si la pregunta sí es sobre un "
    "cliente, usa pedir_aclaracion(cliente)."
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
