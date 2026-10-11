"""Conservative guards for explicit requests unsupported by the business tools."""

import re
import unicodedata

COMPARISON_CLARIFICATION = (
    "Para saber cuál de esos clientes tiene saldo hay que comparar sus saldos. "
    "Todavía no tengo una operación para comparar ese grupo; puedo consultar cada código "
    "por separado. Indica el código que quieres revisar."
)


def requests_candidate_comparison(question):
    """Recognize explicit 'which of these N' requests; this is not a general intent model."""
    text = "".join(
        c
        for c in unicodedata.normalize("NFKD", question.casefold())
        if not unicodedata.combining(c)
    )
    text = " ".join(text.split())
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
