"""Tests for boot markers posted by BoardController."""

from __future__ import annotations

from pathlib import Path

import pytest

from relay_tools.board import BoardController
from relay_tools.board_config import load_board_profile
from relay_tools.client import BoardState, ChannelState

PROFILE_YAML = """
name: lab
switches:
  power:
    channel: 1
  boot0:
    channel: 2
signals:
  reset_key:
    channel: 3
timings:
  reset_pulse: 0.01
  settle_delay: 0.01
  boot_wait: 0.01
defaults:
  power_switch: power
  reset_signal: reset_key
boot_modes:
  emmc:
    switches:
      boot0: off
workflows:
  power-cycle:
    - name: cut-power
      action: set
      switch: power
      state: off
    - name: restore-power
      action: set
      switch: power
      state: on
  toggle-flag:
    - name: flag-off
      action: set
      switch: boot0
      state: off
console:
  device: /dev/pts/5
"""


class _FakeRelayClient:
    def __init__(self, journal: list) -> None:
        self.state = {channel: False for channel in range(1, 9)}
        self.journal = journal

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
        self.journal.append(("relay-on", channel))
        self.state[channel] = True
        return self.get_channel(channel)

    def off(self, channel: int) -> ChannelState:
        self.journal.append(("relay-off", channel))
        self.state[channel] = False
        return self.get_channel(channel)

    def press(self, channel: int, duration: float) -> ChannelState:
        self.journal.append(("relay-press", channel))
        return self.get_channel(channel)


class _FakeConsoleClient:
    def __init__(self, journal: list, *, fail: bool = False) -> None:
        self.journal = journal
        self.fail = fail

    def post_marker(self, board, *, reason, actor, audit_id=None):
        if self.fail:
            raise ConnectionError("relay-console unreachable")
        self.journal.append(("marker", board, reason, actor, audit_id))
        return {"board": board, "boot": 1}


@pytest.fixture()
def profile(tmp_path: Path):
    path = tmp_path / "lab.yaml"
    path.write_text(PROFILE_YAML)
    return load_board_profile(path)


def _controller(profile, journal, *, console=True, fail=False, **kwargs):
    relay = _FakeRelayClient(journal)
    console_client = (
        _FakeConsoleClient(journal, fail=fail) if console else None
    )
    return BoardController(
        profile, relay, console_client=console_client, **kwargs
    )


def test_marker_posted_before_power_step_once(profile):
    journal: list = []
    controller = _controller(profile, journal, board_name="lab")
    controller.power_cycle(verify=False)
    markers = [entry for entry in journal if entry[0] == "marker"]
    assert len(markers) == 1
    assert markers[0][1:] == ("lab", "power-cycle", "relay-board", None)
    # The marker comes before the step that switches power on.
    marker_index = journal.index(markers[0])
    power_on_index = journal.index(("relay-on", 1))
    assert marker_index < power_on_index


def test_marker_for_reset(profile):
    journal: list = []
    controller = _controller(profile, journal)
    controller.reset(verify=False)
    markers = [entry for entry in journal if entry[0] == "marker"]
    assert len(markers) == 1
    assert journal.index(markers[0]) < journal.index(("relay-press", 3))


def test_marker_for_boot_and_wait_uses_mode_reason(profile):
    journal: list = []
    controller = _controller(profile, journal)
    controller.boot_and_wait("emmc", verify=False)
    markers = [entry for entry in journal if entry[0] == "marker"]
    assert len(markers) == 1
    assert markers[0][2] == "boot-emmc"


def test_no_marker_for_non_boot_workflow(profile):
    journal: list = []
    controller = _controller(profile, journal)
    controller.run_workflow("toggle-flag", verify=False)
    assert not [entry for entry in journal if entry[0] == "marker"]


def test_no_marker_without_console_client(profile):
    journal: list = []
    controller = _controller(profile, journal, console=False)
    controller.power_cycle(verify=False)
    assert not [entry for entry in journal if entry[0] == "marker"]


def test_no_marker_without_console_block(profile, tmp_path):
    path = tmp_path / "plain.yaml"
    path.write_text(PROFILE_YAML.split("console:")[0])
    plain = load_board_profile(path)
    journal: list = []
    controller = _controller(plain, journal)
    controller.power_cycle(verify=False)
    assert not [entry for entry in journal if entry[0] == "marker"]


def test_unreachable_console_does_not_fail_action(profile):
    journal: list = []
    controller = _controller(profile, journal, fail=True)
    result = controller.power_cycle(verify=False)
    assert ("relay-on", 1) in journal
    assert result.final_status.switches["power"] is True


def test_actor_and_audit_id_passed(profile):
    journal: list = []
    controller = _controller(
        profile, journal, actor="relay-mcp", audit_id="abc123",
        board_name="lab-file",
    )
    controller.power_cycle(verify=False)
    markers = [entry for entry in journal if entry[0] == "marker"]
    assert markers[0][1:] == (
        "lab-file", "power-cycle", "relay-mcp", "abc123",
    )
