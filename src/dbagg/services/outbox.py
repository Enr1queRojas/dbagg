"""Outbox persistente y deduplicación de mensajes entrantes en SQLite."""

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dbagg.evaluation.store import utc_now


def _cutoff_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _cutoff_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class OutboxStore:
    """Almacén persistente de mensajes de salida y deduplicación de mensajes entrantes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._migrate()

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self):
        """Crea las tablas si no existen. Idempotente."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id         INTEGER PRIMARY KEY,
                    sender     TEXT NOT NULL,
                    body       TEXT NOT NULL,
                    status     TEXT NOT NULL DEFAULT 'pendiente',
                    wamid      TEXT,
                    attempts   INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_messages (
                    message_id TEXT PRIMARY KEY,
                    sender     TEXT NOT NULL,
                    seen_at    TEXT NOT NULL
                )
            """)

    def enqueue(self, sender: str, body: str) -> int:
        """Inserta una fila pendiente y devuelve su id."""
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO outbox (sender, body, status, attempts, created_at, updated_at) "
                "VALUES (?, ?, 'pendiente', 0, ?, ?)",
                (sender, body, now, now),
            )
            return cursor.lastrowid

    def mark_sent(self, row_id: int, wamid: str):
        """Marca la fila como enviada y guarda el wamid de Meta."""
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET status='enviado', wamid=?, updated_at=? WHERE id=?",
                (wamid, now, row_id),
            )

    def mark_failed(self, row_id: int):
        """Marca la fila como fallida."""
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET status='fallido', updated_at=? WHERE id=?",
                (now, row_id),
            )

    def increment_attempts(self, row_id: int):
        """Incrementa el contador de intentos y actualiza updated_at."""
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET attempts=attempts+1, updated_at=? WHERE id=?",
                (now, row_id),
            )

    def pending(self) -> list:
        """Devuelve filas pendientes ordenadas por id."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM outbox WHERE status='pendiente' ORDER BY id"
            ).fetchall()

    def purge(self, days: int) -> int:
        """Borra filas enviadas/fallidas más antiguas de `days` días. Devuelve el nº borrado."""
        cutoff = _cutoff_days(days)
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM outbox WHERE status IN ('enviado', 'fallido') AND created_at < ?",
                (cutoff,),
            ).rowcount
        return deleted


class PersistentMessageGate:
    """Deduplicación y rate-limit de mensajes entrantes respaldada por SQLite."""

    RATE_LIMIT_SECONDS = 10

    def __init__(self, store: OutboxStore):
        self.store = store

    def claim(self, message_id: str, sender: str, control: bool = False) -> bool:
        """
        Intenta reclamar un message_id. Devuelve True si el mensaje puede procesarse.

        - Deduplica: si ya fue visto, devuelve False.
        - Rate-limit (solo mensajes no-control): rechaza si el mismo sender envió
          otro mensaje no-control en los últimos 10 s.
        """
        now_iso = utc_now()
        # Usamos el timestamp ISO para comparar en SQLite; para el rate-limit necesitamos
        # también el instante en epoch para comparar con seen_at del último registro.
        now_epoch = time.time()
        cutoff_rate = (
            datetime.now(timezone.utc) - timedelta(seconds=self.RATE_LIMIT_SECONDS)
        ).isoformat()

        with self.store._connect() as conn:
            # 1. Deduplicación
            existing = conn.execute(
                "SELECT 1 FROM seen_messages WHERE message_id=?", (message_id,)
            ).fetchone()
            if existing is not None:
                return False

            # 2. Rate-limit (sólo para mensajes normales)
            if not control:
                last_row = conn.execute(
                    "SELECT seen_at FROM seen_messages WHERE sender=? ORDER BY seen_at DESC LIMIT 1",
                    (sender,),
                ).fetchone()
                if last_row is not None and last_row["seen_at"] >= cutoff_rate:
                    # Aún registramos el message_id para deduplicar futuros reintentos
                    conn.execute(
                        "INSERT OR IGNORE INTO seen_messages (message_id, sender, seen_at) VALUES (?, ?, ?)",
                        (message_id, sender, now_iso),
                    )
                    return False

            # 3. Registrar
            conn.execute(
                "INSERT OR IGNORE INTO seen_messages (message_id, sender, seen_at) VALUES (?, ?, ?)",
                (message_id, sender, now_iso),
            )
            return True

    def purge_old(self, hours: int = 24):
        """Borra registros de seen_messages más viejos que `hours` horas."""
        cutoff = _cutoff_hours(hours)
        with self.store._connect() as conn:
            conn.execute(
                "DELETE FROM seen_messages WHERE seen_at < ?", (cutoff,)
            )
