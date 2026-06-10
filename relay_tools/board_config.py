"""Board profile configuration for board-level relay workflows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from relay_tools.config import _parse_state

DEFAULT_BOARD_CONFIG_DIR = Path("/etc/relay/boards.d")
DEFAULT_BOARD_CONFIG_ENV = "RELAY_BOARD_CONFIG"
DEFAULT_BOARD_SELECTOR_ENV = "RELAY_BOARD_DEFAULT"
SUPPORTED_WORKFLOW_ACTIONS = frozenset(
    {"set", "pulse", "delay", "verify", "set-boot-mode"}
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
class BoardProfile:
    """Validated board profile."""

    name: str
    signals: dict[str, SignalConfig]
    switches: dict[str, SwitchConfig]
    timings: dict[str, float]
    boot_modes: dict[str, BootModeConfig]
    workflows: dict[str, tuple[WorkflowStepConfig, ...]]
    defaults: BoardDefaults


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

    return BoardProfile(
        name=name,
        signals=signals,
        switches=switches,
        timings=timings,
        boot_modes=boot_modes,
        workflows=workflows,
        defaults=defaults,
    )
