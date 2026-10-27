# relay-mcp: MCP server for board-level relay control

`relay-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io)
server that lets AI agents (OpenClaw/NemoClaw, GitHub Copilot, Claude, local
models in Ollama, …) power-cycle, reset and switch boot modes on a device
under test (DUT) wired to the Waveshare relay hat.

It is a thin layer over the existing `BoardController`: it talks to the relay
daemon over HTTP only and never imports GPIO code, so it can run on the Pi
itself or on any host that can reach the daemon. It is designed to run next to
`bsp-mcp` (bsp-registry-tools) so a Tester agent can flash an image, set the
boot mode, power-cycle the board and run tests over serial with no human at
the bench.

## Install

```bash
pip install "relay-tools[mcp]"
```

The `mcp` extra needs Python 3.10+. The rest of relay-tools keeps working on
Python 3.9 (Raspberry Pi OS Bookworm ships 3.11).

## Run

stdio (default; for Copilot/Claude-style clients that spawn the server):

```bash
relay-mcp --url http://pi.local:8000 --config-dir /etc/relay/boards.d
```

Streamable HTTP (for clients such as NemoClaw behind an HTTPS proxy); the MCP
endpoint is served on `/mcp`:

```bash
relay-mcp --url http://pi.local:8000 --transport http --host 127.0.0.1 --port 8766
```

All logging goes to stderr; on stdio, stdout carries the MCP protocol.

### Options

| Flag | Env var | Default | Meaning |
|---|---|---|---|
| `--url` | `RELAY_API_URL` | `http://localhost:8000` | Relay daemon URL |
| `--config-dir` | – | `/etc/relay/boards.d` | Board profile directory |
| – | `RELAY_BOARD_DEFAULT` / `RELAY_BOARD_CONFIG` | – | Default board when a tool's `board` argument is omitted |
| `--api-token` | `RELAY_API_TOKEN` | – | Optional bearer token sent as `Authorization` on every daemon request (for a daemon behind a token-checking proxy); never logged |
| `--cooldown` | `RELAY_MCP_COOLDOWN` | `5` | Seconds between power actions on the same board |
| `--audit-log` | `RELAY_MCP_AUDIT_LOG` | `~/.local/state/relay-mcp/audit.jsonl` | Audit log path |
| `--read-only` | `RELAY_MCP_READ_ONLY=1` | off | Refuse every state-changing tool |
| `--allow-board` | – | all boards | Limit the exposed boards (repeatable) |
| `--transport` | – | `stdio` | `stdio` or `http` |
| `--host` / `--port` | – | `127.0.0.1` / `8766` | HTTP bind address |

### Client configuration example

```json
{
  "mcpServers": {
    "relay": {
      "command": "relay-mcp",
      "args": [
        "--url", "http://pi.local:8000",
        "--config-dir", "/etc/relay/boards.d"
      ]
    }
  }
}
```

## Tools

Every tool takes an optional `board` argument (default: the board resolved
from `RELAY_BOARD_DEFAULT` / `RELAY_BOARD_CONFIG`).

| Tool | Description |
|---|---|
| `list_boards` | List board profiles in the config dir; invalid files are skipped and their errors reported |
| `describe_board` | Signals, switches, boot modes (with `risky` flag), workflows, timings and defaults — profile only, no daemon call |
| `board_status` | Live signals, switches, `matching_boot_modes`, raw channel states, plus `power` (`on`/`off`/`unknown`) and `power_switch`, derived from the profile's power switch (not measured at the DUT) |
| `power_on` / `power_off` / `power_cycle` / `reset` | The profile's power/reset actions (`power_off` is marked destructive) |
| `set_boot_mode(mode)` | Set a boot mode's strap signals/switches; risky modes are refused |
| `boot_and_wait(mode)` | Set a boot mode, power-cycle, wait for the profile's `boot_wait` |
| `run_workflow(workflow)` | Run a named workflow from the profile |
| `audit_log(limit=20)` | Newest audit entries, limit clamped to 1..200 |

Prompts (shown as slash commands in most clients): `smoke_test`,
`power_cycle_and_check(board)`, `boot_from(board, mode)`.

## Sample prompts

### Built-in MCP prompts

The server ships three prompts. Clients such as Claude Desktop or VS Code show
them as slash commands; selecting one inserts a ready-made instruction for the
model.

| Prompt | Arguments | What the agent does |
|---|---|---|
| `/smoke_test` | – | Calls `list_boards`, then `describe_board` and `board_status` for each board. Read-only: no relay state is changed. Reports what it could see. |
| `/power_cycle_and_check` | `board` | Calls `board_status`, `power_cycle`, then `board_status` again and confirms the power switch ended active. |
| `/boot_from` | `board`, `mode` | Calls `describe_board` to check the mode exists and is not risky, then `set_boot_mode`, `power_cycle` and `board_status`, confirming `mode` is in `matching_boot_modes`. |

### Example free-form prompts

You can also just ask in plain language. Some prompts that map cleanly onto
the tools:

- *"Which boards can you control, and what boot modes does each one have?"*
  → `list_boards`, `describe_board`
- *"Is the lab board powered on right now? Which boot mode is it strapped
  for?"* → `board_status`
- *"Power-cycle the lab board and tell me when it's back up."*
  → `power_cycle`, then `board_status`
- *"Set the lab board to boot from eMMC and reboot it."*
  → `set_boot_mode(mode="emmc")`, `power_cycle`
- *"Boot the board from SD and wait until it has had time to start."*
  → `boot_and_wait(mode="sd")`
- *"Run the power-cycle workflow defined in the board profile."*
  → `run_workflow(workflow="power-cycle")`
- *"Show me the last ten relay actions that were performed."*
  → `audit_log(limit=10)`

Prompts that ask for raw channel control ("turn relay 3 on"), `force`, or a
risky boot mode ("put the board in usb-recovery") are refused by design — see
the safety rules below.

## Safety rules

1. **Board-level actions only.** No raw channel tools (`on`, `off`, `toggle`,
   `press`, `all-on`, `all-off`). The board profile in `/etc/relay/boards.d/`
   is the allowlist of what an agent may do.
2. **No `force`.** Boot modes marked `risky: true` (for example
   `usb-recovery`) are always refused; a human must run
   `relay-board set-boot-mode <mode> --force` at the bench. Workflows whose
   steps reach a risky boot mode are refused up front.
3. **Always verify.** Every action runs with `verify=True` unless the
   profile's `defaults.verify` is false.
4. **One action at a time per board**, plus a cooldown (default 5 s) between
   power actions on the same board. A refused action reports the seconds
   remaining.
5. **Audit log.** Every state-changing tool call appends one JSON line
   (UTC timestamp, board, tool, arguments, ok/error, executed steps, final
   matching boot modes) — success and failure alike.
6. **Read-only mode** for demos and untrusted agents: `--read-only`.
7. **Validated names.** Board, boot mode and workflow names must match
   `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`; boards resolve only to
   `<config-dir>/<name>.yaml` (no path separators, no `..`).

> **Note:** the relay daemon itself has no authentication. Bind it to the lab
> network only, or put it behind a reverse proxy that checks a token and pass
> that token with `--api-token` / `RELAY_API_TOKEN`.

## Worked example with bsp-mcp

A Tester agent with both `bsp-mcp` and `relay-mcp` configured can run a full
flash-and-test cycle:

1. `start_flash` (bsp-mcp) – flash the image to the DUT.
2. `set_boot_mode` with `mode="emmc"` (relay-mcp) – strap the board to boot
   from eMMC.
3. `power_cycle` (relay-mcp) – power-cycle the board into the new image.
4. `start_hil_test --backend direct-serial` (bsp-mcp) – run the test suite
   over the serial console.

The agent can call `board_status` between steps to confirm the power switch is
active and `emmc` is listed in `matching_boot_modes`.
