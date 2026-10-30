"""relay-capture: HTTP daemon for HDMI screen capture of the DUT.

Run with::

    relay-capture [--device GLOB] [--size WxH] [--fps N] [--timeout S]
                  [--outdir DIR] [--host HOST] [--port PORT]

See ``docs/capture.md`` for hardware setup and API examples.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .hub import DEFAULT_DEVICE_GLOB, DEFAULT_OUTDIR, CaptureConfig

DEFAULT_PORT = 8002


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relay-capture",
        description="HDMI screen capture daemon for the device under test.",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE_GLOB,
        help="V4L2 device path or glob; the first sorted match is used.",
    )
    parser.add_argument(
        "--size",
        default="1920x1080",
        help="Capture resolution WxH.",
    )
    parser.add_argument(
        "--fps", type=int, default=30, help="Capture framerate."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Watchdog: restart ffmpeg after this many seconds without data.",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help="Directory for MKV recordings.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host.")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Bind port."
    )
    return parser


def main(argv: "list[str] | None" = None) -> None:
    import uvicorn

    from .api import create_app

    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = CaptureConfig(
        device=args.device,
        size=args.size,
        fps=args.fps,
        timeout=args.timeout,
        outdir=args.outdir,
    )
    app = create_app(cfg)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
