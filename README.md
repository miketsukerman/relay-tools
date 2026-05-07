# relay-tools

[![Tests](https://github.com/miketsukerman/relay-tools/actions/workflows/tests.yml/badge.svg)](https://github.com/miketsukerman/relay-tools/actions/workflows/tests.yml)

Python package for controlling relay hats on Raspberry Pi.
Includes support for the [Waveshare RPi Relay Board (B)](https://www.waveshare.com/wiki/RPi_Relay_Board_(B))
(8-channel GPIO relay hat) on Raspberry Pi 4 and Raspberry Pi 5.

---

## Features

- **Abstract relay API** – a base class (`AbstractRelayBoard`) that can be
  implemented for any relay hardware.
- **Waveshare RPi Relay Board (B) driver** – GPIO control via `gpiozero`
  (compatible with Raspberry Pi 4 and Raspberry Pi 5).
- **CLI** – control individual channels or all channels from the terminal.
- **REST API** – an HTTP API built with FastAPI.

---

## Hardware: GPIO pin mapping

The Waveshare RPi Relay Board (B) uses the following BCM GPIO pins
(active-LOW – the relay **closes** when the pin is driven LOW):

| Channel | BCM GPIO |
|---------|----------|
| 1       | 5        |
| 2       | 6        |
| 3       | 13       |
| 4       | 16       |
| 5       | 19       |
| 6       | 20       |
| 7       | 21       |
| 8       | 26       |

---

## Installation

**Basic install (no GPIO)**

```bash
pip install relay-tools
```

**With GPIO support** (required on Raspberry Pi):

```bash
pip install "relay-tools[gpio]"
```

> On Raspberry Pi 5, `lgpio` is used as the gpiozero pin factory automatically.
> Ensure `lgpio` is installed: `pip install lgpio`.

---

## CLI usage

```bash
# Turn channel 1 ON
relay on 1

# Turn channel 3 OFF
relay off 3

# Toggle channel 2
relay toggle 2

# Press channel 2 (momentary ON then OFF)
relay press 2

# Show the state of all channels
relay status

# Turn all channels ON
relay all-on

# Turn all channels OFF
relay all-off
```

---

## REST API

Start the server:

```bash
uvicorn relay_tools.api:app --host 0.0.0.0 --port 8000
```

### Endpoints

| Method | Path                        | Description                     |
|--------|-----------------------------|---------------------------------|
| GET    | `/relays`                   | State of all channels           |
| GET    | `/relays/{channel}`         | State of a single channel       |
| POST   | `/relays/{channel}/on`      | Turn a channel on               |
| POST   | `/relays/{channel}/off`     | Turn a channel off              |
| POST   | `/relays/{channel}/toggle`  | Toggle a channel                |
| POST   | `/relays/{channel}/press`   | Momentarily press a channel     |
| POST   | `/relays/on`                | Turn all channels on            |
| POST   | `/relays/off`               | Turn all channels off           |

Interactive documentation is available at `http://<host>:8000/docs`.

### Examples

```bash
# Turn channel 1 on
curl -X POST http://localhost:8000/relays/1/on

# Turn channel 1 off
curl -X POST http://localhost:8000/relays/1/off

# Toggle channel 2
curl -X POST http://localhost:8000/relays/2/toggle

# Press channel 2 (default hold duration: 0.2s)
curl -X POST http://localhost:8000/relays/2/press

# Press channel 2 with custom hold duration
curl -X POST "http://localhost:8000/relays/2/press?duration=0.5"

# Get state of all channels
curl http://localhost:8000/relays

# Get state of a single channel
curl http://localhost:8000/relays/3

# Turn all channels on
curl -X POST http://localhost:8000/relays/on

# Turn all channels off
curl -X POST http://localhost:8000/relays/off
```

---

## Python API

### Basic usage

```python
from relay_tools import WaveshareRelayBoard

# Use as a context manager – board is closed automatically on exit
with WaveshareRelayBoard() as board:
    board.turn_on(1)          # close relay 1
    print(board.is_on(1))     # True
    print(board.get_state())  # {1: True, 2: False, ...}
    board.turn_off_all()
```

### Controlling individual channels

```python
from relay_tools import WaveshareRelayBoard

with WaveshareRelayBoard() as board:
    board.turn_on(3)           # activate channel 3
    board.turn_off(3)          # deactivate channel 3

    # Toggle: flip current state
    if board.is_on(2):
        board.turn_off(2)
    else:
        board.turn_on(2)
```

### Bulk operations

```python
from relay_tools import WaveshareRelayBoard

with WaveshareRelayBoard() as board:
    board.turn_on_all()        # activate every channel
    state = board.get_state()  # {1: True, 2: True, ..., 8: True}
    board.turn_off_all()       # deactivate every channel
```

### Error handling

```python
from relay_tools import WaveshareRelayBoard

with WaveshareRelayBoard() as board:
    try:
        board.turn_on(99)      # invalid channel
    except ValueError as exc:
        print(exc)             # Channel 99 is out of range. Valid channels: 1–8.
```

### Implementing a custom driver

```python
from relay_tools.base import AbstractRelayBoard

class MyRelayBoard(AbstractRelayBoard):
    @property
    def num_channels(self) -> int:
        return 4

    def turn_on(self, channel: int) -> None:
        self._validate_channel(channel)
        # your hardware code here

    def turn_off(self, channel: int) -> None:
        self._validate_channel(channel)
        # your hardware code here

    def is_on(self, channel: int) -> bool:
        self._validate_channel(channel)
        # your hardware code here
        return False
```

---

## Development

```bash
pip install -e ".[dev]"
pytest
```
