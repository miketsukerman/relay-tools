# relay-tools

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
relay-tools on 1

# Turn channel 3 OFF
relay-tools off 3

# Toggle channel 2
relay-tools toggle 2

# Show the state of all channels
relay-tools status

# Turn all channels ON
relay-tools all-on

# Turn all channels OFF
relay-tools all-off
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
| POST   | `/relays/on`                | Turn all channels on            |
| POST   | `/relays/off`               | Turn all channels off           |

Interactive documentation is available at `http://<host>:8000/docs`.

### Example

```bash
# Turn channel 1 on
curl -X POST http://localhost:8000/relays/1/on

# Get state of all channels
curl http://localhost:8000/relays
```

---

## Python API

```python
from relay_tools import WaveshareRelayBoard

with WaveshareRelayBoard() as board:
    board.turn_on(1)          # close relay 1
    print(board.is_on(1))     # True
    print(board.get_state())  # {1: True, 2: False, ...}
    board.turn_off_all()
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
