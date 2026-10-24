"""Cleaning of raw serial console data before it is stored or shown."""

from __future__ import annotations

import re
from typing import Iterable

MAX_LINE_LENGTH = 2000
TRUNCATION_SUFFIX = "…[truncated]"
REDACTED = "[REDACTED]"

# CSI/OSC/single-character ANSI escape sequences.
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\^_])"
)
# Every C0/C1 control character except tab (ESC is gone by then).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")
# Same, but newlines survive (used for stream taps).
_CONTROL_KEEP_NL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class Redactor:
    """Applies redaction regexes plus dynamically added literal secrets."""

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        self._patterns = [re.compile(pattern) for pattern in patterns]

    def add_literal(self, secret: str) -> None:
        """Redact a literal value (e.g. a console password) from now on."""

        if secret:
            self._patterns.append(re.compile(re.escape(secret)))

    def apply(self, text: str) -> str:
        for pattern in self._patterns:
            text = pattern.sub(REDACTED, text)
        return text


def sanitize_line(raw: bytes, redactor: Redactor | None = None) -> str:
    """Decode, strip escapes/control characters, truncate and redact."""

    text = raw.decode("utf-8", errors="replace")
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    if len(text) > MAX_LINE_LENGTH:
        text = text[:MAX_LINE_LENGTH] + TRUNCATION_SUFFIX
    if redactor is not None:
        text = redactor.apply(text)
    return text


def sanitize_chunk(raw: bytes, redactor: Redactor | None = None) -> str:
    """Like :func:`sanitize_line` but keeps newlines, for stream taps."""

    text = raw.decode("utf-8", errors="replace")
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_KEEP_NL_RE.sub("", text)
    if redactor is not None:
        text = redactor.apply(text)
    return text
