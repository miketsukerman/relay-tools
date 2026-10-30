"""Split a raw MJPEG byte stream into individual JPEG frames.

Pure logic, no I/O: feed arbitrary chunks, get back complete frames.
"""

from __future__ import annotations

SOI = b"\xff\xd8"  # JPEG start-of-image marker
EOI = b"\xff\xd9"  # JPEG end-of-image marker


class MjpegSplitter:
    """Incremental splitter for a concatenated-JPEG (MJPEG) stream.

    ``feed`` returns every complete frame (SOI through EOI inclusive) found
    so far and buffers partial data. Junk before an SOI is discarded, and
    markers split across chunk boundaries are handled.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._in_frame = False
        self._scan = 0  # resume position for the EOI search

    def feed(self, chunk: bytes) -> "list[bytes]":
        self._buf += chunk
        frames: list[bytes] = []
        while True:
            if not self._in_frame:
                idx = self._buf.find(SOI)
                if idx < 0:
                    # No SOI: drop junk, but keep a trailing 0xFF in case the
                    # marker is split across chunk boundaries.
                    if self._buf and self._buf[-1] == 0xFF:
                        del self._buf[:-1]
                    else:
                        self._buf.clear()
                    return frames
                del self._buf[:idx]
                self._in_frame = True
                self._scan = len(SOI)
            idx = self._buf.find(EOI, self._scan)
            if idx < 0:
                # Partial frame: remember where to resume the EOI search
                # (step back one byte in case EOI straddles the boundary).
                self._scan = max(len(SOI), len(self._buf) - 1)
                return frames
            end = idx + len(EOI)
            frames.append(bytes(self._buf[:end]))
            del self._buf[:end]
            self._in_frame = False
            self._scan = 0
