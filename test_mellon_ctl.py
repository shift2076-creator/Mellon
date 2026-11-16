import importlib.util
import socket
import subprocess
import threading

import pytest

spec = importlib.util.spec_from_file_location("ctl", "mellon-ctl.py")
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(cmd):
        seen.append(cmd)
        out = "Volume: 0.40 [MUTED]\n" if cmd[1] == "get-volume" else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(ctl, "run", fake)
    return seen


def test_volume_set_and_clamp(calls):
    assert ctl.handle("VOL 40\n").startswith("S ")
    assert ["wpctl", "set-volume", ctl.SINK, "0.40"] in calls
    ctl.handle("VOL 250")
    assert ["wpctl", "set-volume", ctl.SINK, "1.00"] in calls


def test_mic_uses_source_and_mute_toggles(calls):
    ctl.handle("MIC 70")
    ctl.handle("MIC MUTE")
    assert ["wpctl", "set-volume", ctl.SOURCE, "0.70"] in calls
    assert ["wpctl", "set-mute", ctl.SOURCE, "toggle"] in calls


def test_state_reports_level_and_mute(calls):
    assert ctl.state() == "S 40 1 40 1"


@pytest.mark.parametrize("line", ["", "VOL", "VOL abc", "VOL -5", "VOL ٣", "VOL 1 2",
                                  "SHUTDOWN now", "rm -rf /", "shutdown", "GET x"])
def test_bad_input_is_refused_and_runs_nothing(calls, line):
    assert ctl.handle(line) == "ERR"
    assert calls == []


@pytest.mark.parametrize("name", list(ctl.ACTIONS))
def test_power_actions_run_their_fixed_command(calls, name):
    assert ctl.handle(name) == "OK"
    assert calls == [ctl.ACTIONS[name]]


def test_power_failure_reports_err(monkeypatch):
    monkeypatch.setattr(ctl, "run", lambda c: subprocess.CompletedProcess(c, 1, "", ""))
    assert ctl.handle("SLEEP") == "ERR"
    monkeypatch.setattr(ctl, "run", lambda c: None)
    assert ctl.handle("LOCK") == "ERR"


def test_missing_wpctl_yields_zero_state(monkeypatch):
    monkeypatch.setattr(ctl, "run", lambda c: None)
    assert ctl.state() == "S 0 0 0 0"


def test_serve_answers_and_stops_on_eof(calls):
    a, b = socket.socketpair()
    t = threading.Thread(target=ctl.serve, args=(a,))
    t.start()
    f = b.makefile("rw", newline="\n")
    f.write("GET\n"); f.flush()
    assert f.readline().startswith("S ")
    f.write("BOGUS\n"); f.flush()
    assert f.readline() == "ERR\n"
    b.shutdown(socket.SHUT_WR)
    t.join(2)
    assert not t.is_alive()


def test_serve_drops_overlong_line(calls):
    a, b = socket.socketpair()
    t = threading.Thread(target=ctl.serve, args=(a,))
    t.start()
    b.sendall(b"X" * 200)
    t.join(2)
    assert not t.is_alive()
    assert calls == []
