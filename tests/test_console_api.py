"""Tests for the relay-console HTTP API."""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("serial")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from relay_tools.board_config import (  # noqa: E402
    BoardConfigError,
)
from relay_tools.console import api as api_mod  # noqa: E402
from relay_tools.console import capture as capture_mod  # noqa: E402
from relay_tools.console import session as session_mod  # noqa: E402
from relay_tools.console.api import (  # noqa: E402
    ConsoleManager,
    create_app,
    load_console_boards,
)

from .test_console_session import LOGIN, FakeDUT, _wait_for  # noqa: E402

PROFILE_TEMPLATE = """
name: lab
switches:
  power:
    channel: 1
defaults:
  power_switch: power
console:
  device: {device}
"""


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    monkeypatch.setattr(capture_mod, "RETRY_INITIAL_S", 0.05)
    monkeypatch.setattr(session_mod, "PROBE_WINDOW_S", 0.4)
    monkeypatch.setattr(session_mod, "ACTION_LOCK_TIMEOUT_S", 0.3)


@pytest.fixture()
def shell_board(tmp_path):
    """A manager with one board whose console is a fake Linux shell."""

    master, slave = os.openpty()
    dut = FakeDUT(master, "shell")
    dut.start()
    manager = ConsoleManager(tmp_path / "data")
    boards_dir = tmp_path / "boards.d"
    boards_dir.mkdir()
    (boards_dir / "lab.yaml").write_text(
        PROFILE_TEMPLATE.format(device=os.ttyname(slave))
    )
    profiles = load_console_boards(boards_dir)
    profile = profiles["lab"]
    # Attach a login config so shell-prompt detection works.
    object.__setattr__(profile.console, "login", LOGIN)
    console = manager.add_board("lab", profile)
    manager.start()
    assert _wait_for(lambda: console.connected)
    time.sleep(0.2)
    dut.received = b""
    yield manager, console, master
    dut.stop()
    manager.stop()
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


@pytest.fixture()
def client(shell_board):
    manager, _, _ = shell_board
    return TestClient(create_app(manager))


def test_consoles_listing(client):
    body = client.get("/consoles").json()
    assert len(body["consoles"]) == 1
    entry = body["consoles"][0]
    assert entry["board"] == "lab"
    assert entry["connected"] is True
    assert entry["current_boot"] == 1


def test_unknown_board_404(client):
    assert client.get("/consoles/nope/boots").status_code == 404


def test_marker_endpoint(client):
    response = client.post(
        "/consoles/lab/markers",
        json={"reason": "power-cycle", "actor": "relay-mcp",
              "audit_id": "77"},
    )
    assert response.status_code == 200
    assert response.json()["boot"] == 2
    boots = client.get("/consoles/lab/boots").json()["boots"]
    assert boots[0]["trigger"] == "marker"
    assert boots[0]["reason"] == "power-cycle"
    assert boots[0]["audit_id"] == "77"


def test_boots_limit_validation(client):
    assert client.get("/consoles/lab/boots?limit=0").status_code == 422
    assert client.get("/consoles/lab/boots?limit=201").status_code == 422


def test_log_endpoint_with_grep_and_tail(shell_board, client):
    manager, console, master = shell_board
    for i in range(10):
        os.write(master, f"line {i}\n".encode())
    assert _wait_for(lambda: console.store.current.lines >= 10)

    body = client.get("/consoles/lab/boots/last/log?tail=3").json()
    assert [line["text"] for line in body["untrusted_lines"]] == [
        "line 7", "line 8", "line 9",
    ]
    assert body["total_lines"] >= 10

    body = client.get("/consoles/lab/boots/last/log?grep=line%205").json()
    assert [line["text"] for line in body["untrusted_lines"]] == ["line 5"]

    body = client.get("/consoles/lab/boots/1/log").json()
    assert body["boot"] == 1

    assert (
        client.get("/consoles/lab/boots/last/log?tail=99999").status_code
        == 422
    )
    assert (
        client.get("/consoles/lab/boots/last/log?grep=(").status_code == 400
    )
    assert client.get("/consoles/lab/boots/abc/log").status_code == 400
    assert client.get("/consoles/lab/boots/999/log").status_code == 404


def test_report_endpoint(shell_board, client):
    manager, console, master = shell_board
    os.write(master, b"U-Boot 2025.04\nStarting kernel ...\nimx93 login:\n")
    assert _wait_for(lambda: console.store.current.lines >= 3)
    body = client.get("/consoles/lab/boots/last/report").json()
    assert body["result"] in ("login_reached", "in_progress")
    assert body["board"] == "lab"
    assert body["uboot"] == "U-Boot 2025.04"


def test_wait_endpoint_match_and_timeout(shell_board, client):
    manager, console, master = shell_board
    import threading

    def _feed():
        time.sleep(0.1)
        os.write(master, b"imx93 login:\n")

    threading.Thread(target=_feed).start()
    body = client.get("/consoles/lab/wait?pattern=login:&timeout_s=5").json()
    assert body["matched"] is True

    body = client.get("/consoles/lab/wait?pattern=zzz&timeout_s=0.2").json()
    assert body["matched"] is False
    assert client.get(
        "/consoles/lab/wait?pattern=x&timeout_s=9999"
    ).status_code == 422


def test_actions_listing(client):
    body = client.get("/consoles/lab/actions").json()
    names = {action["name"] for action in body["actions"]}
    assert "ip_address" in names
    assert "uboot_net" in names


def test_state_endpoint(client):
    body = client.post("/consoles/lab/state").json()
    assert body["state"] == "linux_shell"
    assert "untrusted_lines" in body


def test_run_action_endpoint(client):
    response = client.post(
        "/consoles/lab/actions/ip_address",
        headers={"X-Relay-Actor": "relay-mcp", "X-Relay-Audit-Id": "9"},
    )
    body = response.json()
    assert body["parsed"][0]["address"] == "192.168.3.141"
    assert body["exit_code"] == 0

    assert client.post("/consoles/lab/actions/nope").status_code == 404


def test_run_action_wrong_context_is_structured(client):
    body = client.post("/consoles/lab/actions/uboot_net").json()
    assert body["error"] == "wrong_context"
    assert "in Linux" in body["message"]


def test_token_enforcement(shell_board):
    manager, _, _ = shell_board
    secured = TestClient(create_app(manager, token="s3cret"))
    assert secured.get("/consoles").status_code == 401
    assert (
        secured.get(
            "/consoles", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert (
        secured.get(
            "/consoles", headers={"Authorization": "Bearer s3cret"}
        ).status_code
        == 200
    )


def test_main_refuses_non_loopback_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("RELAY_CONSOLE_TOKEN", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        api_mod.main(
            ["--host", "0.0.0.0", "--boards-dir", str(tmp_path)]
        )
    assert excinfo.value.code == 2


def test_load_console_boards_skips_profiles_without_console(tmp_path):
    (tmp_path / "plain.yaml").write_text(
        "name: plain\nswitches:\n  p:\n    channel: 1\n"
    )
    (tmp_path / "con.yaml").write_text(
        PROFILE_TEMPLATE.format(device="/dev/pts/5").replace(
            "name: lab", "name: con"
        )
    )
    boards = load_console_boards(tmp_path)
    assert set(boards) == {"con"}


def test_load_console_boards_rejects_duplicate_mirror_ports(tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.yaml").write_text(
            f"name: {name}\nswitches:\n  p:\n    channel: 1\n"
            "console:\n  device: /dev/pts/5\n  mirror_port: 7001\n"
        )
    with pytest.raises(BoardConfigError) as excinfo:
        load_console_boards(tmp_path)
    assert "mirror_port" in str(excinfo.value)


def test_board_without_console_block_not_served(tmp_path):
    manager = ConsoleManager(tmp_path)
    client = TestClient(create_app(manager))
    assert client.get("/consoles").json() == {"consoles": []}
    assert client.post(
        "/consoles/lab/state"
    ).status_code == 404
