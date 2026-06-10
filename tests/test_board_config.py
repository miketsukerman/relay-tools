"""Tests for board profile config parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from relay_tools.board_config import (
    DEFAULT_BOARD_CONFIG_ENV,
    DEFAULT_BOARD_SELECTOR_ENV,
    BoardConfigError,
    load_board_profile,
    resolve_board_config_name,
    resolve_default_board_config_path,
)


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
  power_switch: general_power_input
  reset_signal: reset_key
signals:
  reset_key: {channel: 6}
switches:
  sw1003: {channel: 1}
  sw1002: {channel: 2}
  sw1001_2: {channel: 3}
  sw1001_1: {channel: 4}
  general_power_input: {channel: 5}
timings:
  reset_pulse: 0.1
  settle_delay: 0.5
boot_modes:
  emmc:
    switches:
      sw1003: off
      sw1002: off
  usb-recovery:
    risky: true
    switches:
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
    assert profile.switches["sw1002"].channel == 2
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


def test_load_board_profile_rejects_signal_switch_channel_conflict(
    tmp_path: Path,
) -> None:
    path = _write_profile(
        tmp_path,
        """
signals:
  reset_key: {channel: 1}
switches:
  general_power_input: {channel: 1}
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


def test_load_board_profile_rejects_unknown_boot_mode_switch(tmp_path: Path) -> None:
    path = _write_profile(
        tmp_path,
        """
switches:
  sw1003: {channel: 1}
boot_modes:
  emmc:
    switches:
      sw1002: on
""",
    )
    with pytest.raises(BoardConfigError, match="unknown switch"):
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


def test_resolve_board_config_name_from_default_directory() -> None:
    assert (
        resolve_board_config_name("lab")
        == "/etc/relay/boards.d/lab.yaml"
    )


def test_resolve_board_config_name_rejects_separators() -> None:
    with pytest.raises(ValueError, match="must not include path separators"):
        resolve_board_config_name("etc/lab")


def test_resolve_default_board_config_prefers_relay_board_config() -> None:
    path = resolve_default_board_config_path(
        env={
            DEFAULT_BOARD_CONFIG_ENV: "/tmp/explicit.yaml",
            DEFAULT_BOARD_SELECTOR_ENV: "lab",
        }
    )
    assert path == "/tmp/explicit.yaml"


def test_resolve_default_board_config_uses_named_selector() -> None:
    path = resolve_default_board_config_path(
        env={DEFAULT_BOARD_SELECTOR_ENV: "lab"},
        config_dir="/tmp/boards.d",
    )
    assert path == "/tmp/boards.d/lab.yaml"


def test_resolve_default_board_config_uses_selector_path_value() -> None:
    path = resolve_default_board_config_path(
        env={DEFAULT_BOARD_SELECTOR_ENV: "/tmp/custom-board.yaml"},
        config_dir="/tmp/boards.d",
    )
    assert path == "/tmp/custom-board.yaml"


def test_resolve_default_board_config_errors_without_configuration() -> None:
    with pytest.raises(ValueError, match="Could not resolve a board config"):
        resolve_default_board_config_path(env={})
