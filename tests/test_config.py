"""Tests for relay_tools.config — YAML channel config loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from relay_tools.config import load_channel_config


class TestLoadChannelConfig:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        result = load_channel_config(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("")
        assert load_channel_config(f) == {}

    def test_no_channels_key_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("something_else: 1\n")
        assert load_channel_config(f) == {}

    def test_on_off_strings(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  1: on\n  2: off\n")
        result = load_channel_config(f)
        assert result == {1: True, 2: False}

    def test_true_false_strings(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  3: 'true'\n  4: 'false'\n")
        result = load_channel_config(f)
        assert result == {3: True, 4: False}

    def test_yaml_booleans(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  5: true\n  6: false\n")
        result = load_channel_config(f)
        assert result == {5: True, 6: False}

    def test_yes_no_strings(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  7: yes\n  8: no\n")
        result = load_channel_config(f)
        assert result == {7: True, 8: False}

    def test_numeric_string_1_and_0(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  1: '1'\n  2: '0'\n")
        result = load_channel_config(f)
        assert result == {1: True, 2: False}

    def test_case_insensitive_states(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  1: ON\n  2: OFF\n")
        result = load_channel_config(f)
        assert result == {1: True, 2: False}

    def test_partial_channels_only_lists_present(self, tmp_path: Path) -> None:
        """Channels absent from the file must not appear in the result."""
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  1: on\n  3: on\n")
        result = load_channel_config(f)
        assert result == {1: True, 3: True}
        assert 2 not in result

    def test_invalid_state_raises_value_error(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  1: maybe\n")
        with pytest.raises(ValueError, match="Invalid state"):
            load_channel_config(f)

    def test_invalid_channel_key_raises_value_error(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  relay_one: on\n")
        with pytest.raises(ValueError, match="Invalid channel key"):
            load_channel_config(f)

    def test_invalid_yaml_raises_yaml_error(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels: [\n  unclosed bracket\n")
        with pytest.raises(yaml.YAMLError):
            load_channel_config(f)

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  1: on\n")
        result = load_channel_config(f)
        assert result == {1: True}

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n  2: off\n")
        result = load_channel_config(str(f))
        assert result == {2: False}

    def test_channels_null_value_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "channels.yaml"
        f.write_text("channels:\n")
        assert load_channel_config(f) == {}
