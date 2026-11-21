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


def media_fake(statuses, md, seen, extra=None):
    def fake(cmd):
        seen.append(cmd)
        out = ""
        if cmd[-1] == "list":
            out = LIST
        elif cmd[-1] == "PlaybackStatus":
            out = json.dumps({"type": "s", "data": statuses[cmd[4]]})
        elif cmd[-1] == "Metadata":
            out = json.dumps({"type": "a{sv}", "data": md[cmd[4]] if cmd[4] in md else md})
        elif extra and cmd[-1] in extra:
            out = json.dumps({"type": "?", "data": extra[cmd[-1]]})
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return fake


MD = {"mpris:trackid": {"type": "o", "data": "/x"},
      "xesam:title": {"type": "s", "data": "Some  Song\tTitle"},
      "xesam:artist": {"type": "as", "data": ["Ann", "Bob"]}}
VLC, FF = "org.mpris.MediaPlayer2.vlc", "org.mpris.MediaPlayer2.firefox.instance_1"


def test_now_prefers_the_playing_player(monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Playing"}, MD, seen))
    assert ctl.handle("NOW") == "M\tplaying\tSome Song Title\tAnn, Bob\t1"
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
    assert ctl.handle("NOW") == "M\tnone\t\t\t0"
    assert ctl.handle("MEDIA NEXT") == "M\tnone\t\t\t0"
    assert all(c[-1] == "list" for c in seen)


def test_media_rejects_unknown_actions(calls):
    for line in ("MEDIA", "MEDIA STOP", "MEDIA NEXT now", "NOW x"):
        assert ctl.handle(line) == "ERR"
    assert calls == []


def test_titleless_headset_proxy_does_not_beat_the_browser(monkeypatch):
    # bluez mpris-proxy reports "Playing" with no metadata while the browser holds the real track
    seen = []
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Playing", FF: "Paused"}, {VLC: {}, FF: MD}, seen))
    assert ctl.handle("NOW") == "M\tpaused\tSome Song Title\tAnn, Bob\t1"
    ctl.handle("MEDIA PLAYPAUSE")
    assert ["busctl", "--user", "call", FF, *ctl.PLAYER, "PlayPause"] in seen
    assert not any(c[:4] == ["busctl", "--user", "call", VLC] for c in seen)


def test_now_reports_whether_next_exists(monkeypatch):
    for can, want in ((False, "0"), (True, "1")):
        monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Playing"}, MD, [], {"CanGoNext": can}))
        assert ctl.handle("NOW").endswith("\t" + want)


def test_previous_restarts_the_track_when_there_is_no_queue(monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Playing"}, MD, seen, {"CanGoPrevious": False}))
    ctl.handle("MEDIA PREV")
    assert ["busctl", "--user", "call", FF, *ctl.PLAYER, "SetPosition", "ox", "/x", "0"] in seen
    assert not any(c[-1] == "Previous" for c in seen)


def test_previous_restarts_when_well_into_the_track_and_seeks_without_a_trackid(monkeypatch):
    seen = []
    md = {k: v for k, v in MD.items() if k != "mpris:trackid"}
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Playing"}, md, seen,
                                               {"CanGoPrevious": True, "Position": 42_000_000}))
    ctl.handle("MEDIA PREV")
    assert ["busctl", "--user", "--", "call", FF, *ctl.PLAYER, "Seek", "x", "-42000000"] in seen
    assert not any(c[-1] == "Previous" for c in seen)


def test_previous_goes_to_the_previous_track_near_the_start(monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "run", media_fake({VLC: "Paused", FF: "Playing"}, MD, seen,
                                               {"CanGoPrevious": True, "Position": 1_000_000}))
    ctl.handle("MEDIA PREV")
    assert ["busctl", "--user", "call", FF, *ctl.PLAYER, "Previous"] in seen


# --- STATS: the numbers behind the phone's GPU/CPU rings ---------------------


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A fake /proc and hwmon tree plus a fake nvidia-smi; returns a dict of what it reports."""
    proc, hw = tmp_path / "proc", tmp_path / "hwmon"
    proc.mkdir()
    (hw / "hwmon0").mkdir(parents=True)
    (hw / "hwmon1").mkdir()
    (proc / "cpuinfo").write_text("processor\t: 0\nmodel name\t: AMD Ryzen 9 5900XT 16-Core Processor\n")
    (proc / "stat").write_text("cpu  100 0 100 700 100 0 0 0 0 0\n")  # idle 800 / total 1000
    (proc / "meminfo").write_text("MemTotal:       32000000 kB\nMemFree: 1 kB\nMemAvailable:   16000000 kB\n")
    (hw / "hwmon0" / "name").write_text("nvme\n")
    (hw / "hwmon0" / "temp1_input").write_text("99000\n")
    (hw / "hwmon1" / "name").write_text("k10temp\n")
    (hw / "hwmon1" / "temp1_input").write_text("61250\n")
    (hw / "hwmon1" / "temp1_label").write_text("Tctl\n")
    (hw / "hwmon1" / "temp2_input").write_text("40000\n")
    monkeypatch.setattr(ctl, "PROC", str(proc))
    monkeypatch.setattr(ctl, "HWMON", str(hw))
    monkeypatch.setattr(ctl, "_cpu_prev", None)
    smi = {"out": "NVIDIA GeForce RTX 5060 Ti, 62, 68, 7372, 16384\n"
                  "NVIDIA GeForce RTX 3060 Ti, [N/A], 44, 2480, 8192\n", "rc": 0}
    monkeypatch.setattr(ctl, "run", lambda c: subprocess.CompletedProcess(c, smi["rc"], smi["out"], ""))

    def later():  # 200 ms on: 400 more jiffies of which 100 idle -> 75 % busy
        (proc / "stat").write_text("cpu  400 0 100 800 100 0 0 0 0 0\n")

    monkeypatch.setattr(ctl.time, "sleep", lambda s: later())
    return smi


def test_short_names():
    assert ctl.short_cpu("AMD Ryzen 9 5900XT 16-Core Processor") == "AMD 5900XT"
    assert ctl.short_cpu("12th Gen Intel(R) Core(TM) i7-12700K") == "i7-12700K"
    assert ctl.short_cpu("Some Odd CPU Model Name") == "Some Odd CPU"
    assert ctl.short_gpu("NVIDIA GeForce RTX 3060 Ti") == "RTX 3060 Ti"
    assert ctl.short_gpu("AMD Radeon RX 7900 XTX") == "RX 7900 XTX"


def test_stats_reports_cpu_gpus_and_ram(pc):
    line = ctl.handle("STATS\n")
    assert line.startswith("G\t") and "\n" not in line
    d = json.loads(line[2:])
    assert d["cpu"] == {"name": "AMD 5900XT", "usage": 75, "temp": 61, "mem": 50}  # Tctl, not nvme
    assert d["gpus"] == [{"name": "RTX 5060 Ti", "usage": 62, "temp": 68, "mem": 45},
                         {"name": "RTX 3060 Ti", "usage": None, "temp": 44, "mem": 30}]


def test_cpu_usage_is_since_the_previous_call(pc, tmp_path):
    ctl.cpu_usage()  # first call measures over a short sleep
    (tmp_path / "proc" / "stat").write_text("cpu  400 0 100 1000 100 0 0 0 0 0\n")  # +200 idle, 0 busy
    assert ctl.cpu_usage() == 0


def test_stats_without_nvidia_or_sensors_gives_nulls_not_guesses(pc, monkeypatch, tmp_path):
    pc["rc"] = 1
    for f in (tmp_path / "hwmon" / "hwmon1").iterdir():
        f.unlink()
    (tmp_path / "proc" / "meminfo").write_text("")
    d = json.loads(ctl.stats()[2:])
    assert d["gpus"] == []
    assert d["cpu"]["temp"] is None and d["cpu"]["mem"] is None


def test_stats_takes_no_arguments_and_runs_only_the_fixed_query(pc, monkeypatch):
    assert ctl.handle("STATS now") == "ERR"
    seen = []
    monkeypatch.setattr(ctl, "run", lambda c: seen.append(c) or subprocess.CompletedProcess(c, 1, "", ""))
    ctl.handle("STATS")
    assert seen == [ctl.NVIDIA]
