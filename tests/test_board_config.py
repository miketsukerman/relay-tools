"""Tests for board profile config parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from relay_tools.board_config import BoardConfigError, load_board_profile


def _write_profile(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "board.yaml"
    path.write_text(content)
    return path


def test_load_board_profile_parses_valid_config(tmp_path: Path) -> None:
    profile = load_board_profile(
        _write_profile(
            tmp_path,
            """
name: rom2820
defaults:
  power_signal: power_key
  reset_signal: reset_key
signals:
  sw1003: {channel: 1}
  sw1002: {channel: 2}
  sw1001_2: {channel: 3}
  sw1001_1: {channel: 4}
  power_key: {channel: 5}
  reset_key: {channel: 6}
timings:
  power_on_pulse: 0.2
  power_off_pulse: 1.5
  reset_pulse: 0.1
  settle_delay: 0.5
boot_modes:
  emmc:
    signals:
      sw1003: off
      sw1002: off
  usb-recovery:
    risky: true
    signals:
      sw1003: on
      sw1002: on
workflows:
  enter-recovery:
    - action: set-boot-mode
      boot_mode: usb-recovery
    - action: pulse
      signal: reset_key
      timing: reset_pulse
""",
        )
    )

    assert profile.name == "rom2820"
    assert profile.signals["sw1002"].channel == 2
    assert profile.boot_modes["usb-recovery"].risky is True
    assert profile.workflows["enter-recovery"][1].timing == "reset_pulse"


def test_load_board_profile_rejects_conflicting_signal_channels(tmp_path: Path) -> None:
    path = _write_profile(
        tmp_path,
        """
signals:
  one: {channel: 1}
  two: {channel: 1}
""",
    )
    with pytest.raises(BoardConfigError, match="conflicts"):
        load_board_profile(path)


def test_load_board_profile_rejects_unknown_boot_mode_signal(tmp_path: Path) -> None:
    path = _write_profile(
        tmp_path,
        """
signals:
  sw1003: {channel: 1}
boot_modes:
  emmc:
    signals:
      sw1002: on
""",
    )
    with pytest.raises(BoardConfigError, match="unknown signal"):
        load_board_profile(path)


def test_load_board_profile_rejects_unknown_workflow_timing(tmp_path: Path) -> None:
    path = _write_profile(
        tmp_path,
        """
signals:
  power_key: {channel: 1}
workflows:
  power-on:
    - action: pulse
      signal: power_key
      timing: missing
""",
    )
    with pytest.raises(BoardConfigError, match="unknown timing"):
        load_board_profile(path)
