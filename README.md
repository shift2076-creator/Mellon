# Mellon

Use an Android phone's fingerprint as a FIDO/U2F key for a Linux PC (`pam_u2f`, sudo, lock screen).

- `mellond.py`: ~110-line stdlib daemon. Creates a virtual U2F key (`/dev/uhid`) and forwards each request to the phone.
- `app/`: one-file Android app. Per-credential Keystore keys, fingerprint required for every signature.

**Status: spike, untested on hardware.** The app has not been compiled yet (no Android SDK where it was written).

## Try it (no system changes)

```sh
./build.sh                                   # needs ANDROID_HOME; or build in a distrobox
adb install mellon.apk                       # open the app and leave it in the foreground
adb forward tcp:8765 localabstract:mellon
sudo python3 mellond.py &                    # sudo only for /dev/uhid
command -v pamu2fcfg fido2-token            # from pam-u2f / libfido2; if missing, run in a distrobox
fido2-token -L                               # the "Mellon" key should be listed
pamu2fcfg -u "$USER" > /tmp/u2f_keys         # tap fingerprint on the phone
```

Wire it into PAM only after the above works, and keep password fallback (`auth sufficient pam_u2f.so`, never the only method).

## Known limits

- U2F only (no CTAP2/PIN/`hmac-secret`), so no LUKS unlock at boot.
- v0 transport is `adb forward`; the phone app must stay in the foreground.
- Any app on the phone can reach `@mellon`; each signature still needs your fingerprint.
- Phone bootloader is unlocked, so treat this as "device present + fingerprint", not attested hardware.

Tests: `python3 -m pytest`
