#!/usr/bin/env python3
"""mellon-ctl: PC volume, mic and power/session controls driven from the phone.

Stdlib only; runs as a user service (it needs your PipeWire and Plasma session
buses, so not as root). It connects to the phone app through
`adb forward tcp:8767 localabstract:mellon-ctl` and reads one command per line:

    VOL <0-100> | VOL MUTE | MIC <0-100> | MIC MUTE | GET
    LOCK | SLEEP | LOGOFF | REBOOT | SHUTDOWN

Only these words are accepted and nothing reaches a shell. After each volume/mic
command, and once on connect, it answers `S <vol> <muted> <mic> <muted>`;
power commands answer `OK` or `ERR`; anything else answers `ERR`.
"""
import os
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
    if cmd in ACTIONS and arg is None:
        r = run(ACTIONS[cmd])
        return "OK" if r is not None and r.returncode == 0 else "ERR"
    return "ERR"


def serve(sock):
    """Answer commands on one phone connection until it closes."""
    f = sock.makefile("rw", newline="\n")
    f.write(state() + "\n")
    f.flush()
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
