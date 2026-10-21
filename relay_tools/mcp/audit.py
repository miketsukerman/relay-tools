"""Append-only JSONL audit log for relay-mcp tool calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_LOG = Path("~/.local/state/relay-mcp/audit.jsonl")
AUDIT_LOG_ENV = "RELAY_MCP_AUDIT_LOG"


class AuditLog:
    """One JSON line per state-changing tool call."""

    def __init__(self, path: str | Path = DEFAULT_AUDIT_LOG) -> None:
        self.path = Path(path).expanduser()

    def record(
        self,
        *,
        board: str,
        tool: str,
        arguments: dict[str, Any],
        ok: bool,
        error: str | None = None,
        steps: list[str] | None = None,
        matching_boot_modes: list[str] | None = None,
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "board": board,
            "tool": tool,
            "arguments": arguments,
            "ok": ok,
            "error": error,
            "steps": steps or [],
            "matching_boot_modes": matching_boot_modes or [],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest entries first, with ``limit`` clamped to 1..200."""

        limit = max(1, min(int(limit), 200))
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    entries.append(parsed)
        return entries[-limit:][::-1]
