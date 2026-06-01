"""Board-level relay workflows built on top of the HTTP relay client."""

from __future__ import annotations

import time
from dataclasses import dataclass

from relay_tools.board_config import (
    BoardProfile,
    SignalConfig,
    SwitchConfig,
    WorkflowStepConfig,
)
from relay_tools.client import BoardState, RelayClient


class BoardExecutionError(RuntimeError):
    """Raised when a board workflow step fails."""

    def __init__(
        self,
        *,
        step_name: str,
        message: str,
        expected: str | None = None,
        actual: str | None = None,
        recovery_hint: str | None = None,
    ) -> None:
        self.step_name = step_name
        self.expected = expected
        self.actual = actual
        self.recovery_hint = recovery_hint
        parts = [f"{step_name}: {message}"]
        if expected is not None and actual is not None:
            parts.append(f"expected {expected}, got {actual}")
        if recovery_hint:
            parts.append(f"hint: {recovery_hint}")
        super().__init__("; ".join(parts))


@dataclass(frozen=True)
class ExecutedStep:
    """Observable workflow step result."""

    name: str
    detail: str


@dataclass(frozen=True)
class BoardStatus:
    """Observed state of the board profile signals and switches."""

    relay_state: BoardState
    signals: dict[str, bool]
    switches: dict[str, bool]
    matching_boot_modes: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowResult:
    """Executed workflow summary."""

    steps: tuple[ExecutedStep, ...]
    final_status: BoardStatus


class BoardController:
    """Translate board intents into relay channel operations."""

    def __init__(self, profile: BoardProfile, relay_client: RelayClient) -> None:
        self.profile = profile
        self.relay_client = relay_client

    def status(self) -> BoardStatus:
        relay_state = self.relay_client.status()
        signal_state = {
            name: self._target_active(signal, relay_state.by_channel)
            for name, signal in self.profile.signals.items()
        }
        switch_state = {
            name: self._target_active(switch, relay_state.by_channel)
            for name, switch in self.profile.switches.items()
        }
        matching_boot_modes = tuple(
            mode_name
            for mode_name, boot_mode in self.profile.boot_modes.items()
            if all(
                signal_state[signal_name] == expected
                for signal_name, expected in boot_mode.signals.items()
            )
            and all(
                switch_state[switch_name] == expected
                for switch_name, expected in boot_mode.switches.items()
            )
        )
        return BoardStatus(
            relay_state=relay_state,
            signals=signal_state,
            switches=switch_state,
            matching_boot_modes=matching_boot_modes,
        )

    def set_boot_mode(
        self,
        mode_name: str,
        *,
        verify: bool,
        force: bool = False,
    ) -> WorkflowResult:
        return self._execute_steps(
            self._boot_mode_steps(mode_name, force=force),
            verify=verify,
            force=force,
        )

    def _boot_mode_steps(
        self,
        mode_name: str,
        *,
        force: bool,
    ) -> tuple[WorkflowStepConfig, ...]:
        if mode_name not in self.profile.boot_modes:
            raise BoardExecutionError(
                step_name="set-boot-mode",
                message=f"unknown boot mode {mode_name!r}",
                recovery_hint=(
                    "Available modes: "
                    + (", ".join(sorted(self.profile.boot_modes)) or "none")
                ),
            )
        mode = self.profile.boot_modes[mode_name]
        if mode.risky and not force:
            raise BoardExecutionError(
                step_name="set-boot-mode",
                message=f"boot mode {mode_name!r} requires --force",
                recovery_hint="Re-run with --force after confirming the board manual.",
            )
        return tuple(
            [
                *(
                    WorkflowStepConfig(
                        name=f"set-boot-mode:{mode_name}:{signal_name}",
                        action="set",
                        signal=signal_name,
                        state=state,
                    )
                    for signal_name, state in mode.signals.items()
                ),
                *(
                    WorkflowStepConfig(
                        name=f"set-boot-mode:{mode_name}:{switch_name}",
                        action="set",
                        switch=switch_name,
                        state=state,
                    )
                    for switch_name, state in mode.switches.items()
                ),
            ]
        )

    def run_workflow(
        self,
        workflow_name: str,
        *,
        verify: bool,
        force: bool = False,
    ) -> WorkflowResult:
        if workflow_name not in self.profile.workflows:
            raise BoardExecutionError(
                step_name=workflow_name,
                message="workflow is not defined for this board profile",
                recovery_hint=(
                    "Available workflows: "
                    + (", ".join(sorted(self.profile.workflows)) or "none")
                ),
            )
        return self._execute_steps(
            self.profile.workflows[workflow_name],
            verify=verify,
            force=force,
        )

    def power_on(self, *, verify: bool, force: bool = False) -> WorkflowResult:
        if "power-on" in self.profile.workflows:
            return self.run_workflow("power-on", verify=verify, force=force)
        if self.profile.defaults.power_switch:
            return self._set_default_switch(
                "power-on",
                self.profile.defaults.power_switch,
                True,
                verify=verify,
            )
        return self._pulse_default_signal(
            "power-on",
            self.profile.defaults.power_signal,
            "power_on_pulse",
            verify=verify,
        )

    def power_off(self, *, verify: bool, force: bool = False) -> WorkflowResult:
        if "power-off" in self.profile.workflows:
            return self.run_workflow("power-off", verify=verify, force=force)
        if self.profile.defaults.power_switch:
            return self._set_default_switch(
                "power-off",
                self.profile.defaults.power_switch,
                False,
                verify=verify,
            )
        return self._pulse_default_signal(
            "power-off",
            self.profile.defaults.power_signal,
            "power_off_pulse",
            verify=verify,
        )

    def reset(self, *, verify: bool, force: bool = False) -> WorkflowResult:
        if "reset" in self.profile.workflows:
            return self.run_workflow("reset", verify=verify, force=force)
        return self._pulse_default_signal(
            "reset",
            self.profile.defaults.reset_signal,
            "reset_pulse",
            verify=verify,
        )

    def power_cycle(self, *, verify: bool, force: bool = False) -> WorkflowResult:
        if "power-cycle" in self.profile.workflows:
            return self.run_workflow("power-cycle", verify=verify, force=force)
        steps = []
        if self.profile.defaults.power_switch:
            steps.extend(
                self._default_switch_steps(
                    "power-off",
                    self.profile.defaults.power_switch,
                    False,
                )
            )
        else:
            steps.extend(
                self._default_power_steps(
                    "power-off",
                    self.profile.defaults.power_signal,
                    "power_off_pulse",
                )
            )
        settle_delay = self.profile.timings.get("settle_delay")
        if settle_delay is not None:
            steps.append(
                WorkflowStepConfig(
                    name="power-cycle:settle-delay",
                    action="delay",
                    duration=settle_delay,
                )
            )
        if self.profile.defaults.power_switch:
            steps.extend(
                self._default_switch_steps(
                    "power-on",
                    self.profile.defaults.power_switch,
                    True,
                )
            )
        else:
            steps.extend(
                self._default_power_steps(
                    "power-on",
                    self.profile.defaults.power_signal,
                    "power_on_pulse",
                )
            )
        return self._execute_steps(tuple(steps), verify=verify, force=force)

    def boot_and_wait(
        self,
        mode_name: str,
        *,
        verify: bool,
        force: bool = False,
    ) -> WorkflowResult:
        if "boot-and-wait" in self.profile.workflows:
            return self.run_workflow("boot-and-wait", verify=verify, force=force)
        steps = [
            WorkflowStepConfig(
                name=f"boot-and-wait:set-boot-mode:{mode_name}",
                action="set-boot-mode",
                boot_mode=mode_name,
            )
        ]
        if "power-cycle" in self.profile.workflows:
            steps.extend(self.profile.workflows["power-cycle"])
        else:
            if self.profile.defaults.power_switch:
                steps.extend(
                    self._default_switch_steps(
                        "power-off",
                        self.profile.defaults.power_switch,
                        False,
                    )
                )
            else:
                steps.extend(
                    self._default_power_steps(
                        "power-off",
                        self.profile.defaults.power_signal,
                        "power_off_pulse",
                    )
                )
            settle_delay = self.profile.timings.get("settle_delay")
            if settle_delay is not None:
                steps.append(
                    WorkflowStepConfig(
                        name="boot-and-wait:settle-delay",
                        action="delay",
                        duration=settle_delay,
                    )
                )
            if self.profile.defaults.power_switch:
                steps.extend(
                    self._default_switch_steps(
                        "power-on",
                        self.profile.defaults.power_switch,
                        True,
                    )
                )
            else:
                steps.extend(
                    self._default_power_steps(
                        "power-on",
                        self.profile.defaults.power_signal,
                        "power_on_pulse",
                    )
                )
        boot_wait = self.profile.timings.get("boot_wait")
        if boot_wait is not None:
            steps.append(
                WorkflowStepConfig(
                    name="boot-and-wait:boot-delay",
                    action="delay",
                    duration=boot_wait,
                )
            )
        return self._execute_steps(tuple(steps), verify=verify, force=force)

    def _pulse_default_signal(
        self,
        step_name: str,
        signal_name: str | None,
        timing_name: str,
        *,
        verify: bool,
    ) -> WorkflowResult:
        steps = self._default_power_steps(step_name, signal_name, timing_name)
        return self._execute_steps(tuple(steps), verify=verify, force=False)

    def _set_default_switch(
        self,
        step_name: str,
        switch_name: str | None,
        state: bool,
        *,
        verify: bool,
    ) -> WorkflowResult:
        steps = self._default_switch_steps(step_name, switch_name, state)
        return self._execute_steps(tuple(steps), verify=verify, force=False)

    def _default_power_steps(
        self,
        step_name: str,
        signal_name: str | None,
        timing_name: str,
    ) -> list[WorkflowStepConfig]:
        if not signal_name:
            raise BoardExecutionError(
                step_name=step_name,
                message="required signal mapping is missing from the board profile",
                recovery_hint=(
                    "Add the signal mapping to the board config or define "
                    "the workflow explicitly."
                ),
            )
        if timing_name not in self.profile.timings:
            raise BoardExecutionError(
                step_name=step_name,
                message=(
                    f"required timing {timing_name!r} is missing from "
                    "the board profile"
                ),
                recovery_hint=(
                    "Add the timing to the board config or define the "
                    "workflow explicitly."
                ),
            )
        return [
            WorkflowStepConfig(
                name=step_name,
                action="pulse",
                signal=signal_name,
                timing=timing_name,
            )
        ]

    def _default_switch_steps(
        self,
        step_name: str,
        switch_name: str | None,
        state: bool,
    ) -> list[WorkflowStepConfig]:
        if not switch_name:
            raise BoardExecutionError(
                step_name=step_name,
                message="required switch mapping is missing from the board profile",
                recovery_hint=(
                    "Add the switch mapping to the board config or define "
                    "the workflow explicitly."
                ),
            )
        return [
            WorkflowStepConfig(
                name=step_name,
                action="set",
                switch=switch_name,
                state=state,
            )
        ]

    def _execute_steps(
        self,
        steps: tuple[WorkflowStepConfig, ...],
        *,
        verify: bool,
        force: bool,
    ) -> WorkflowResult:
        executed_steps: list[ExecutedStep] = []
        for step in steps:
            if step.action == "set-boot-mode":
                nested_steps = self._boot_mode_steps(
                    step.boot_mode or "",
                    force=force,
                )
                for nested_step in nested_steps:
                    executed_steps.append(
                        self._execute_step(nested_step, verify=verify)
                    )
                continue
            executed_steps.append(self._execute_step(step, verify=verify))

        return WorkflowResult(steps=tuple(executed_steps), final_status=self.status())

    def _execute_step(
        self,
        step: WorkflowStepConfig,
        *,
        verify: bool,
    ) -> ExecutedStep:
        if step.action == "set":
            return self._set_target(step, verify=verify)
        if step.action == "pulse":
            return self._pulse_signal(step, verify=verify)
        if step.action == "delay":
            duration = self._resolve_duration(step)
            time.sleep(duration)
            return ExecutedStep(step.name, f"delay {duration:.3f}s")
        if step.action == "verify":
            return self._verify_target(step)
        raise BoardExecutionError(
            step_name=step.name,
            message="unsupported step action",
        )

    def _resolve_duration(self, step: WorkflowStepConfig) -> float:
        if step.duration is not None:
            return step.duration
        if step.timing is not None:
            return self.profile.timings[step.timing]
        raise BoardExecutionError(step_name=step.name, message="duration is missing")

    def _set_target(self, step: WorkflowStepConfig, *, verify: bool) -> ExecutedStep:
        target, target_kind = self._get_target(step)
        relay_on = target.active if step.state else not target.active
        self._set_relay_state(target.channel, relay_on)
        if verify:
            self._assert_target_state(
                target,
                expected=bool(step.state),
                step_name=step.name,
                recovery_hint=step.recovery_hint,
                target_kind=target_kind,
            )
        state_label = "active" if step.state else "inactive"
        return ExecutedStep(
            step.name,
            f"set {target_kind} {target.name} {state_label} (channel {target.channel})",
        )

    def _pulse_signal(self, step: WorkflowStepConfig, *, verify: bool) -> ExecutedStep:
        signal = self._get_signal(step)
        duration = self._resolve_duration(step)
        if signal.active:
            self.relay_client.press(signal.channel, duration)
        else:
            self._set_relay_state(signal.channel, False)
            time.sleep(duration)
            self._set_relay_state(signal.channel, True)
        if verify:
            self._assert_target_state(
                signal,
                expected=False,
                step_name=step.name,
                recovery_hint=step.recovery_hint,
                target_kind="signal",
            )
        return ExecutedStep(
            step.name,
            f"pulse {signal.name} for {duration:.3f}s (channel {signal.channel})",
        )

    def _verify_target(self, step: WorkflowStepConfig) -> ExecutedStep:
        target, target_kind = self._get_target(step)
        self._assert_target_state(
            target,
            expected=bool(step.state),
            step_name=step.name,
            recovery_hint=step.recovery_hint,
            target_kind=target_kind,
        )
        state_label = "active" if step.state else "inactive"
        return ExecutedStep(
            step.name,
            f"verified {target_kind} {target.name} is {state_label}",
        )

    def _assert_target_state(
        self,
        target: SignalConfig | SwitchConfig,
        *,
        expected: bool,
        step_name: str,
        recovery_hint: str | None,
        target_kind: str,
    ) -> None:
        observed = self.relay_client.get_channel(target.channel).on
        actual = observed == target.active
        if actual != expected:
            raise BoardExecutionError(
                step_name=step_name,
                message=(
                    f"{target_kind} {target.name!r} did not reach the requested state"
                ),
                expected="active" if expected else "inactive",
                actual="active" if actual else "inactive",
                recovery_hint=recovery_hint
                or (
                    f"Check relay channel {target.channel} and wiring "
                    f"for {target.name}."
                ),
            )

    def _get_signal(self, step: WorkflowStepConfig) -> SignalConfig:
        if not step.signal or step.signal not in self.profile.signals:
            raise BoardExecutionError(
                step_name=step.name,
                message=f"unknown signal {step.signal!r}",
            )
        return self.profile.signals[step.signal]

    def _get_target(
        self,
        step: WorkflowStepConfig,
    ) -> tuple[SignalConfig | SwitchConfig, str]:
        if step.signal is not None:
            return self._get_signal(step), "signal"
        if not step.switch or step.switch not in self.profile.switches:
            raise BoardExecutionError(
                step_name=step.name,
                message=f"unknown switch {step.switch!r}",
            )
        return self.profile.switches[step.switch], "switch"

    def _set_relay_state(self, channel: int, relay_on: bool) -> None:
        if relay_on:
            self.relay_client.on(channel)
        else:
            self.relay_client.off(channel)

    @staticmethod
    def _target_active(
        target: SignalConfig | SwitchConfig,
        channel_state: dict[int, bool],
    ) -> bool:
        relay_on = channel_state.get(target.channel, False)
        return relay_on == target.active
