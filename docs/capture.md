# Screen capture (relay-capture)

`relay-capture` captures the DUT's HDMI output through a cheap UVC
HDMI-to-USB dongle (for example a UGREEN CM629) plugged into the Pi, and
serves it over HTTP: screenshots, a live MJPEG stream (viewable in a
browser) and MKV recordings. It is designed for watching the DUT during
boot, before any network or serial login exists.

No video is ever decoded or re-encoded on the Pi: one long-lived ffmpeg
process stream-copies the dongle's MJPEG frames (`-c:v copy`), Python only
splits the byte stream into frames and fans them out to consumers. Recording
is a second ffmpeg stream copy into an MKV container.

## Hardware setup

1. Connect the DUT's HDMI output to the dongle's HDMI input.
2. Plug the dongle into a USB 3 port of the Pi (USB 2 limits MJPEG
   1080p30 bandwidth on some dongles).
3. The dongle is a standard UVC device: it appears as `/dev/videoN`
   (driver `uvcvideo`) without any vendor driver.

Find the device and its formats:

```bash
ls -l /dev/v4l/by-id/
# usb-UGREEN_...-video-index0 -> ../../video0

v4l2-ctl -d /dev/video0 --list-formats-ext
# look for MJPG 1920x1080 @ 30 fps
```

Prefer the stable `/dev/v4l/by-id/...-video-index0` path over `/dev/videoN`
(index0 is the capture node; index1 is metadata). The daemon's `--device`
option is a glob that is re-resolved on every (re)start, so the default
`/dev/v4l/by-id/usb-*-video-index0` survives re-plugging.

## Running the daemon

```bash
relay-capture --device '/dev/v4l/by-id/usb-*-video-index0' \
              --size 1920x1080 --fps 30 --timeout 5 \
              --outdir /var/lib/relay-capture \
              --host 0.0.0.0 --port 8002
```

All options shown are the defaults. The port is 8002 because `relay-console`
already uses 8001.

As a systemd service (Debian package installs the unit; it ships disabled):

```bash
sudo cp deploy/systemd/relay-capture.service /etc/systemd/system/
sudo cp deploy/systemd/relay-capture.env.example /etc/default/relay-capture
sudo systemctl daemon-reload
sudo systemctl enable --now relay-capture
```

The unit runs unprivileged (`DynamicUser=yes`) with the `video` and `audio`
supplementary groups, and records into `/var/lib/relay-capture`
(`StateDirectory`). GPIO stays isolated in the relay daemon; the capture
daemon never touches it.

## HTTP API

| Method | Path | Behavior |
|---|---|---|
| GET | `/capture/snapshot?timeout=3` | Next frame as `image/jpeg`; header `X-Frame-Time` carries the unix timestamp. 503 when no frame arrives within the timeout. |
| GET | `/capture/stream` | Live MJPEG stream (`multipart/x-mixed-replace`), open it in a browser. |
| POST | `/capture/record/start?name=...` | Start an MKV recording; `{"file": path}`. 409 when already recording, 422 for an invalid name. |
| POST | `/capture/record/stop` | Stop and finalize the recording; `{"file": path or null}`. |
| GET | `/capture/status` | `{"fps", "last_frame_age_s", "recording", "device"}`. |

Examples:

```bash
# Screenshot of the DUT
curl -o s.jpg 'http://pi.local:8002/capture/snapshot'

# Live view: open http://pi.local:8002/capture/stream in a browser

# Record a boot
curl -X POST 'http://pi.local:8002/capture/record/start?name=boot-emmc'
# ... boot the DUT ...
curl -X POST 'http://pi.local:8002/capture/record/stop'

# Health
curl 'http://pi.local:8002/capture/status'
# {"fps": 30.0, "last_frame_age_s": 0.03, "recording": null, "device": "/dev/video0"}
```

Or with the CLI (`RELAY_CAPTURE_URL` or `--capture-url` select the daemon):

```bash
relay-client capture snapshot -o s.jpg
relay-client capture record-start --name boot-emmc
relay-client capture record-stop
relay-client capture status
```

MCP agents get the read-only `dut_screenshot` tool from `relay-mcp`
(see [`mcp-server.md`](mcp-server.md)).

## Capturing a boot: HDMI hot-plug ordering

Start capture (or at least have the dongle powered and enumerated)
**before** powering the DUT on. The dongle presents an EDID and asserts
HDMI hot-plug detect (HPD); the DUT's firmware/bootloader samples HPD and
EDID at boot, and many boards output nothing on HDMI when no sink was
present at power-on. A typical sequence:

```bash
relay-client capture record-start --name boot-test   # capture first
relay-board lab power on                             # then power the DUT
# ... wait for boot, take snapshots, etc. ...
relay-client capture record-stop
```

## Troubleshooting

### Watchdog restarts and `fps: 0`

Some dongles simply stop sending frames when there is no HDMI signal (DUT
off, or still in early boot). The daemon's watchdog then kills and restarts
ffmpeg every few seconds and `/capture/status` reports `fps: 0`. This is
expected; frames resume as soon as the DUT outputs video. The daemon never
exits over this, so it is safe to leave running.

### USB autosuspend

If the Pi autosuspends the dongle, HPD can drop and the DUT may renegotiate
or lose its display. Disable autosuspend either globally on the kernel
command line:

```
usbcore.autosuspend=-1
```

or per device (persist with a udev rule):

```bash
echo on | sudo tee /sys/bus/usb/devices/<bus-port>/power/control
```

### HDCP

HDCP-protected sources cannot be captured: the dongle presents no HDCP keys
and the source blanks or refuses output. Disable HDCP on the DUT when
possible; typical embedded bootloaders and Linux consoles do not use it.

### JPEG viewers rejecting snapshot frames

Some dongles emit MJPEG frames without Huffman tables, which strict JPEG
decoders reject. If snapshots do not open in your viewer, add the
`mjpeg2jpeg` bitstream filter to the capture command in
`relay_tools/capture/hub.py` (or request it as a future `--bsf` option):

```
-bsf:v mjpeg2jpeg
```

It rewrites each frame into a standalone JPEG without re-encoding.

## Non-goals (for now)

No decoding/re-encoding, no image comparison or OCR, no audio capture. The
design keeps room for them: everything consumes frames from the same hub, so
a future `wait_for` endpoint can subscribe and match frames against a
reference image.
