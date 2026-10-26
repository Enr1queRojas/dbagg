"""Shared bounded-resource limits for the local pilot."""

MAX_ROWS = 50
MAX_CELL_CHARS = 300
MAX_RESULT_CHARS = 16000
MAX_QUESTION_CHARS = 1000
# Voice notes: WhatsApp Opus is ~16-32 kbit/s; 2 MB also bounds non-Opus audio.
MAX_AUDIO_BYTES = 2_000_000
