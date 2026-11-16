import importlib.util
import json
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


LIST = ("org.freedesktop.DBus 1 dbus-daemon x :1.0 - - -\n"
        "org.mpris.MediaPlayer2.vlc 22 vlc x :1.5 - - -\n"
        "org.mpris.MediaPlayer2.firefox.instance_1 33 firefox x :1.6 - - -\n")


def media_fake(statuses, md, seen):
    def fake(cmd):
        seen.append(cmd)
        out = ""
        if cmd[-1] == "list":
            out = LIST
        elif cmd[-1] == "PlaybackStatus":
            out = json.dumps({"type": "s", "data": statuses[cmd[4]]})
        elif cmd[-1] == "Metadata":
            out = json.dumps({"type": "a{sv}", "data": md[cmd[4]] if cmd[4] in md else md})
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return fake


MD = {"mpris:trackid": {"type": "o", "data": "/x"},
      "xesam:title": {"type": "s", "data": "Some  Song\tTitle"},
      "xesam:artist": {"type": "as", "data": ["Ann", "Bob"]}}
VLC, FF = "org.mpris.MediaPlayer2.vlc", "org.mpris.MediaPlayer2.firefox.instance_1"


def test_now_prefers_the_playing_player(monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Playing"}, MD, seen))
    assert ctl.handle("NOW") == "M\tplaying\tSome Song Title\tAnn, Bob"
    assert any(c[4] == FF and c[-1] == "Metadata" for c in seen)


def test_now_falls_back_to_first_paused_player(monkeypatch):
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Paused"}, MD, []))
    assert ctl.handle("NOW").startswith("M\tpaused\t")


def test_media_calls_the_player_method_then_reports(monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Playing"}, MD, seen))
    assert ctl.handle("MEDIA PLAYPAUSE").startswith("M\tplaying")
    assert ["busctl", "--user", "call", FF, *ctl.PLAYER, "PlayPause"] in seen
    ctl.handle("MEDIA NEXT")
    ctl.handle("MEDIA PREV")
    assert ["busctl", "--user", "call", FF, *ctl.PLAYER, "Next"] in seen
    assert ["busctl", "--user", "call", FF, *ctl.PLAYER, "Previous"] in seen


def test_no_player_reports_none_and_calls_nothing(monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "run", lambda c: seen.append(c) or subprocess.CompletedProcess(c, 0, "", ""))
    assert ctl.handle("NOW") == "M\tnone\t\t"
    assert ctl.handle("MEDIA NEXT") == "M\tnone\t\t"
    assert all(c[-1] == "list" for c in seen)


def test_media_rejects_unknown_actions(calls):
    for line in ("MEDIA", "MEDIA STOP", "MEDIA NEXT now", "NOW x"):
        assert ctl.handle(line) == "ERR"
    assert calls == []


def test_titleless_headset_proxy_does_not_beat_the_browser(monkeypatch):
    # bluez mpris-proxy reports "Playing" with no metadata while the browser holds the real track
    seen = []
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Playing", FF: "Paused"}, {VLC: {}, FF: MD}, seen))
    assert ctl.handle("NOW") == "M\tpaused\tSome Song Title\tAnn, Bob"
    ctl.handle("MEDIA PLAYPAUSE")
    assert ["busctl", "--user", "call", FF, *ctl.PLAYER, "PlayPause"] in seen
    assert not any(c[:4] == ["busctl", "--user", "call", VLC] for c in seen)
