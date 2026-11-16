# Mellon

Use an Android phone's fingerprint as a FIDO/U2F key for a Linux PC (`pam_u2f`, e.g. `sudo`).

```
phone app (Keystore + fingerprint) <- adb forward -> mellond (virtual U2F key via /dev/uhid) -> libfido2 / pam_u2f
```

- `mellon-ctl.py`: ~175-line stdlib user daemon for PC controls from the phone (below).
- `mellond.py`: ~110-line stdlib daemon. Env: `MELLON_PORT` (default 8765), `MELLON_DEBUG=1`.
- `app/`: one-file Android app (~20 KB). One Keystore key per credential; a fresh strong fingerprint per signature.
- `build.sh`: builds `mellon.apk` from the Android SDK build-tools only (needs `ANDROID_HOME`, JDK 17+, python3). Debug-signed.
- `systemd/`: units to run the daemon and the adb forward at boot.

Status: works on Pixel XL / LineageOS 22.2 + Fedora 44 (Aurora). U2F only, so no PIN and no LUKS unlock.

## Use

1. Build and install: `./build.sh && adb install -r mellon.apk`. Enroll a fingerprint on the phone first.
2. Open the app and keep it in the foreground (screen on). Reopen it after every reinstall.
3. `adb forward tcp:8766 localabstract:mellon`
4. `sudo env MELLON_PORT=8766 python3 mellond.py`
5. Register (origin must match the machine that will check it):
   `sudo pamu2fcfg -u "$USER" -o pam://$(hostname) -i pam://$(hostname) > ~/.config/Yubico/u2f_keys`
6. Add `auth sufficient pam_u2f.so cue` above the other `auth` lines in `/etc/pam.d/sudo`. Keep a root shell open while testing; the password stays as fallback.

## PC controls (now playing, volume, mic, power)

The app is two swipeable pages (or tap the icons at the bottom). Page 1: now playing with previous, play/pause and next, plus volume and mic dials; drag around a dial to set the level, tap its icon to mute. Page 2: Lock, Sleep, Log off, Restart and Shut down (the last three ask first). Portrait and landscape. Volume, mic and power are ported from the DroidCon daemon, minus the start menu; now playing is new (MPRIS via `busctl --user`, so it controls whichever player is active).

```
app (@mellon-ctl) <- adb forward tcp:8767 -> mellon-ctl.py (user service) -> wpctl / busctl --user / systemctl
```

- The PC connects out to the phone; nothing listens on the PC. `mellon-ctl.py` runs a fixed allowlist: `wpctl` for volume and mic, Plasma's `org.kde.Shutdown` and `org.freedesktop.ScreenSaver` over the session bus, `systemctl suspend`. Env: `MELLON_CTL_PORT` (default 8767).
- It must run as you, not root: it needs your PipeWire and Plasma session.
- Try it: `adb forward tcp:8767 localabstract:mellon-ctl && python3 mellon-ctl.py`, open the app, drag a dial. Now playing refreshes every 2 s while page 1 is showing.
- Any app on the phone can reach `@mellon-ctl` and run these commands. Fingerprint is not required for them.

## Run at boot

```sh
sudo mkdir -p /usr/local/libexec && sudo install -m 755 mellond.py /usr/local/libexec/mellond.py
sudo install -m 644 systemd/mellond.service /etc/systemd/system/ && sudo systemctl enable --now mellond
install -Dm 644 systemd/mellon-forward.service ~/.config/systemd/user/mellon-forward.service
systemctl --user enable --now mellon-forward      # needs adb and an authorized phone
install -Dm 755 mellon-ctl.py ~/.local/libexec/mellon-ctl.py
install -Dm 644 systemd/mellon-ctl.service ~/.config/systemd/user/mellon-ctl.service
systemctl --user daemon-reload && systemctl --user enable --now mellon-ctl
```

The units use ports 8766 (key) and 8767 (controls). After updating `mellond.py`, reinstall it and `sudo systemctl restart mellond`.

## Troubleshooting

- `FIDO_ERR_RX` or a silent fall-through to the password: phone asleep or app not on screen, app not running, no forward, or daemon down. Check `adb logcat -d -s mellon` and `systemctl status mellond`.
- `Caller is not foreground` in logcat: wake and unlock the phone and open the app.
- `PreAuthInfo ... Status: 7`: no fingerprint enrolled. Enrolling another finger later invalidates the keys; register again.
- Probe the phone link: send APDU `00 03 00 00` (length-prefixed) to the port; expect `U2F_V2` + `9000`.

## Limits

- Any app on the phone can reach `@mellon`; every signature still needs your fingerprint.
- Unlocked bootloader means nothing attests the OS: "device present + fingerprint", not attested hardware.
- Transport is adb over USB; the app must stay in the foreground.

Tests: `python3 -m pytest`
