"""Channel configuration loader for relay-tools.

The YAML format expected at the config path::

    channels:
      1: on    # or true / false / off
      2: off
      3: on

Channels not listed default to off.  An absent or empty file is treated
as "all channels off" without raising an error.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Accepted truthy/falsy literals (case-insensitive strings or native booleans).
_TRUTHY: frozenset[Any] = frozenset({"on", "true", "yes", "1", True, 1})
_FALSY: frozenset[Any] = frozenset({"off", "false", "no", "0", False, 0})


def _parse_state(value: Any, channel: int) -> bool:
    """Convert a config value to a bool relay state.

    Args:
        value: Raw value from the YAML file.
        channel: Channel number (used only for error messages).

    Returns:
        ``True`` if the channel should be on, ``False`` if off.

    Raises:
        ValueError: If *value* is not a recognised state string or boolean.
    """
    normalised = value.strip().lower() if isinstance(value, str) else value
    if normalised in _TRUTHY:
        return True
    if normalised in _FALSY:
        return False
    raise ValueError(
        f"Invalid state {value!r} for channel {channel}. "
        "Use 'on'/'off', 'true'/'false', or a YAML boolean."
    )


def load_channel_config(path: str | Path) -> dict[int, bool]:
    """Load per-channel initial states from a YAML configuration file.

    If the file does not exist the function returns an empty dict so that
    the daemon starts cleanly with all channels off without requiring the
    operator to create a config file first.

    Args:
        path: Filesystem path to the YAML configuration file.

    Returns:
        A mapping of ``{channel_number: initial_state}`` for every channel
        explicitly listed in the file.  Channels absent from the file are
        *not* included; callers should treat missing entries as ``False``.

    Raises:
        ValueError: If the file contains an invalid channel key or state
            value.
        yaml.YAMLError: If the file is not valid YAML.
    """
    p = Path(path)
    if not p.exists():
        logger.debug(
            "Channel config not found at %s; all channels default to off.", p
        )
        return {}

    with p.open() as fh:
        data = yaml.safe_load(fh)

    if data is None:
        logger.debug(
            "Channel config %s is empty; all channels default to off.", p
        )
        return {}

    channels_raw: dict[Any, Any] = data.get("channels") or {}
    result: dict[int, bool] = {}
    for key, val in channels_raw.items():
        try:
            ch = int(key)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Invalid channel key {key!r} in config {p}. "
                "Channel numbers must be integers."
            ) from exc
        result[ch] = _parse_state(val, ch)

    logger.debug("Loaded channel config from %s: %s", p, result)
    return result
