"""On-disk boot logs and the per-board boot index."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from relay_tools.board_config import ConsoleRetentionConfig

BOOT_FILE_WIDTH = 6
_TS_RE = re.compile(r"^(?P<ts>[0-9T:.\-]+Z)\t(?P<text>.*)$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


@dataclass
class BootRecord:
    """One entry in ``boots.jsonl``."""

    id: int
    started_at: str
    ended_at: str | None = None
    trigger: str = "service_start"
    reason: str | None = None
    actor: str | None = None
    audit_id: str | None = None
    lines: int = 0
    bytes: int = 0
    result: str = "in_progress"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogLine:
    timestamp: datetime
    text: str
    number: int = field(default=0)


class BootStore:
    """Boot log files and the boot index for one board.

    Thread-safe; the capture thread writes while HTTP handlers read.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        retention: ConsoleRetentionConfig | None = None,
        result_fn: Callable[[list[LogLine], BootRecord], str] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.boots_dir = self.directory / "boots"
        self.index_path = self.directory / "boots.jsonl"
        self.retention = retention or ConsoleRetentionConfig()
        self.result_fn = result_fn
        self._lock = threading.RLock()
        self._records: list[BootRecord] = []
        self._handle = None
        self.boots_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    # ------------------------------------------------------------------
    # Index persistence
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        if not self.index_path.exists():
            return
        with self.index_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict) and "id" in data:
                    known = {f for f in BootRecord.__dataclass_fields__}
                    self._records.append(
                        BootRecord(
                            **{k: v for k, v in data.items() if k in known}
                        )
                    )
        # A boot left open by a previous service run is over.
        for record in self._records:
            if record.result == "in_progress":
                record.result = "incomplete"
                if record.ended_at is None:
                    record.ended_at = format_timestamp(utc_now())
        self._save_index()

    def _save_index(self) -> None:
        tmp_path = self.index_path.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for record in self._records:
                handle.write(json.dumps(record.to_json(), sort_keys=True) + "\n")
        tmp_path.replace(self.index_path)

    # ------------------------------------------------------------------
    # Boot lifecycle
    # ------------------------------------------------------------------

    @property
    def current(self) -> BootRecord | None:
        with self._lock:
            if self._records and self._records[-1].ended_at is None:
                return self._records[-1]
            return None

    def _boot_path(self, boot_id: int) -> Path:
        return self.boots_dir / f"{boot_id:0{BOOT_FILE_WIDTH}d}.log"

    def new_boot(
        self,
        *,
        trigger: str,
        reason: str | None = None,
        actor: str | None = None,
        audit_id: str | None = None,
    ) -> BootRecord:
        with self._lock:
            self._finalize_current()
            next_id = (self._records[-1].id + 1) if self._records else 1
            record = BootRecord(
                id=next_id,
                started_at=format_timestamp(utc_now()),
                trigger=trigger,
                reason=reason,
                actor=actor,
                audit_id=audit_id,
            )
            self._records.append(record)
            self._handle = self._boot_path(next_id).open("a", encoding="utf-8")
            self._save_index()
            self._apply_retention()
            return record

    def _finalize_current(self) -> None:
        current = self.current
        if current is None:
            return
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        current.ended_at = format_timestamp(utc_now())
        if self.result_fn is not None:
            try:
                current.result = self.result_fn(
                    self.read_log(current.id), current
                )
            except Exception:  # noqa: BLE001 - classification must not crash
                current.result = "incomplete"
        else:
            current.result = "incomplete"
        self._save_index()

    def close(self) -> None:
        with self._lock:
            self._finalize_current()

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------

    def append_line(self, text: str, *, moment: datetime | None = None) -> None:
        with self._lock:
            current = self.current
            if current is None or self._handle is None:
                return
            stamp = format_timestamp(moment or utc_now())
            entry = f"{stamp}\t{text}\n"
            self._handle.write(entry)
            self._handle.flush()
            current.lines += 1
            current.bytes += len(entry.encode("utf-8"))

    def read_log(self, boot_id: int) -> list[LogLine]:
        path = self._boot_path(boot_id)
        if not path.exists():
            return []
        lines: list[LogLine] = []
        with path.open(encoding="utf-8") as handle:
            for number, raw in enumerate(handle, start=1):
                match = _TS_RE.match(raw.rstrip("\n"))
                if not match:
                    continue
                lines.append(
                    LogLine(
                        timestamp=parse_timestamp(match.group("ts")),
                        text=match.group("text"),
                        number=number,
                    )
                )
        return lines

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def records(self, limit: int | None = None) -> list[BootRecord]:
        """Newest boots first."""

        with self._lock:
            newest_first = list(reversed(self._records))
            if limit is not None:
                newest_first = newest_first[:limit]
            return newest_first

    def find(self, boot: str) -> BootRecord | None:
        with self._lock:
            if boot == "last":
                return self._records[-1] if self._records else None
            try:
                boot_id = int(boot)
            except ValueError:
                return None
            for record in self._records:
                if record.id == boot_id:
                    return record
            return None

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def _apply_retention(self) -> None:
        max_bytes = self.retention.max_megabytes * 1024 * 1024
        while len(self._records) > 1:
            over_count = len(self._records) > self.retention.max_boots
            total_bytes = sum(record.bytes for record in self._records)
            if not over_count and total_bytes <= max_bytes:
                break
            oldest = self._records.pop(0)
            try:
                self._boot_path(oldest.id).unlink()
            except FileNotFoundError:
                pass
        self._save_index()
