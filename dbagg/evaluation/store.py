"""Local human feedback. Ratings are observations, not verified training labels."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_training_case(case):
    if (
        not isinstance(case, dict)
        or case.get("anonymized") is not True
        or case.get("verified") is not True
    ):
        raise ValueError(
            "El caso requiere anonymized=true y verified=true, confirmados por el revisor."
        )
    messages = case.get("messages")
    if not isinstance(messages, list) or not 2 <= len(messages) <= 12 or len(messages) % 2:
        raise ValueError("El caso requiere pares usuario/respuesta, máximo seis turnos.")
    for index, message in enumerate(messages):
        expected = "user" if index % 2 == 0 else "assistant"
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] != expected
            or not isinstance(message["content"], str)
            or not message["content"].strip()
        ):
            raise ValueError(
                "Mensajes inválidos; se esperan roles user y assistant alternados con contenido."
            )
    if len(json.dumps(messages, ensure_ascii=False)) > 32000:
        raise ValueError("Caso demasiado grande.")
    return {"messages": messages}


class FeedbackStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                question TEXT NOT NULL, answer TEXT NOT NULL, history_json TEXT NOT NULL,
                model TEXT NOT NULL, prompt_version TEXT NOT NULL, context_version TEXT,
                rating TEXT NOT NULL CHECK(rating IN ('good','bad')), reason TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending', reviewer TEXT,
                reviewed_at TEXT, prepared_case_json TEXT
            )""")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record(self, interaction, rating, reason=""):
        if rating not in {"good", "bad"}:
            raise ValueError("La valoración debe ser good o bad.")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO feedback
                (id, created_at, question, answer, history_json, model, prompt_version,
                 context_version, rating, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET rating=excluded.rating, reason=excluded.reason,
                    review_status='pending', reviewer=NULL, reviewed_at=NULL, prepared_case_json=NULL""",
                (
                    interaction["id"],
                    interaction["created_at"],
                    interaction["question"],
                    interaction["answer"],
                    json.dumps(interaction.get("history", []), ensure_ascii=False),
                    interaction["model"],
                    interaction["prompt_version"],
                    interaction.get("context_version"),
                    rating,
                    reason[:1000],
                ),
            )
        return interaction["id"]

    def summary(self):
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT rating, review_status, COUNT(*) AS count FROM feedback GROUP BY rating, review_status"
                )
            ]

    def list_pending(self):
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id, created_at, rating, model, prompt_version FROM feedback "
                    "WHERE review_status='pending' ORDER BY created_at"
                )
            ]

    def get(self, interaction_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM feedback WHERE id=?", (interaction_id,)
            ).fetchone()
        if row is None:
            raise ValueError("No existe esa evaluación.")
        return dict(row)

    def review(self, interaction_id, decision, reviewer, case=None):
        if decision not in {"approved", "rejected"} or not reviewer.strip():
            raise ValueError("Indica approved/rejected y un revisor.")
        prepared = validate_training_case(case) if decision == "approved" else None
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE feedback SET review_status=?, reviewer=?, reviewed_at=?,
                prepared_case_json=? WHERE id=?""",
                (
                    decision,
                    reviewer,
                    utc_now(),
                    json.dumps(prepared, ensure_ascii=False) if prepared else None,
                    interaction_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("No existe esa evaluación.")

    def export(self, output):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT prepared_case_json FROM feedback "
                "WHERE review_status='approved' ORDER BY created_at"
            ).fetchall()
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Never silently replace a reviewed dataset.
        with output.open("x", encoding="utf-8") as stream:
            for row in rows:
                stream.write(row["prepared_case_json"] + "\n")
        return len(rows)
