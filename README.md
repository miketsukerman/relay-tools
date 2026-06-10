# relay-tools

[![Tests](https://github.com/Advantech-EECC/relay-tools/actions/workflows/tests.yml/badge.svg)](https://github.com/Advantech-EECC/relay-tools/actions/workflows/tests.yml)

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

## HTTP client usage

```bash
# Same channel-level commands over the daemon API
relay-client --url http://pi.local:8000 status
relay-client on 1
relay-client press 2 --duration 0.5
```

## Board control usage

Board control is additive and runs on top of the same relay daemon HTTP API used
by `relay-client`, so it can run from any host that can reach the daemon.

```bash
# Read the configured board signal and switch state
relay-board --config /etc/relay/boards.d/rom2820.yaml status

# Resolve config by board name from /etc/relay/boards.d/<name>.yaml
relay-board rom2820 status

# Apply a named boot-mode profile
relay-board set-boot-mode emmc

# Apply a boot mode, execute the boot workflow, then exit
relay-board boot-and-wait emmc

# Run a custom workflow by name
relay-board run-workflow flash-internal-memory

# Run the standard eMMC flashing workflow name
relay-board flash-internal-memory

# Override the daemon URL or board profile path
RELAY_API_URL=http://pi.local:8000 \
RELAY_BOARD_CONFIG=/etc/relay/boards.d/rom2820.yaml \
relay-board status

# Set package-level default board profile (name or path)
RELAY_BOARD_DEFAULT=lab \
relay-board status
```

`relay-board` executes configured relay actions and exits; it does not block on a
board-health condition. Use `--verify` (default from the board profile) to read
relay state back after each step.
You can pass an optional `config_name` as the first argument (before the
subcommand) to load `/etc/relay/boards.d/<config_name>.yaml`.
Selection precedence is:
`--config` > `config_name` > `RELAY_BOARD_CONFIG` > `RELAY_BOARD_DEFAULT`.

If none of those are set, `relay-board` exits with an error instead of falling
back to `rom2820`.

For full board profile schema details, worked YAML examples, action reference,
and workflow tutorials, see:

- [`docs/board-yaml-format.md`](docs/board-yaml-format.md)

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

## `/etc/relay` configuration layout

Channel startup state config remains unchanged:

- `/etc/relay/channels.yaml`

Board profiles are separate additive files:

- `/etc/relay/boards.d/<board-name>.yaml`

Board profiles define:

- relay channel mappings for named board signals and maintained switches,
- timing parameters,
- named boot modes,
- optional composite workflows.

Validation errors are surfaced with actionable CLI messages for missing files,
unknown signals or switches, invalid timings, missing boot modes, and
conflicting channel mappings.

## ROM2820 profile notes

The repository now ships a ROM2820 sample profile at
`systemd/rom2820-board.yaml` and, in
Debian packages, under `/usr/share/relay-tools/examples/rom2820-board.yaml`.

The current sample models the supplied ROM2820 controls as maintained switches:

- SW1003 → channel 1
- SW1002 → channel 2
- SW1001-2 → channel 3
- SW1001-1 → channel 4
- General power input → channel 5

The sample also treats general power input as a maintained switch: ON provides
power and OFF removes it. Reset mapping and exact boot-mode switch states were
not provided, so the sample keeps those fields commented until the operator
fills them from the board manual revision in use.

## systemd and deployment

The sample `relay-daemon.service` still deploys the daemon exactly as before and
continues to use `/etc/relay/channels.yaml` for channel startup state.

For board control:

1. Copy `systemd/rom2820-board.yaml` to `/etc/relay/boards.d/rom2820.yaml`.
2. Configure default board profile selection if desired:
   - `RELAY_BOARD_CONFIG=/path/to/profile.yaml` for an explicit path.
   - `RELAY_BOARD_DEFAULT=<name-or-path>` for package-level board default.
3. Run `relay-board` against the same daemon URL used by `relay-client`.

Existing `relay`, `relay-client`, and REST endpoints remain unchanged; board
control is opt-in additive functionality.
