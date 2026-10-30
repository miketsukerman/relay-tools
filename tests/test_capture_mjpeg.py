"""Tests for the MJPEG stream splitter."""

from __future__ import annotations

from relay_tools.capture.mjpeg import MjpegSplitter


def _frame(payload: bytes) -> bytes:
    return b"\xff\xd8" + payload + b"\xff\xd9"


FRAME_A = _frame(b"\x00AAA\x01")
FRAME_B = _frame(b"BBBB")
FRAME_FF = _frame(b"\x00\xff\x00pay\xff\x00load")  # stuffed 0xFF bytes


def test_single_frame_one_chunk():
    splitter = MjpegSplitter()
    assert splitter.feed(FRAME_A) == [FRAME_A]


def test_multiple_frames_one_chunk():
    splitter = MjpegSplitter()
    assert splitter.feed(FRAME_A + FRAME_B + FRAME_FF) == [
        FRAME_A,
        FRAME_B,
        FRAME_FF,
    ]


def test_byte_by_byte_feed():
    splitter = MjpegSplitter()
    stream = FRAME_A + FRAME_B
    frames = []
    for i in range(len(stream)):
        frames.extend(splitter.feed(stream[i : i + 1]))
    assert frames == [FRAME_A, FRAME_B]


def test_leading_junk_is_discarded():
    splitter = MjpegSplitter()
    assert splitter.feed(b"garbage\x00\xff\x00" + FRAME_A) == [FRAME_A]


def test_junk_then_frame_in_later_chunk():
    splitter = MjpegSplitter()
    assert splitter.feed(b"junk") == []
    assert splitter.feed(FRAME_A) == [FRAME_A]


def test_soi_split_across_chunks():
    splitter = MjpegSplitter()
    # Junk ends with 0xFF, SOI second byte arrives in the next chunk.
    assert splitter.feed(b"junk\xff") == []
    assert splitter.feed(b"\xd8payload\xff\xd9") == [_frame(b"payload")]


def test_eoi_split_across_chunks():
    splitter = MjpegSplitter()
    assert splitter.feed(b"\xff\xd8payload\xff") == []
    assert splitter.feed(b"\xd9") == [_frame(b"payload")]


def test_ff_bytes_inside_payload():
    splitter = MjpegSplitter()
    frames = []
    for i in range(0, len(FRAME_FF), 3):
        frames.extend(splitter.feed(FRAME_FF[i : i + 3]))
    assert frames == [FRAME_FF]


def test_partial_frame_is_buffered():
    splitter = MjpegSplitter()
    assert splitter.feed(b"\xff\xd8partial") == []
    assert splitter.feed(b"more") == []
    assert splitter.feed(b"\xff\xd9" + FRAME_B) == [
        _frame(b"partialmore"),
        FRAME_B,
    ]
