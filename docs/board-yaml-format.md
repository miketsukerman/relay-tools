# Board YAML format and workflow tutorials

This guide documents the board profile schema used by `relay-board`.

Board profiles map named board controls to relay channels, define boot modes,
and optionally define reusable workflows.

---

## File location and loading

- Override with:
  - `relay-board --config /path/to/profile.yaml ...` (highest priority)
  - `relay-board <config_name> ...` → `/etc/relay/boards.d/<config_name>.yaml`
  - `RELAY_BOARD_CONFIG=/path/to/profile.yaml relay-board ...`
  - `RELAY_BOARD_DEFAULT=<name-or-path> relay-board ...`
- `config_name` and `--config` are mutually exclusive.
- Selection precedence: `--config` > `config_name` > `RELAY_BOARD_CONFIG` >
  `RELAY_BOARD_DEFAULT`.
- If nothing resolves a board profile, `relay-board` exits with an error.

The file must be a YAML mapping (dictionary) at the top level.

---

## Top-level keys

`relay-board` supports these top-level sections:

- `name` (optional string): profile name shown in errors and status output.
- `signals` (optional mapping): named momentary controls (for example reset).
- `switches` (optional mapping): named maintained controls (for example DIP switches).
- `timings` (optional mapping): named durations in seconds.
- `defaults` (optional mapping): default controls used by built-in commands.
- `boot_modes` (optional mapping): named switch/signal state sets.
- `workflows` (optional mapping): named step sequences.

Each signal/switch entry supports:

- `channel` (required integer)
- `active` (optional state, default `on`)
- `description` (optional string)

Valid state values are the same as channel config parsing (`on/off`, `true/false`,
`1/0`, case-insensitive).

Important validation rules:

- A relay channel can only be mapped once across `signals` and `switches`.
- `defaults.power_signal` and `defaults.reset_signal` must reference names in `signals`.
- `defaults.power_switch` must reference a name in `switches`.
- `defaults.verify` must be boolean.
- Timing values must be numbers greater than zero.

---

## `defaults` behavior

`defaults` controls fallback behavior when a named workflow is not provided:

- `power-on`: uses workflow `power-on` if defined, otherwise uses `power_switch`
  (set active) or `power_signal` pulse via `timings.power_on_pulse`.
- `power-off`: uses workflow `power-off` if defined, otherwise uses `power_switch`
  (set inactive) or `power_signal` pulse via `timings.power_off_pulse`.
- `reset`: uses workflow `reset` if defined, otherwise pulses `reset_signal` using
  `timings.reset_pulse`.
- `power-cycle`: uses workflow `power-cycle` if defined, otherwise composes power off,
  optional `timings.settle_delay`, then power on.
- `boot-and-wait`: uses workflow `boot-and-wait` if defined, otherwise runs
  `set-boot-mode`, then power cycle behavior, then optional `timings.boot_wait`.

`defaults.verify` is used unless you pass `--verify` or `--no-verify`.

---

## `boot_modes` format

Each boot mode can define target states for signals and switches:

```yaml
boot_modes:
  emmc:
    description: Boot from onboard eMMC
    risky: false
    switches:
      sw1001_1: off
      sw1001_2: off
      sw1002: off
      sw1003: off
```

- `risky: true` requires `relay-board ... --force`.
- Unknown signal/switch names are rejected during config load.

---

## `workflows` format

`workflows` is a mapping from workflow name to a list of steps.

Supported step actions:

- `set`: set one `signal` or one `switch` to required `state`.
- `pulse`: pulse a `signal` for `duration` or named `timing`.
- `delay`: wait for `duration` or named `timing`.
- `verify`: verify one `signal` or one `switch` is at required `state`.
- `set-boot-mode`: expand and apply a named `boot_mode`.

Optional per-step fields:

- `name`: custom step label (auto-generated if omitted)
- `recovery_hint`: custom hint shown on failure

Action requirements:

- `set`/`verify` require exactly one of `signal` or `switch`, plus `state`.
- `pulse` requires `signal` and cannot target a switch.
- `pulse`/`delay` require `duration` or `timing`.
- `set-boot-mode` requires `boot_mode`.

Example:

```yaml
timings:
  short_pulse: 0.25
  settle_delay: 1.0

workflows:
  cold-boot-emmc:
    - name: apply boot selectors
      action: set-boot-mode
      boot_mode: emmc
    - name: power off
      action: set
      switch: general_power_input
      state: off
    - name: settle
      action: delay
      timing: settle_delay
    - name: power on
      action: set
      switch: general_power_input
      state: on
    - name: verify power is on
      action: verify
      switch: general_power_input
      state: on
```

---

## Example 1: minimal switch-driven profile

```yaml
name: lab-board

defaults:
  verify: true
  power_switch: main_power

switches:
  main_power:
    channel: 1
    description: Main power enable

timings:
  settle_delay: 1.0

boot_modes:
  normal:
    switches: {}
```

This profile is enough for:

- `relay-board power-on`
- `relay-board power-off`
- `relay-board power-cycle`

---

## Example 2: signal + reset profile

```yaml
name: signal-board

defaults:
  verify: true
  power_signal: pwr_key
  reset_signal: rst_key

signals:
  pwr_key:
    channel: 1
    active: on
    description: Power key (momentary)
  rst_key:
    channel: 2
    active: on
    description: Reset key (momentary)

timings:
  power_on_pulse: 0.2
  power_off_pulse: 4.0
  reset_pulse: 0.2
  boot_wait: 30

boot_modes:
  default:
    signals: {}
```

This profile uses pulse-based defaults for power and reset commands.

---

## Workflow usage tutorials

## 1) First-time setup and validation

1. Copy a profile:
   ```bash
   sudo install -D -m 0644 \
     /usr/share/relay-tools/examples/lab-board.yaml \
     /etc/relay/boards.d/lab.yaml
   ```
2. Verify daemon reachability and profile parse:
   ```bash
   relay-board --url http://localhost:8000 --config /etc/relay/boards.d/lab.yaml status
   ```
3. Confirm signals/switches render and no config error is shown.

## 2) Boot mode application tutorial

1. Review available modes in your YAML (`boot_modes` keys).
2. Apply one mode:
   ```bash
   relay-board set-boot-mode emmc
   ```
3. If mode is marked `risky: true`, re-run with:
   ```bash
   relay-board set-boot-mode usb-recovery --force
   ```
4. Re-check board state:
   ```bash
   relay-board status
   ```

## 3) End-to-end boot-and-wait tutorial

1. Apply boot mode and run boot sequence:
   ```bash
   relay-board boot-and-wait emmc
   ```
2. Disable verification temporarily when troubleshooting wiring:
   ```bash
   relay-board boot-and-wait emmc --no-verify
   ```
3. Re-enable verification once wiring is confirmed:
   ```bash
   relay-board boot-and-wait emmc --verify
   ```

## 4) Customizing built-in workflow names

`relay-board` commands call specific workflow names when present:

- `relay-board power-on` → workflow `power-on`
- `relay-board power-off` → workflow `power-off`
- `relay-board reset` → workflow `reset`
- `relay-board power-cycle` → workflow `power-cycle`
- `relay-board boot-and-wait <mode>` → workflow `boot-and-wait`
- `relay-board flash-internal-memory` → workflow `flash-internal-memory`

To customize behavior, define those names in `workflows` and then run the normal
CLI command. This keeps operations consistent for operators while allowing board-
specific step sequences.

To run any other workflow name directly, use:

- `relay-board run-workflow <workflow-name>`

---

## Troubleshooting checklist

- **Config parse errors**: verify key names and indentation.
- **Unknown signal/switch/timing/mode**: ensure references match exactly.
- **Channel conflict**: each channel can only be used once across all controls.
- **`--force` required**: boot mode is marked `risky: true`.
- **Verification failures**: check relay wiring polarity and `active` values.
