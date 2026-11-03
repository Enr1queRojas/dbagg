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


class UnauthorizedNotices:
    """Who may receive the support notice; in memory so strangers' numbers never reach disk.

    Once a day per number, with a global cap so nobody can make the service send in bulk.
    """

    MAX_PER_HOUR = 30

    def __init__(self):
        self.notified = {}
        self.recent = []
        self.lock = threading.Lock()

    def claim(self, sender):
        now = time.monotonic()
        with self.lock:
            self.notified = {k: v for k, v in self.notified.items() if now - v < 86400}
            self.recent = [v for v in self.recent if now - v < 3600]
            if sender in self.notified or len(self.recent) >= self.MAX_PER_HOUR:
                return False
            self.notified[sender] = now
            self.recent.append(now)
            return True

    def release(self, sender):
        """A transient failure must not spend the day's notice; the hourly cap still counts."""
        with self.lock:
            self.notified.pop(sender, None)


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
