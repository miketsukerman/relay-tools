"""Tests for board workflow execution."""

from __future__ import annotations

from dataclasses import replace

import pytest

from relay_tools.board import BoardController, BoardExecutionError
from relay_tools.board_config import (
    BoardDefaults,
    BoardProfile,
    BootModeConfig,
    SignalConfig,
    SwitchConfig,
    WorkflowStepConfig,
)
from relay_tools.client import BoardState, ChannelState


class _FakeRelayClient:
    def __init__(self) -> None:
        self.state = {channel: False for channel in range(1, 9)}
        self.calls: list[tuple] = []

    def status(self) -> BoardState:
        return BoardState(
            channels=tuple(
                ChannelState(channel=channel, on=state)
                for channel, state in sorted(self.state.items())
            )
        )

    def get_channel(self, channel: int) -> ChannelState:
        return ChannelState(channel=channel, on=self.state[channel])

    def on(self, channel: int) -> ChannelState:
        self.calls.append(("on", channel))
        self.state[channel] = True
        return self.get_channel(channel)

    def off(self, channel: int) -> ChannelState:
        self.calls.append(("off", channel))
        self.state[channel] = False
        return self.get_channel(channel)

    def press(self, channel: int, duration: float = 0.2) -> ChannelState:
        self.calls.append(("press", channel, duration))
        self.state[channel] = False
        return self.get_channel(channel)


def _profile() -> BoardProfile:
    return BoardProfile(
        name="lab",
        signals={
            "reset_key": SignalConfig("reset_key", 6),
        },
        switches={
            "sw1003": SwitchConfig("sw1003", 1),
            "sw1002": SwitchConfig("sw1002", 2),
            "general_power_input": SwitchConfig("general_power_input", 5),
        },
        timings={
            "reset_pulse": 0.1,
            "settle_delay": 0.5,
            "boot_wait": 1.5,
        },
        boot_modes={
            "emmc": BootModeConfig(
                "emmc",
                signals={},
                switches={"sw1003": False, "sw1002": False},
            ),
            "recovery": BootModeConfig(
                "recovery",
                signals={},
                switches={"sw1003": True, "sw1002": True},
                risky=True,
            ),
        },
        workflows={},
        defaults=BoardDefaults(
            power_switch="general_power_input",
            reset_signal="reset_key",
        ),
    )


def test_set_boot_mode_updates_expected_channels() -> None:
    relay_client = _FakeRelayClient()
    controller = BoardController(_profile(), relay_client)

    result = controller.set_boot_mode("emmc", verify=True)

    assert [call[:2] for call in relay_client.calls] == [("off", 1), ("off", 2)]
    assert result.final_status.switches["sw1003"] is False
    assert result.final_status.matching_boot_modes == ("emmc",)


def test_power_cycle_uses_default_signal_and_delays(monkeypatch) -> None:
    relay_client = _FakeRelayClient()
    controller = BoardController(_profile(), relay_client)
    delays = []
    monkeypatch.setattr(
        "relay_tools.board.time.sleep",
        lambda duration: delays.append(duration),
    )

    controller.power_cycle(verify=False)

    assert relay_client.calls == [("off", 5), ("on", 5)]
    assert delays == [0.5]


def test_risky_boot_mode_requires_force() -> None:
    controller = BoardController(_profile(), _FakeRelayClient())

    with pytest.raises(BoardExecutionError, match="requires --force"):
        controller.set_boot_mode("recovery", verify=False)


def test_verify_failure_is_actionable() -> None:
    relay_client = _FakeRelayClient()
    controller = BoardController(_profile(), relay_client)

    def _wrong_channel_state(channel: int) -> ChannelState:
        return ChannelState(channel=channel, on=False)

    relay_client.get_channel = _wrong_channel_state

    with pytest.raises(BoardExecutionError, match="expected active, got inactive"):
        controller.set_boot_mode("recovery", verify=True, force=True)


def test_flash_internal_memory_runs_named_workflow() -> None:
    relay_client = _FakeRelayClient()
    profile = replace(
        _profile(),
        workflows={
            "flash-internal-memory": (
                WorkflowStepConfig(
                    name="flash-internal-memory:set-sw1003",
                    action="set",
                    switch="sw1003",
                    state=True,
                ),
            )
        },
    )
    controller = BoardController(profile, relay_client)

    result = controller.flash_internal_memory(verify=False)

    assert relay_client.calls == [("on", 1)]
    assert result.final_status.switches["sw1003"] is True
