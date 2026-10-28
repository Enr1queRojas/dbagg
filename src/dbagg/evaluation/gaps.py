"""Local log of questions the agent could not answer with a verified operation.

Rows can contain private names: the database lives in data/ (ignored by Git). Only
reviewed, anonymized cases in the intent-routing format can be exported.
"""

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from dbagg.evaluation.store import utc_now

CATEGORIES = frozenset({"fuera_alcance", "sin_evidencia", "limite_consultas", "exploratoria"})
DECISIONS = frozenset({"nuevo_caso", "resuelto", "descartado"})
CASE_KEYS = {"id", "question", "history", "expected_tool", "expected_arguments", "criteria"}


def normalized(question):
    """Grouping key: lowercase, no accents or punctuation, single spaces."""
    text = "".join(
        c
        for c in unicodedata.normalize("NFKD", question.casefold())
        if not unicodedata.combining(c)
    )
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def validate_gap_case(case):
    """A reviewed case must be synthetic and follow evals/cases/intent-routing.json."""
    if not isinstance(case, dict) or case.get("anonymized") is not True:
        raise ValueError("El caso requiere anonymized=true, confirmado por el revisor.")
    prepared = {key: case.get(key) for key in CASE_KEYS}
    if (
        not isinstance(prepared["id"], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,60}", prepared["id"])
        or not isinstance(prepared["question"], str)
        or not prepared["question"].strip()
        or not isinstance(prepared["history"], list)
        or prepared["expected_tool"]
        not in {"consultar_negocio", "pedir_aclaracion", "describir_tablas", "consultar_sql"}
        or not isinstance(prepared["expected_arguments"], dict)
        or not isinstance(prepared["criteria"], str)
        or not prepared["criteria"].strip()
    ):
        raise ValueError(
            "Caso inválido: requiere id (minúsculas y guiones), question, history (lista), "
            "expected_tool, expected_arguments (objeto) y criteria."
        )
    if len(json.dumps(prepared, ensure_ascii=False)) > 8000:
        raise ValueError("Caso demasiado grande.")
    return prepared


class GapStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS gaps (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, category TEXT NOT NULL,
                question TEXT NOT NULL, normalized TEXT NOT NULL, input TEXT NOT NULL,
                operation TEXT, sources_json TEXT NOT NULL, model TEXT,
                prompt_version TEXT, context_version TEXT, catalog_version TEXT,
                review_status TEXT NOT NULL DEFAULT 'pending', reviewer TEXT,
                reviewed_at TEXT, note TEXT, prepared_case_json TEXT
            )""")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record(self, question, trace, voice=False):
        category = trace.get("gap")
        if category not in CATEGORIES:
            raise ValueError("Categoría de pregunta sin respuesta desconocida.")
        gap_id = uuid4().hex
        key = normalized(question)[:1000]
        with self._connect() as connection:
            # A question discarded once stays discarded, so repeats never return to review.
            discarded = connection.execute(
                "SELECT 1 FROM gaps WHERE normalized=? AND review_status='descartado' LIMIT 1",
                (key,),
            ).fetchone()
            connection.execute(
                """INSERT INTO gaps (id, created_at, category, question, normalized, input,
                operation, sources_json, model, prompt_version, context_version,
                catalog_version, review_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    gap_id,
                    utc_now(),
                    category,
                    question[:1000],
                    key,
                    "voice" if voice else "text",
                    trace.get("operation"),
                    json.dumps(list(trace.get("sources") or []), ensure_ascii=False),
                    trace.get("model"),
                    trace.get("prompt_version"),
                    trace.get("context_version"),
                    trace.get("catalog_version"),
                    "descartado" if discarded else "pending",
                ),
            )
        return gap_id

    def summary(self):
        """Counts only; never question text."""
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT category, review_status, COUNT(*) AS count FROM gaps "
                    "GROUP BY category, review_status ORDER BY category, review_status"
                )
            ]

    def grouped(self, category=None, limit=50):
        """Pending questions grouped by normalized text, most frequent first (private)."""
        where = "WHERE review_status='pending'" + (" AND category=?" if category else "")
        params = (category,) if category else ()
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    f"""SELECT MIN(id) AS id, category, COUNT(*) AS count,
                    MAX(created_at) AS last_seen, MAX(question) AS example,
                    GROUP_CONCAT(DISTINCT sources_json) AS sources
                    FROM gaps {where} GROUP BY category, normalized
                    ORDER BY count DESC, last_seen DESC LIMIT ?""",
                    params + (limit,),
                )
            ]

    def review(self, gap_id, decision, reviewer, note="", case=None):
        """Review every pending row with the same question text and category."""
        if decision not in DECISIONS or not reviewer.strip():
            raise ValueError("Indica nuevo_caso, resuelto o descartado y un revisor.")
        prepared = validate_gap_case(case) if decision == "nuevo_caso" else None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT category, normalized FROM gaps WHERE id=?", (gap_id,)
            ).fetchone()
            if row is None:
                raise ValueError("No existe ese registro.")
            cursor = connection.execute(
                """UPDATE gaps SET review_status=?, reviewer=?, reviewed_at=?, note=?,
                prepared_case_json=? WHERE category=? AND normalized=?
                AND review_status='pending'""",
                (
                    decision,
                    reviewer,
                    utc_now(),
                    note[:1000],
                    json.dumps(prepared, ensure_ascii=False) if prepared else None,
                    row["category"],
                    row["normalized"],
                ),
            )
            return cursor.rowcount

    def export(self, output):
        """Write approved synthetic cases as a JSON file shaped like evals/cases."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT prepared_case_json FROM gaps WHERE review_status='nuevo_caso' "
                "AND prepared_case_json IS NOT NULL ORDER BY prepared_case_json"
            ).fetchall()
        cases = [json.loads(row["prepared_case_json"]) for row in rows]
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        dataset = {
            "dataset_version": "1",
            "data_kind": "synthetic",
            "execution": "Casos revisados desde preguntas sin respuesta; verificar antes de "
            "añadir a evals/cases.",
            "cases": cases,
        }
        # Never silently replace a reviewed dataset.
        with output.open("x", encoding="utf-8") as stream:
            json.dump(dataset, stream, ensure_ascii=False, indent=2)
        return len(cases)
