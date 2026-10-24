"""Board profile configuration for board-level relay workflows."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from relay_tools.config import _parse_state

logger = logging.getLogger(__name__)

DEFAULT_BOARD_CONFIG_DIR = Path("/etc/relay/boards.d")
DEFAULT_BOARD_CONFIG_ENV = "RELAY_BOARD_CONFIG"
DEFAULT_BOARD_SELECTOR_ENV = "RELAY_BOARD_DEFAULT"
SUPPORTED_WORKFLOW_ACTIONS = frozenset(
    {"set", "pulse", "delay", "verify", "set-boot-mode"}
)

STANDARD_BAUD_RATES = frozenset(
    {
        50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800,
        9600, 19200, 38400, 57600, 115200, 230400, 460800, 500000,
        576000, 921600, 1000000, 1152000, 1500000, 2000000, 2500000,
        3000000, 3500000, 4000000,
    }
)
CONSOLE_ACTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
CONSOLE_ACTION_CONTEXTS = frozenset({"linux", "uboot"})
CONSOLE_ACTION_PARSERS = frozenset({"ipv4", "systemd", "kv", "lines"})
# Commands with these fragments are refused unless the action sets
# ``mutating: true``: console actions are meant to be read-only.
# This is an advisory blocklist, not a sandbox: the real control is that
# profiles are root-owned files on the Pi and agents cannot add actions.
CONSOLE_MUTATION_TOKENS = (
    ">", "rm ", "dd ", "reboot", "poweroff", "shutdown", "halt",
    "mkfs", "fw_setenv", "setenv", "saveenv", "mmc write", "erase", "| sh",
    "systemctl start", "systemctl stop", "systemctl restart",
    "systemctl isolate", "systemctl kill", "kill", "tee ", "sed -i", "mv ",
    "chmod", "chown", "ip link set", "ip addr add", "ip addr del",
    "ip route add", "ip route del", "ifdown", "rmmod", "modprobe -r",
    "fdisk", "parted", "flash_erase", "nandwrite", "insmod",
)
# Extra fragments refused in U-Boot actions only (too broad for Linux).
CONSOLE_UBOOT_MUTATION_TOKENS = (
    "env set", "env save", "env default", "run ", "boot", "reset", "go ",
    "mw", "nand write", "sf write", "sf update", "ums", "fastboot", "load",
    "tftp", "dhcp",
)
DEFAULT_CONSOLE_ERROR_PATTERNS = (
    "Kernel panic",
    "Oops",
    "BUG:",
    "Unable to handle kernel",
    "Call trace",
    r"\[FAILED\]",
    "Failed to start",
)
DEFAULT_CONSOLE_WARNING_PATTERNS = (
    "rcu: INFO",
    "watchdog",
    "mmc[0-9]+: error",
)


class BoardConfigError(ValueError):
    """Raised when a board profile is invalid."""


def resolve_board_config_name(
    config_name: str,
    *,
    config_dir: str | Path = DEFAULT_BOARD_CONFIG_DIR,
) -> str:
    """Resolve a board config name to an absolute YAML path."""

    if "/" in config_name or "\\" in config_name:
        raise ValueError("Board config name must not include path separators.")
    if not config_name:
        raise ValueError("Board config name must not be empty.")
    file_name = config_name if config_name.endswith(".yaml") else f"{config_name}.yaml"
    return str(Path(config_dir) / file_name)


def resolve_default_board_config_path(
    *,
    env: Mapping[str, str] | None = None,
    config_dir: str | Path = DEFAULT_BOARD_CONFIG_DIR,
) -> str:
    """Resolve default board config path from env variables."""

    env_map = os.environ if env is None else env
    configured_path = (env_map.get(DEFAULT_BOARD_CONFIG_ENV) or "").strip()
    if configured_path:
        return configured_path

    configured_default = (env_map.get(DEFAULT_BOARD_SELECTOR_ENV) or "").strip()
    if configured_default:
        is_path = (
            Path(configured_default).is_absolute()
            or "/" in configured_default
            or "\\" in configured_default
        )
        if is_path:
            return configured_default
        return resolve_board_config_name(configured_default, config_dir=config_dir)

    raise ValueError(
        "Could not resolve a board config. Specify --config, a board config name, "
        "or set RELAY_BOARD_CONFIG/RELAY_BOARD_DEFAULT."
    )


@dataclass(frozen=True)
class SignalConfig:
    """Relay mapping for a named board signal."""

    name: str
    channel: int
    active: bool = True
    description: str | None = None


@dataclass(frozen=True)
class SwitchConfig:
    """Relay mapping for a named board switch."""

    name: str
    channel: int
    active: bool = True
    description: str | None = None


@dataclass(frozen=True)
class BootModeConfig:
    """Named boot-mode signal/switch state set."""

    name: str
    signals: dict[str, bool]
    switches: dict[str, bool]
    risky: bool = False
    description: str | None = None


@dataclass(frozen=True)
class WorkflowStepConfig:
    """Single workflow step loaded from YAML."""

    name: str
    action: str
    signal: str | None = None
    switch: str | None = None
    state: bool | None = None
    duration: float | None = None
    timing: str | None = None
    boot_mode: str | None = None
    recovery_hint: str | None = None


@dataclass(frozen=True)
class BoardDefaults:
    """Default signal/switch names and execution options."""

    power_signal: str | None = None
    power_switch: str | None = None
    reset_signal: str | None = None
    verify: bool = True


@dataclass(frozen=True)
class ConsoleActionConfig:
    """Named, pre-approved command runnable on the serial console."""

    name: str
    command: str
    context: str = "linux"
    parse: str = "lines"
    timeout_s: float = 5.0
    description: str | None = None
    mutating: bool = False


@dataclass(frozen=True)
class ConsoleLoginConfig:
    """Credentials and prompt used to log in on a Linux serial console."""

    user: str
    password_file: str | None = None
    password_env: str | None = None
    shell_prompt: str = "[#$] $"
    timeout_s: float = 10.0
    logout_after: bool = False


@dataclass(frozen=True)
class ConsoleRetentionConfig:
    """Boot log retention limits; whichever limit is hit first wins."""

    max_boots: int = 200
    max_megabytes: int = 200


@dataclass(frozen=True)
class ConsoleConfig:
    """Serial console capture settings for a board."""

    device: str
    baud: int = 115200
    data_bits: int = 8
    parity: str = "none"
    stop_bits: int = 1
    boot_banner: str | None = None
    uboot_prompt: str | None = None
    login_prompt: str | None = None
    error_patterns: tuple[str, ...] = DEFAULT_CONSOLE_ERROR_PATTERNS
    warning_patterns: tuple[str, ...] = DEFAULT_CONSOLE_WARNING_PATTERNS
    redact: tuple[str, ...] = ()
    retention: ConsoleRetentionConfig = field(
        default_factory=ConsoleRetentionConfig
    )
    mirror_port: int | None = None
    login: ConsoleLoginConfig | None = None
    actions: dict[str, ConsoleActionConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class BoardProfile:
    """Validated board profile."""

    name: str
    signals: dict[str, SignalConfig]
    switches: dict[str, SwitchConfig]
    timings: dict[str, float]
    boot_modes: dict[str, BootModeConfig]
    workflows: dict[str, tuple[WorkflowStepConfig, ...]]
    defaults: BoardDefaults
    console: ConsoleConfig | None = None


BUILTIN_CONSOLE_ACTIONS: dict[str, ConsoleActionConfig] = {
    action.name: action
    for action in (
        ConsoleActionConfig(
            name="ip_address",
            command="ip -4 -o addr show scope global",
            parse="ipv4",
            description="IPv4 addresses of the DUT's interfaces",
        ),
        ConsoleActionConfig(
            name="network_status",
            command="ip -o link show; ip route show default",
            parse="lines",
            description="Link states and the default route",
        ),
        ConsoleActionConfig(
            name="system_status",
            command=(
                "systemctl is-system-running; "
                "systemctl --failed --no-legend --plain"
            ),
            parse="systemd",
            description="systemd system state and failed units",
        ),
        ConsoleActionConfig(
            name="uptime",
            command="cat /proc/uptime",
            parse="lines",
            description="Seconds since the kernel booted",
        ),
        ConsoleActionConfig(
            name="os_release",
            command="cat /etc/os-release",
            parse="kv",
            description="OS identification from /etc/os-release",
        ),
        ConsoleActionConfig(
            name="kernel",
            command="uname -a",
            parse="lines",
            description="Kernel version string",
        ),
        ConsoleActionConfig(
            name="dmesg_tail",
            command="dmesg | tail -n 50",
            parse="lines",
            description="Last 50 kernel log lines",
        ),
        ConsoleActionConfig(
            name="uboot_net",
            command="printenv ipaddr serverip ethaddr",
            context="uboot",
            parse="kv",
            description="U-Boot network environment variables",
        ),
    )
}


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BoardConfigError(f"{context} must be a mapping.")
    return value


def _parse_duration(value: Any, context: str) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise BoardConfigError(f"{context} must be a number.") from exc
    if duration <= 0:
        raise BoardConfigError(f"{context} must be greater than zero.")
    return duration


def _parse_named_controls(
    raw_controls: Any,
    *,
    context: str,
    channels_in_use: dict[int, tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    controls_data = _require_mapping(raw_controls or {}, context)
    parsed_controls: dict[str, dict[str, Any]] = {}
    for control_name, raw_control in controls_data.items():
        control_data = _require_mapping(raw_control, f"{context}.{control_name}")
        try:
            channel = int(control_data["channel"])
        except KeyError as exc:
            raise BoardConfigError(
                f"{context}.{control_name} is missing required key 'channel'."
            ) from exc
        except (TypeError, ValueError) as exc:
            raise BoardConfigError(
                f"{context}.{control_name}.channel must be an integer."
            ) from exc
        if channel in channels_in_use:
            other_context, other_name = channels_in_use[channel]
            raise BoardConfigError(
                f"{context}.{control_name}.channel conflicts with "
                f"{other_context[:-1]} {other_name!r} on channel {channel}."
            )
        channels_in_use[channel] = (context, str(control_name))
        try:
            active = _parse_state(control_data.get("active", "on"), channel)
        except ValueError as exc:
            raise BoardConfigError(
                f"{context}.{control_name}.active is invalid: {exc}"
            ) from exc
        parsed_controls[str(control_name)] = {
            "name": str(control_name),
            "channel": channel,
            "active": active,
            "description": control_data.get("description"),
        }
    return parsed_controls


def _parse_signals(
    raw_signals: Any,
    *,
    channels_in_use: dict[int, tuple[str, str]],
) -> dict[str, SignalConfig]:
    return {
        name: SignalConfig(**data)
        for name, data in _parse_named_controls(
            raw_signals,
            context="signals",
            channels_in_use=channels_in_use,
        ).items()
    }


def _parse_switches(
    raw_switches: Any,
    *,
    channels_in_use: dict[int, tuple[str, str]],
) -> dict[str, SwitchConfig]:
    return {
        name: SwitchConfig(**data)
        for name, data in _parse_named_controls(
            raw_switches,
            context="switches",
            channels_in_use=channels_in_use,
        ).items()
    }


def _parse_boot_modes(
    raw_boot_modes: Any,
    *,
    signals: dict[str, SignalConfig],
    switches: dict[str, SwitchConfig],
) -> dict[str, BootModeConfig]:
    boot_modes_data = _require_mapping(raw_boot_modes or {}, "boot_modes")
    boot_modes: dict[str, BootModeConfig] = {}
    for mode_name, raw_mode in boot_modes_data.items():
        mode_data = _require_mapping(raw_mode, f"boot_modes.{mode_name}")
        raw_states = _require_mapping(
            mode_data.get("signals", {}), f"boot_modes.{mode_name}.signals"
        )
        signal_states: dict[str, bool] = {}
        for signal_name, state_value in raw_states.items():
            if signal_name not in signals:
                raise BoardConfigError(
                    f"boot_modes.{mode_name}.signals references unknown signal "
                    f"{signal_name!r}."
                )
            signal_states[str(signal_name)] = _parse_state(
                state_value, signals[str(signal_name)].channel
            )
        raw_switch_states = _require_mapping(
            mode_data.get("switches", {}), f"boot_modes.{mode_name}.switches"
        )
        switch_states: dict[str, bool] = {}
        for switch_name, state_value in raw_switch_states.items():
            if switch_name not in switches:
                raise BoardConfigError(
                    f"boot_modes.{mode_name}.switches references unknown switch "
                    f"{switch_name!r}."
                )
            switch_states[str(switch_name)] = _parse_state(
                state_value, switches[str(switch_name)].channel
            )
        risky_value = mode_data.get("risky", False)
        if not isinstance(risky_value, bool):
            raise BoardConfigError(f"boot_modes.{mode_name}.risky must be a boolean.")
        boot_modes[str(mode_name)] = BootModeConfig(
            name=str(mode_name),
            signals=signal_states,
            switches=switch_states,
            risky=risky_value,
            description=mode_data.get("description"),
        )
    return boot_modes


def _parse_workflows(
    raw_workflows: Any,
    *,
    signals: dict[str, SignalConfig],
    switches: dict[str, SwitchConfig],
    timings: dict[str, float],
    boot_modes: dict[str, BootModeConfig],
) -> dict[str, tuple[WorkflowStepConfig, ...]]:
    workflows_data = _require_mapping(raw_workflows or {}, "workflows")
    workflows: dict[str, tuple[WorkflowStepConfig, ...]] = {}
    for workflow_name, raw_steps in workflows_data.items():
        if not isinstance(raw_steps, list):
            raise BoardConfigError(f"workflows.{workflow_name} must be a list.")
        steps: list[WorkflowStepConfig] = []
        for index, raw_step in enumerate(raw_steps, start=1):
            step_data = _require_mapping(
                raw_step, f"workflows.{workflow_name}[{index}]"
            )
            action = str(step_data.get("action", "")).strip()
            if action not in SUPPORTED_WORKFLOW_ACTIONS:
                raise BoardConfigError(
                    f"workflows.{workflow_name}[{index}].action must be one of "
                    f"{sorted(SUPPORTED_WORKFLOW_ACTIONS)}."
                )
            step_name = str(step_data.get("name") or f"{workflow_name}:{index}")
            signal_name = step_data.get("signal")
            switch_name = step_data.get("switch")
            boot_mode_name = step_data.get("boot_mode")
            timing_name = step_data.get("timing")
            duration = step_data.get("duration")
            state = step_data.get("state")

            if action == "pulse":
                if signal_name not in signals:
                    raise BoardConfigError(
                        f"workflows.{workflow_name}[{index}] references unknown "
                        f"signal {signal_name!r}."
                    )
                if switch_name is not None:
                    raise BoardConfigError(
                        f"workflows.{workflow_name}[{index}] cannot pulse a switch."
                    )
            if action in {"set", "verify"}:
                if (signal_name is None) == (switch_name is None):
                    raise BoardConfigError(
                        f"workflows.{workflow_name}[{index}] requires exactly one "
                        "of 'signal' or 'switch'."
                    )
                target_channel: int
                if signal_name is not None:
                    if signal_name not in signals:
                        raise BoardConfigError(
                            f"workflows.{workflow_name}[{index}] references unknown "
                            f"signal {signal_name!r}."
                        )
                    target_channel = signals[str(signal_name)].channel
                else:
                    if switch_name not in switches:
                        raise BoardConfigError(
                            f"workflows.{workflow_name}[{index}] references unknown "
                            f"switch {switch_name!r}."
                        )
                    target_channel = switches[str(switch_name)].channel
                if state is None:
                    raise BoardConfigError(
                        f"workflows.{workflow_name}[{index}] requires 'state'."
                    )
                state = _parse_state(state, target_channel)
            else:
                state = None
            if action in {"pulse", "delay"}:
                if duration is None and timing_name is None:
                    raise BoardConfigError(
                        f"workflows.{workflow_name}[{index}] requires 'duration' "
                        "or 'timing'."
                    )
                if duration is not None:
                    duration = _parse_duration(
                        duration, f"workflows.{workflow_name}[{index}].duration"
                    )
                if timing_name is not None and str(timing_name) not in timings:
                    raise BoardConfigError(
                        f"workflows.{workflow_name}[{index}] references unknown "
                        f"timing {timing_name!r}."
                    )
            else:
                duration = None
                timing_name = None
            if action in {"delay", "set-boot-mode"}:
                signal_name = None
                switch_name = None
            if action == "set-boot-mode":
                if boot_mode_name not in boot_modes:
                    raise BoardConfigError(
                        f"workflows.{workflow_name}[{index}] references unknown "
                        f"boot mode {boot_mode_name!r}."
                    )
            else:
                boot_mode_name = None

            steps.append(
                WorkflowStepConfig(
                    name=step_name,
                    action=action,
                    signal=str(signal_name) if signal_name is not None else None,
                    switch=str(switch_name) if switch_name is not None else None,
                    state=state,
                    duration=duration,
                    timing=str(timing_name) if timing_name is not None else None,
                    boot_mode=(
                        str(boot_mode_name) if boot_mode_name is not None else None
                    ),
                    recovery_hint=step_data.get("recovery_hint"),
                )
            )
        workflows[str(workflow_name)] = tuple(steps)
    return workflows


def _parse_defaults(
    raw_defaults: Any,
    *,
    signals: dict[str, SignalConfig],
    switches: dict[str, SwitchConfig],
) -> BoardDefaults:
    defaults_data = _require_mapping(raw_defaults or {}, "defaults")
    power_signal = defaults_data.get("power_signal")
    power_switch = defaults_data.get("power_switch")
    reset_signal = defaults_data.get("reset_signal")
    for key, signal_name, allowed in (
        ("power_signal", power_signal, signals),
        ("power_switch", power_switch, switches),
        ("reset_signal", reset_signal, signals),
    ):
        if signal_name is not None and signal_name not in allowed:
            label = "switch" if key == "power_switch" else "signal"
            raise BoardConfigError(
                f"defaults.{key} references unknown {label} {signal_name!r}."
            )
    verify = defaults_data.get("verify", True)
    if not isinstance(verify, bool):
        raise BoardConfigError("defaults.verify must be a boolean.")
    return BoardDefaults(
        power_signal=str(power_signal) if power_signal is not None else None,
        power_switch=str(power_switch) if power_switch is not None else None,
        reset_signal=str(reset_signal) if reset_signal is not None else None,
        verify=verify,
    )


def _parse_regex(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise BoardConfigError(f"{context} must be a non-empty string.")
    try:
        re.compile(value)
    except re.error as exc:
        raise BoardConfigError(f"{context} is not a valid regex: {exc}") from exc
    return value


def _parse_regex_list(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BoardConfigError(f"{context} must be a list.")
    return tuple(
        _parse_regex(item, f"{context}[{index}]")
        for index, item in enumerate(value, start=1)
    )


def _parse_console_action(
    name: str,
    raw_action: Any,
    *,
    context_path: str,
) -> ConsoleActionConfig:
    if not CONSOLE_ACTION_NAME_PATTERN.match(name):
        raise BoardConfigError(
            f"{context_path} is not a valid action name: it must match "
            "^[a-z][a-z0-9_]{0,31}$."
        )
    action_data = _require_mapping(raw_action, context_path)
    command = action_data.get("command")
    if not isinstance(command, str) or not command.strip():
        raise BoardConfigError(f"{context_path}.command is required.")
    if "\n" in command or "\r" in command:
        raise BoardConfigError(f"{context_path}.command must be a single line.")
    if len(command) > 256:
        raise BoardConfigError(
            f"{context_path}.command must be at most 256 characters."
        )
    if any(ord(char) < 32 for char in command):
        raise BoardConfigError(
            f"{context_path}.command must not contain control characters."
        )
    context = str(action_data.get("context", "linux"))
    if context not in CONSOLE_ACTION_CONTEXTS:
        raise BoardConfigError(
            f"{context_path}.context must be one of "
            f"{sorted(CONSOLE_ACTION_CONTEXTS)}."
        )
    parse = str(action_data.get("parse", "lines"))
    if parse not in CONSOLE_ACTION_PARSERS:
        raise BoardConfigError(
            f"{context_path}.parse must be one of "
            f"{sorted(CONSOLE_ACTION_PARSERS)}."
        )
    timeout_s = action_data.get("timeout_s", 5)
    try:
        timeout_s = float(timeout_s)
    except (TypeError, ValueError) as exc:
        raise BoardConfigError(f"{context_path}.timeout_s must be a number.") from exc
    if not 1 <= timeout_s <= 60:
        raise BoardConfigError(
            f"{context_path}.timeout_s must be between 1 and 60."
        )
    mutating = action_data.get("mutating", False)
    if not isinstance(mutating, bool):
        raise BoardConfigError(f"{context_path}.mutating must be a boolean.")
    if not mutating:
        tokens = CONSOLE_MUTATION_TOKENS
        if context == "uboot":
            tokens = tokens + CONSOLE_UBOOT_MUTATION_TOKENS
        for token in tokens:
            if token in command:
                raise BoardConfigError(
                    f"{context_path}.command contains {token.strip()!r}, which "
                    "looks mutating; console actions are meant to be "
                    "read-only. Set 'mutating: true' to load it anyway."
                )
    description = action_data.get("description")
    return ConsoleActionConfig(
        name=name,
        command=command,
        context=context,
        parse=parse,
        timeout_s=timeout_s,
        description=str(description) if description is not None else None,
        mutating=mutating,
    )


def _parse_console_login(raw_login: Any) -> ConsoleLoginConfig:
    login_data = _require_mapping(raw_login, "console.login")
    user = login_data.get("user")
    if not isinstance(user, str) or not user:
        raise BoardConfigError("console.login.user is required.")
    password_file = login_data.get("password_file")
    password_env = login_data.get("password_env")
    if password_file is not None:
        password_file = str(password_file)
        if not Path(password_file).is_absolute():
            raise BoardConfigError(
                "console.login.password_file must be an absolute path."
            )
    if password_env is not None:
        password_env = str(password_env)
    if password_file is not None and password_env is not None:
        raise BoardConfigError(
            "console.login accepts either password_file or password_env, "
            "not both."
        )
    shell_prompt = login_data.get("shell_prompt", "[#$] $")
    shell_prompt = _parse_regex(shell_prompt, "console.login.shell_prompt")
    timeout_s = login_data.get("timeout_s", 10)
    try:
        timeout_s = float(timeout_s)
    except (TypeError, ValueError) as exc:
        raise BoardConfigError(
            "console.login.timeout_s must be a number."
        ) from exc
    if timeout_s <= 0:
        raise BoardConfigError(
            "console.login.timeout_s must be greater than zero."
        )
    logout_after = login_data.get("logout_after", False)
    if not isinstance(logout_after, bool):
        raise BoardConfigError("console.login.logout_after must be a boolean.")
    return ConsoleLoginConfig(
        user=user,
        password_file=password_file,
        password_env=password_env,
        shell_prompt=shell_prompt,
        timeout_s=timeout_s,
        logout_after=logout_after,
    )


def _parse_console_retention(raw_retention: Any) -> ConsoleRetentionConfig:
    retention_data = _require_mapping(raw_retention or {}, "console.retention")
    values: dict[str, int] = {}
    for key, default in (("max_boots", 200), ("max_megabytes", 200)):
        raw_value = retention_data.get(key, default)
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise BoardConfigError(
                f"console.retention.{key} must be an integer."
            )
        if raw_value < 1:
            raise BoardConfigError(
                f"console.retention.{key} must be at least 1."
            )
        values[key] = raw_value
    return ConsoleRetentionConfig(**values)


def _parse_console(raw_console: Any) -> ConsoleConfig | None:
    if raw_console is None:
        return None
    console_data = _require_mapping(raw_console, "console")

    device = console_data.get("device")
    if not isinstance(device, str) or not device:
        raise BoardConfigError("console.device is required.")
    is_url = device.startswith(("rfc2217://", "socket://"))
    if not is_url and not Path(device).is_absolute():
        raise BoardConfigError(
            "console.device must be an absolute device path or a "
            "rfc2217:// or socket:// URL."
        )
    if device.startswith(("/dev/ttyUSB", "/dev/ttyACM")):
        logger.warning(
            "console.device %s is not a stable name; prefer "
            "/dev/serial/by-id/... (see docs/console.md)",
            device,
        )

    baud = console_data.get("baud", 115200)
    if isinstance(baud, bool) or not isinstance(baud, int):
        raise BoardConfigError("console.baud must be an integer.")
    if baud not in STANDARD_BAUD_RATES:
        raise BoardConfigError(
            f"console.baud must be a standard rate, not {baud}."
        )
    data_bits = console_data.get("data_bits", 8)
    if data_bits not in (5, 6, 7, 8):
        raise BoardConfigError("console.data_bits must be between 5 and 8.")
    parity = str(console_data.get("parity", "none"))
    if parity not in ("none", "even", "odd"):
        raise BoardConfigError(
            "console.parity must be one of ['even', 'none', 'odd']."
        )
    stop_bits = console_data.get("stop_bits", 1)
    if stop_bits not in (1, 2):
        raise BoardConfigError("console.stop_bits must be 1 or 2.")

    boot_banner = console_data.get("boot_banner")
    if boot_banner is not None:
        boot_banner = _parse_regex(boot_banner, "console.boot_banner")

    prompts_data = _require_mapping(
        console_data.get("prompts", {}), "console.prompts"
    )
    uboot_prompt = prompts_data.get("uboot")
    if uboot_prompt is not None:
        uboot_prompt = _parse_regex(uboot_prompt, "console.prompts.uboot")
    login_prompt = prompts_data.get("login")
    if login_prompt is not None:
        login_prompt = _parse_regex(login_prompt, "console.prompts.login")

    patterns_data = _require_mapping(
        console_data.get("patterns", {}), "console.patterns"
    )
    # Profile patterns are merged over (prepended to) the built-in defaults.
    error_patterns = DEFAULT_CONSOLE_ERROR_PATTERNS
    if "errors" in patterns_data:
        error_patterns = (
            _parse_regex_list(
                patterns_data["errors"], "console.patterns.errors"
            )
            + DEFAULT_CONSOLE_ERROR_PATTERNS
        )
    warning_patterns = DEFAULT_CONSOLE_WARNING_PATTERNS
    if "warnings" in patterns_data:
        warning_patterns = (
            _parse_regex_list(
                patterns_data["warnings"], "console.patterns.warnings"
            )
            + DEFAULT_CONSOLE_WARNING_PATTERNS
        )

    redact = _parse_regex_list(console_data.get("redact", []), "console.redact")

    retention = _parse_console_retention(console_data.get("retention"))

    mirror_port = console_data.get("mirror_port")
    if mirror_port is not None:
        if isinstance(mirror_port, bool) or not isinstance(mirror_port, int):
            raise BoardConfigError("console.mirror_port must be an integer.")
        if not 1024 <= mirror_port <= 65535:
            raise BoardConfigError(
                "console.mirror_port must be between 1024 and 65535."
            )

    login = None
    if console_data.get("login") is not None:
        login = _parse_console_login(console_data["login"])

    actions = dict(BUILTIN_CONSOLE_ACTIONS)
    actions_data = _require_mapping(
        console_data.get("actions", {}) or {}, "console.actions"
    )
    for action_name, raw_action in actions_data.items():
        action_name = str(action_name)
        if raw_action is None:
            actions.pop(action_name, None)
            continue
        actions[action_name] = _parse_console_action(
            action_name,
            raw_action,
            context_path=f"console.actions.{action_name}",
        )

    return ConsoleConfig(
        device=device,
        baud=baud,
        data_bits=int(data_bits),
        parity=parity,
        stop_bits=int(stop_bits),
        boot_banner=boot_banner,
        uboot_prompt=uboot_prompt,
        login_prompt=login_prompt,
        error_patterns=error_patterns,
        warning_patterns=warning_patterns,
        redact=redact,
        retention=retention,
        mirror_port=mirror_port,
        login=login,
        actions=actions,
    )


def load_board_profile(path: str | Path) -> BoardProfile:
    """Load a board profile from YAML."""

    config_path = Path(path)
    if not config_path.exists():
        raise BoardConfigError(f"Board config not found: {config_path}")

    with config_path.open() as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise BoardConfigError(f"Board config {config_path} must contain a mapping.")

    name = str(data.get("name") or config_path.stem)
    channels_in_use: dict[int, tuple[str, str]] = {}
    signals = _parse_signals(data.get("signals"), channels_in_use=channels_in_use)
    switches = _parse_switches(
        data.get("switches"),
        channels_in_use=channels_in_use,
    )
    timings_data = _require_mapping(data.get("timings", {}), "timings")
    timings = {
        str(key): _parse_duration(value, f"timings.{key}")
        for key, value in timings_data.items()
    }
    defaults = _parse_defaults(
        data.get("defaults"),
        signals=signals,
        switches=switches,
    )
    boot_modes = _parse_boot_modes(
        data.get("boot_modes"),
        signals=signals,
        switches=switches,
    )
    workflows = _parse_workflows(
        data.get("workflows"),
        signals=signals,
        switches=switches,
        timings=timings,
        boot_modes=boot_modes,
    )
    console = _parse_console(data.get("console"))

    return BoardProfile(
        name=name,
        signals=signals,
        switches=switches,
        timings=timings,
        boot_modes=boot_modes,
        workflows=workflows,
        defaults=defaults,
        console=console,
    )
