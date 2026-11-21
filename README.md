# Mellon

Use an Android phone's fingerprint as a FIDO/U2F key for a Linux PC (`pam_u2f`, e.g. `sudo`).

```
phone app (Keystore + fingerprint) <- adb forward -> mellond (virtual U2F key via /dev/uhid) -> libfido2 / pam_u2f
```

- `mellon-ctl.py`: ~175-line stdlib user daemon for PC controls from the phone (below).
- `mellond.py`: stdlib daemon. Env: `MELLON_PORT` (default 8765), `MELLON_DEBUG=1`, `MELLON_CTAP2=1` (WebAuthn, off by default; see below).
- `app/`: one-file Android app (~20 KB). One Keystore key per credential; a fresh strong fingerprint per signature.
- `build.sh`: builds `mellon.apk` from the Android SDK build-tools only (needs `ANDROID_HOME`, JDK 17+, python3). Debug-signed.
- `systemd/`: units to run the daemon and the adb forward at boot.

Status: works on Pixel XL / LineageOS 22.2 + Fedora 44 (Aurora). U2F by default, so no PIN and no LUKS unlock; WebAuthn for websites is opt-in (below).

## Use

1. Build and install: `./build.sh && adb install -r mellon.apk`. Enroll a fingerprint on the phone first.
2. Open the app and keep it in the foreground (screen on). Reopen it after every reinstall.
   While the fingerprint prompt is open a glowing white arch frames the screen (dim and pulsing while it waits, a full flare when the
   finger is accepted, a short flicker on cancel or error). The system dialog itself cannot be restyled, so `Arch.java` draws this in the
   app's own window behind it.
3. `adb forward tcp:8766 localabstract:mellon`
4. `sudo env MELLON_PORT=8766 python3 mellond.py`
5. Register (origin must match the machine that will check it):
   `sudo pamu2fcfg -u "$USER" -o pam://$(hostname) -i pam://$(hostname) > ~/.config/Yubico/u2f_keys`
6. Add `auth sufficient pam_u2f.so cue` above the other `auth` lines in `/etc/pam.d/sudo`. Keep a root shell open while testing; the password stays as fallback.

## WebAuthn for websites (experimental, `MELLON_CTAP2=1`)

With `MELLON_CTAP2=1`, `mellond` also speaks CTAP2, so browsers and `libfido2` can register and sign in with the phone on any site that accepts a security key. No phone-app change: `makeCredential` and `getAssertion` are translated to the U2F REGISTER/AUTHENTICATE calls the app already answers (the phone's signature is already the bytes CTAP2 signs; see the commit messages). Existing `pam_u2f` registrations keep working.

Enable (the host keeps the default of off; keep a root shell open the first time):

```sh
sudo install -Dm 755 mellond.py /usr/local/libexec/mellond.py
sudo systemctl edit mellond      # add:  [Service]  Environment=MELLON_CTAP2=1
sudo systemctl restart mellond
```

Check, in this order:
1. `sudo -k && sudo true`, touch the sensor. `libfido2` switches to CTAP2 once it is advertised, so this must still work.
2. `distrobox enter mellon-build -- fido2-token -L`, then `fido2-token -I <hidraw path>`: `version strings` should list `U2F_V2, FIDO_2_0`.
3. A site that takes security keys (e.g. https://webauthn.io): register, then sign in. Keep the phone unlocked with Mellon in the foreground, as for `sudo`.

Roll back: remove the `Environment=MELLON_CTAP2=1` line (`sudo systemctl revert mellond` drops the whole override), then `sudo systemctl restart mellond`.

Limits: no resident keys (so no username-less/passkey sign-in; sites must send the credential list), no PIN and no user-verification flag (the phone still needs the fingerprint for every signature, but the assertion only claims user presence, so sites that *require* UV refuse it), self-signed attestation. The browser must be able to open the `Mellon` hidraw node; systemd's FIDO udev rules normally grant the logged-in user access (check `ls -l /dev/hidraw*`).

Tests: `python3 -m pytest` (the interop test also needs `pip install fido2 cryptography`; it is skipped without them).

## PC controls (now playing, volume, mic, power)

The app is two swipeable pages (page dots at the bottom, tap to switch). Page 1 (portrait), top to bottom: the PC's GPU and CPU rings (same look as the Pi kiosk: usage fills the top half, memory is liquid in the bottom half, temperature inside; one to three GPUs plus the CPU), now playing with previous, play/pause (ultraviolet) and next, then cyan volume and mic dials; drag around a dial to set the level, tap its icon to mute. Page 2: Lock, Sleep, Log off, Restart and Shut down (the last three ask first). Portrait and landscape. Volume, mic and power are ported from the DroidCon daemon, minus the start menu; now playing is new (MPRIS via `busctl --user`, so it controls whichever player is active).

```
app (@mellon-ctl) <- adb forward tcp:8767 -> mellon-ctl.py (user service) -> wpctl / busctl --user / systemctl
```

- The PC connects out to the phone; nothing listens on the PC. `mellon-ctl.py` runs a fixed allowlist: `wpctl` for volume and mic, Plasma's `org.kde.Shutdown` and `org.freedesktop.ScreenSaver` over the session bus, `systemctl suspend`. Env: `MELLON_CTL_PORT` (default 8767).
- It must run as you, not root: it needs your PipeWire and Plasma session.
- Try it: `adb forward tcp:8767 localabstract:mellon-ctl && python3 mellon-ctl.py`, open the app, drag a dial. Now playing refreshes every 2 s while page 1 is showing.
- Now playing picks the player that has a track title, so a Bluetooth headset's title-less MPRIS proxy (`mpris-proxy`) does not hide the browser. Media buttons act on that same player. Previous restarts the track when there is no queue (or the track is past 3 s); Next greys out when the player has no next track. After updating `mellon-ctl.py`, reinstall it and `systemctl --user restart mellon-ctl`.
- The rings come from `STATS`, polled with `NOW` every 2 s in portrait. The reply is `G<TAB>{"gpus":[{name,usage,temp,mem}],"cpu":{...}}` (percents, degrees C, null when unknown). GPU numbers need `nvidia-smi`; CPU usage comes from `/proc/stat`, temperature from the `k10temp`/`coretemp` hwmon, `mem` is VRAM for a GPU and RAM for the CPU. Landscape shows no rings. Update the PC side with the same reinstall + restart as above.
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
