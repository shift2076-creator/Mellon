#!/usr/bin/env python3
"""mellon-ctl: PC volume, mic and power/session controls driven from the phone.

Stdlib only; runs as a user service (it needs your PipeWire and Plasma session
buses, so not as root). It connects to the phone app through
`adb forward tcp:8767 localabstract:mellon-ctl` and reads one command per line:

    VOL <0-100> | VOL MUTE | MIC <0-100> | MIC MUTE | GET
    LOCK | SLEEP | LOGOFF | REBOOT | SHUTDOWN
    NOW | MEDIA PLAYPAUSE | MEDIA NEXT | MEDIA PREV | STATS

Only these words are accepted and nothing reaches a shell. After each volume/mic
command and to GET, it answers `S <vol> <muted> <mic> <muted>`;
power commands answer `OK` or `ERR`; NOW and MEDIA answer
`M<TAB>playing|paused|stopped|none<TAB>title<TAB>artist<TAB>can_next(1|0)` for the MPRIS media player
(browser, Spotify, VLC, ...) that is playing a titled track, else the best other one; STATS answers
`G<TAB>{json}` with the PC's load for the phone's ring graphs (see stats()); anything else answers `ERR`.
"""
import json
import os
import re
import socket
import subprocess
import time

PORT = int(os.environ.get("MELLON_CTL_PORT", "8767"))
SINK, SOURCE = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"
KDE_SHUTDOWN = ["busctl", "--user", "call", "org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown"]
ACTIONS = {
    "LOCK": ["busctl", "--user", "call", "org.freedesktop.ScreenSaver", "/ScreenSaver",
             "org.freedesktop.ScreenSaver", "Lock"],
    "SLEEP": ["systemctl", "suspend"],
    "LOGOFF": KDE_SHUTDOWN + ["logout"],
    "REBOOT": KDE_SHUTDOWN + ["logoutAndReboot"],
    "SHUTDOWN": KDE_SHUTDOWN + ["logoutAndShutdown"],
}


PROC, HWMON = "/proc", "/sys/class/hwmon"
NVIDIA = ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
          "--format=csv,noheader,nounits"]
GPU_MAX = 3  # the phone draws at most three GPU rings
_cpu_prev = None  # (idle, total) jiffies at the last STATS

MPRIS = "org.mpris.MediaPlayer2."
PLAYER = ["/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player"]
MEDIA = {"PLAYPAUSE": "PlayPause", "NEXT": "Next", "PREV": "Previous"}


def run(cmd):
    """Run cmd; return the CompletedProcess, or None if it could not start or hung."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def level(node):
    """(percent, muted) for a wpctl node; (0, 0) if unknown. Output: 'Volume: 0.40 [MUTED]'."""
    r = run(["wpctl", "get-volume", node])
    if r is None or r.returncode:
        return 0, 0
    w = r.stdout.split()
    try:
        return round(float(w[1]) * 100), int("[MUTED]" in w)
    except (IndexError, ValueError):
        return 0, 0


def state():
    v, vm = level(SINK)
    m, mm = level(SOURCE)
    return f"S {v} {vm} {m} {mm}"


def players():
    """Bus names of running MPRIS media players."""
    r = run(["busctl", "--user", "--acquired", "--no-legend", "list"])
    if r is None or r.returncode:
        return []
    return [w[0] for w in map(str.split, r.stdout.splitlines()) if w and w[0].startswith(MPRIS)]


def prop(name, p):
    """One MPRIS Player property as plain JSON data, or None."""
    r = run(["busctl", "--user", "--json=short", "get-property", name] + PLAYER + [p])
    try:
        return json.loads(r.stdout)["data"]
    except (AttributeError, ValueError, KeyError, TypeError):
        return None


def title_of(md):
    return ((md or {}).get("xesam:title") or {}).get("data")


def current():
    """(bus name, status, metadata) of the best player, or None if there is none.

    Best = playing with a track title, then paused with one, then playing without one,
    then anything. A player with no title is usually a Bluetooth headset's AVRCP proxy
    (bluez mpris-proxy), which says "playing" while the browser holds the real track.
    """
    best, top = None, -1
    for n in players():
        st = prop(n, "PlaybackStatus")
        md = prop(n, "Metadata") or {}
        rank = 3 if st == "Playing" and title_of(md) else 2 if title_of(md) else 1 if st == "Playing" else 0
        if rank > top:
            best, top = (n, st, md), rank
    return best


def clean(v):
    return " ".join(str(v or "").split())[:200]


def now():
    c = current()
    if not c:
        return "M\tnone\t\t\t0"
    md = c[2]

    def field(k):
        return (md.get(k) or {}).get("data")

    artist = field("xesam:artist")
    if isinstance(artist, list):
        artist = ", ".join(map(str, artist))
    can_next = "0" if prop(c[0], "CanGoNext") is False else "1"  # unknown counts as yes
    return "M\t%s\t%s\t%s\t%s" % (clean(c[1]).lower() or "stopped", clean(field("xesam:title")),
                                     clean(artist), can_next)


def read(path):
    """Text of a small file, or None."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def short_gpu(name):
    """'NVIDIA GeForce RTX 5060 Ti' -> 'RTX 5060 Ti' (what fits inside a ring)."""
    return clean(re.sub(r"^(NVIDIA|AMD)\s+(GeForce\s+|Radeon\s+)?", "", name.strip()))[:12]


def short_cpu(name):
    """'AMD Ryzen 9 5900XT 16-Core Processor' -> 'AMD 5900XT'; Intel: 'i7-12700K'."""
    n = re.sub(r"\((R|TM)\)", "", name)
    m = re.search(r"\b(i[3579]-\d{4,5}\w*)", n)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{4}\w*)\b", n)
    if m and n.lstrip().startswith("AMD"):
        return "AMD " + m.group(1)
    return clean(n)[:12]


def cpu_name():
    for line in (read(PROC + "/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            return short_cpu(line.partition(":")[2])
    return "CPU"


def cpu_sample():
    """(idle, total) jiffies from the first line of /proc/stat, or None."""
    try:
        v = [int(x) for x in (read(PROC + "/stat") or "").splitlines()[0].split()[1:9]]
        return v[3] + v[4], sum(v)
    except (IndexError, ValueError):
        return None


def cpu_usage():
    """Percent busy since the previous call (the first call measures over 0.2 s)."""
    global _cpu_prev
    cur = cpu_sample()
    if cur is None:
        return None
    if _cpu_prev is None:
        _cpu_prev = cur
        time.sleep(0.2)
        cur = cpu_sample() or cur
    di, dt = cur[0] - _cpu_prev[0], cur[1] - _cpu_prev[1]
    _cpu_prev = cur
    return None if dt <= 0 else max(0, min(100, round(100 * (1 - di / dt))))


def cpu_temp():
    """Degrees C from the k10temp (AMD) or coretemp (Intel) hwmon, preferring Tctl/Tdie/Package."""
    try:
        names = sorted(os.listdir(HWMON))
    except OSError:
        return None
    for d in names:
        base = f"{HWMON}/{d}/"
        if (read(base + "name") or "").strip() not in ("k10temp", "coretemp"):
            continue
        best = None
        for i in range(1, 9):
            raw = read(base + f"temp{i}_input")
            if raw is None:
                continue
            label = (read(base + f"temp{i}_label") or "").strip()
            try:
                t = round(int(raw) / 1000)
            except ValueError:
                continue
            if label in ("Tctl", "Tdie", "Package id 0"):
                return t
            best = t if best is None else best
        if best is not None:
            return best
    return None


def ram_percent():
    info = {}
    for line in (read(PROC + "/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            try:
                info[k] = int(v.split()[0])
            except (IndexError, ValueError):
                return None
    if info.get("MemTotal") and "MemAvailable" in info:
        return round(100 * (1 - info["MemAvailable"] / info["MemTotal"]))
    return None


def num(s):
    """int from an nvidia-smi field; None for '[N/A]' or junk."""
    try:
        return int(float(s))
    except ValueError:
        return None


def gpus():
    """One dict per NVIDIA GPU (nvidia-smi, a fixed query): name, usage %, temp C, memory %."""
    r = run(NVIDIA)
    if r is None or r.returncode:
        return []
    out = []
    for line in r.stdout.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) != 5:
            continue
        used, total = num(f[3]), num(f[4])
        out.append({"name": short_gpu(f[0]), "usage": num(f[1]), "temp": num(f[2]),
                    "mem": round(100 * used / total) if used is not None and total else None})
    return out[:GPU_MAX]


def stats():
    """`G<TAB>json` for the phone's rings. Unknown values are null, never guessed:
    {"gpus": [{"name", "usage", "temp", "mem"}], "cpu": {"name", "usage", "temp", "mem"}}
    where usage/mem are percents (mem = VRAM for a GPU, RAM for the CPU) and temp is degrees C."""
    d = {"gpus": gpus(),
         "cpu": {"name": cpu_name(), "usage": cpu_usage(), "temp": cpu_temp(), "mem": ram_percent()}}
    return "G\t" + json.dumps(d, separators=(",", ":"))


def restart(c):
    """Jump the current track back to its start (what Previous should do when there is no queue)."""
    tid = (c[2].get("mpris:trackid") or {}).get("data")
    if tid:
        run(["busctl", "--user", "call", c[0]] + PLAYER + ["SetPosition", "ox", tid, "0"])
        return
    pos = prop(c[0], "Position")
    if isinstance(pos, int) and pos > 0:  # "--" so the negative number is not read as an option
        run(["busctl", "--user", "--", "call", c[0]] + PLAYER + ["Seek", "x", str(-pos)])


def media(action):
    c = current()
    if c:
        pos = prop(c[0], "Position")
        # Previous: with no earlier track, or once the track is past its first 3 s, restart it instead
        if action == "PREV" and (prop(c[0], "CanGoPrevious") is False
                                 or (isinstance(pos, int) and pos > 3_000_000)):
            restart(c)
        else:
            run(["busctl", "--user", "call", c[0]] + PLAYER + [MEDIA[action]])
    return now()


def handle(line):
    """Run one command line and return the reply line."""
    w = line.split()
    cmd = w[0] if w else ""
    arg = w[1] if len(w) == 2 else None
    if len(w) > 2:
        return "ERR"
    if cmd in ("VOL", "MIC"):
        node = SINK if cmd == "VOL" else SOURCE
        if arg == "MUTE":
            run(["wpctl", "set-mute", node, "toggle"])
        elif arg and arg.isascii() and arg.isdigit():
            run(["wpctl", "set-volume", node, f"{min(int(arg), 100) / 100:.2f}"])
        else:
            return "ERR"
        return state()
    if cmd == "GET" and arg is None:
        return state()
    if cmd == "NOW" and arg is None:
        return now()
    if cmd == "STATS" and arg is None:
        return stats()
    if cmd == "MEDIA" and arg in MEDIA:
        return media(arg)
    if cmd in ACTIONS and arg is None:
        r = run(ACTIONS[cmd])
        return "OK" if r is not None and r.returncode == 0 else "ERR"
    return "ERR"


def serve(sock):
    """Answer commands on one phone connection until it closes."""
    f = sock.makefile("rw", newline="\n")
    while True:
        line = f.readline(64)
        if not line.endswith("\n"):  # EOF, or a line too long to be ours
            return
        f.write(handle(line) + "\n")
        f.flush()


def main():
    while True:  # the phone app or adb may come and go; just keep retrying
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=3) as s:
                s.settimeout(None)
                serve(s)
        except OSError:
            pass
        time.sleep(3)


if __name__ == "__main__":
    main()
