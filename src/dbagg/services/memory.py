"""Volatile per-sender history and local message admission."""

import threading
import time
from dbagg.limits import MAX_QUESTION_CHARS


class ConversationMemory:
    """Bounded volatile memory, isolated by authorized sender; no SQL results stored."""

    def __init__(self):
        self.sessions = {}
        self.interactions = {}

    def history(self, sender):
        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if now - v[0] < 1800}
        self.interactions = {k: v for k, v in self.interactions.items() if k in self.sessions}
        return [dict(message) for message in self.sessions.get(sender, (now, []))[1]]

    def remember(self, sender, question, answer, interaction=None):
        history = self.history(sender)
        history.extend(
            [
                {"role": "user", "content": question[:MAX_QUESTION_CHARS]},
                {"role": "assistant", "content": answer[:3500]},
            ]
        )
        self.sessions[sender] = (time.monotonic(), history[-8:])
        if interaction is not None:
            self.interactions[sender] = interaction
        else:
            self.interactions.pop(sender, None)

    def last_interaction(self, sender):
        self.history(sender)
        return self.interactions.get(sender)

    def invalidate_rating(self, sender):
        """A delivered error must not make the previous answer the target of a new vote."""
        self.interactions.pop(sender, None)

    def clear(self, sender):
        self.sessions.pop(sender, None)
        self.interactions.pop(sender, None)


class MessageGate:
    """In-memory deduplication/rate limiting for a single-process local pilot."""

    def __init__(self):
        self.seen = {}
        self.last = {}
        self.lock = threading.Lock()

    def claim(self, message_id, sender, control=False):
        now = time.monotonic()
        with self.lock:
            self.seen = {k: v for k, v in self.seen.items() if now - v < 86400}
            if message_id in self.seen:
                return False
            if len(self.seen) >= 10000:
                return False
            self.seen[message_id] = now
            if control:
                return True
            if now - self.last.get(sender, -100) < 10:
                return False
            self.last[sender] = now
            return True
