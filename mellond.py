#!/usr/bin/env python3
"""mellond: a virtual U2F key (via /dev/uhid) that forwards each request to a phone.

Stdlib only. The phone speaks 2-byte big-endian length + raw U2F APDU over TCP
(reach it with: adb forward tcp:8765 localabstract:mellon).
"""
import hashlib
import os
import socket
import struct
import sys
import time

PHONE = ("127.0.0.1", int(os.environ.get("MELLON_PORT", "8765")))
DEBUG = os.environ.get("MELLON_DEBUG")  # print phone traffic to stderr
CTAP2 = bool(os.environ.get("MELLON_CTAP2"))  # opt in: speak CTAP2 (WebAuthn) too
BCAST = 0xFFFFFFFF
PING, MSG, INIT, CBOR, CANCEL, KEEPALIVE, ERROR = 0x01, 0x03, 0x06, 0x10, 0x11, 0x3B, 0x3F
CAP_CBOR = 0x04
CAP_NMSG = 0x08  # no CTAPHID_CBOR: clients fall back to U2F
# authenticatorGetInfo: U2F + CTAP2, no resident keys, no PIN/UV (the phone only
# signs with user presence), 1200-byte messages.
INFO = {1: ["U2F_V2", "FIDO_2_0"], 3: bytes(16), 4: {"rk": False, "up": True}, 5: 1200}
# FIDO usage page 0xF1D0, 64-byte in/out reports, no report id.
RDESC = bytes.fromhex(
    "06d0f10901a1010920150026ff007508954081020921150026ff00750895409102c0")
UHID_SIZE = 4376
WAIT = 30  # seconds to wait for a fingerprint before giving up


def _head(major, n):
    m = major << 5
    if n < 24:
        return bytes([m | n])
    for info, fmt, limit in ((24, ">B", 1 << 8), (25, ">H", 1 << 16),
                             (26, ">I", 1 << 32), (27, ">Q", 1 << 64)):
        if n < limit:
            return bytes([m | info]) + struct.pack(fmt, n)
    raise ValueError("integer too large")


def cbor_encode(o):
    """Canonical CBOR (CTAP2): ints, bytes, str, bool, None, list, dict."""
    if o is True:
        return b"\xf5"
    if o is False:
        return b"\xf4"
    if o is None:
        return b"\xf6"
    if isinstance(o, int):
        return _head(0, o) if o >= 0 else _head(1, -1 - o)
    if isinstance(o, bytes):
        return _head(2, len(o)) + o
    if isinstance(o, str):
        b = o.encode()
        return _head(3, len(b)) + b
    if isinstance(o, (list, tuple)):
        return _head(4, len(o)) + b"".join(map(cbor_encode, o))
    if isinstance(o, dict):
        items = sorted((cbor_encode(k), cbor_encode(v)) for k, v in o.items())
        return _head(5, len(o)) + b"".join(k + v for k, v in items)
    raise TypeError("cannot encode %r" % type(o))


def cbor_decode(b, i=0, depth=0):
    """-> (value, next index). Definite lengths only; raises ValueError/IndexError."""
    if depth > 8:
        raise ValueError("nested too deep")
    major, info = b[i] >> 5, b[i] & 31
    i += 1
    if info < 24:
        n = info
    elif info < 28:
        w = 1 << (info - 24)
        if i + w > len(b):
            raise ValueError("truncated")
        n, i = int.from_bytes(b[i:i + w], "big"), i + w
    else:
        raise ValueError("indefinite length or reserved")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        if i + n > len(b):
            raise ValueError("truncated")
        v = b[i:i + n]
        return (v if major == 2 else v.decode()), i + n
    if major == 4:
        out = []
        for _ in range(n):
            v, i = cbor_decode(b, i, depth + 1)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = cbor_decode(b, i, depth + 1)
            v, i = cbor_decode(b, i, depth + 1)
            if not isinstance(k, (int, str, bytes)):
                raise ValueError("bad map key")
            out[k] = v
        return out, i
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], i
    raise ValueError("unsupported CBOR item")


class Fail(Exception):
    """A CTAP2 status code to return instead of a result."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def field(req, key, typ, need=False):
    v = req.get(key)
    if v is None:
        if need:
            raise Fail(0x14)  # MISSING_PARAMETER
        return None
    if not isinstance(v, typ) or isinstance(v, bool):
        raise Fail(0x11)  # CBOR_UNEXPECTED_TYPE
    return v


def der_len(b):
    """Total length of the DER SEQUENCE at the start of b."""
    if len(b) < 2 or b[0] != 0x30:
        raise ValueError("not a DER sequence")
    if b[1] < 0x80:
        return 2 + b[1]
    k = b[1] & 0x7F
    return 2 + k + int.from_bytes(b[2:2 + k], "big")


class Ctap:
    """CTAPHID framing. send(report: bytes) emits one 64-byte report;
    forward(apdu: bytes) -> bytes asks the phone."""

    def __init__(self, send, forward, ctap2=False):
        self.send, self.forward, self.ctap2 = send, forward, ctap2
        self.sleep, self.clock = time.sleep, time.monotonic
        self.cid_next = 1
        self.rx = None  # [cid, cmd, total, data, seq]

    def reply(self, cid, cmd, data):
        pkt = struct.pack(">IBH", cid, 0x80 | cmd, len(data)) + data[:57]
        self.send(pkt.ljust(64, b"\0"))
        data, seq = data[57:], 0
        while data:
            pkt = struct.pack(">IB", cid, seq) + data[:59]
            self.send(pkt.ljust(64, b"\0"))
            data, seq = data[59:], seq + 1

    def packet(self, p):
        cid = struct.unpack(">I", p[:4])[0]
        if p[4] & 0x80:
            total = (p[5] << 8) | p[6]
            self.rx = [cid, p[4] & 0x7F, total, p[7:7 + min(total, 57)], 0]
        elif self.rx and self.rx[0] == cid and self.rx[4] == p[4]:
            need = self.rx[2] - len(self.rx[3])
            self.rx[3] += p[5:5 + min(need, 59)]
            self.rx[4] += 1
        else:
            return
        if len(self.rx[3]) >= self.rx[2]:
            _, cmd, _, data, _ = self.rx
            self.rx = None
            self.dispatch(cid, cmd, data)

    def dispatch(self, cid, cmd, data):
        if cmd == INIT:
            new = cid
            if cid == BCAST:
                new, self.cid_next = self.cid_next, self.cid_next + 1
            self.reply(cid, INIT, struct.pack(
                ">8sIBBBBB", data[:8], new, 2, 0, 1, 0,
                CAP_CBOR if self.ctap2 else CAP_NMSG))
        elif cid == BCAST:
            self.reply(cid, ERROR, b"\x0b")  # invalid channel
        elif cmd == PING:
            self.reply(cid, PING, data)
        elif cmd == MSG:
            try:
                self.reply(cid, MSG, self.forward(data))
            except OSError as e:
                if DEBUG:
                    print("phone error:", e, file=sys.stderr, flush=True)
                self.reply(cid, ERROR, b"\x7f")  # phone unreachable
        elif cmd == CBOR and self.ctap2:
            self.reply(cid, CBOR, self.cbor(cid, data))
        elif cmd == CANCEL and self.ctap2:
            pass  # nothing to cancel: requests are answered one at a time
        else:
            self.reply(cid, ERROR, b"\x01")  # invalid command

    def cbor(self, cid, data):
        """One CTAP2 command -> status byte + CBOR."""
        try:
            cmd = data[:1]
            if cmd == b"\x04":  # authenticatorGetInfo
                return b"\0" + cbor_encode(INFO)
            if cmd not in (b"\x01", b"\x02"):
                raise Fail(0x01)  # INVALID_COMMAND
            try:
                req = cbor_decode(data, 1)[0]
            except (ValueError, IndexError):
                raise Fail(0x12)  # INVALID_CBOR
            if not isinstance(req, dict):
                raise Fail(0x12)
            run = self.make_credential if cmd == b"\x01" else self.get_assertion
            return b"\0" + run(cid, req)
        except Fail as e:
            return bytes([e.code])
        except OSError as e:
            if DEBUG:
                print("phone error:", e, file=sys.stderr, flush=True)
            return b"\x7f"  # OTHER: phone unreachable

    def u2f(self, cid, ins, p1, data, wait=False):
        """Run one U2F command on the phone -> (body, status word).
        With wait, poll the identical request while the phone shows its
        fingerprint prompt (0x6985), sending CTAPHID keepalives."""
        apdu = bytes([0, ins, p1, 0, 0]) + struct.pack(">H", len(data)) + data + b"\0\0"
        end = self.clock() + WAIT
        while True:
            r = self.forward(apdu)
            if len(r) < 2:
                raise OSError("short reply from phone")
            sw = struct.unpack(">H", r[-2:])[0]
            if sw != 0x6985 or not wait:
                return r[:-2], sw
            if self.clock() > end:
                raise Fail(0x2F)  # USER_ACTION_TIMEOUT
            self.reply(cid, KEEPALIVE, b"\x02")  # UPNEEDED
            self.sleep(0.2)

    def owns(self, cid, app, kid):
        """True if the phone made key handle kid for this rpIdHash (check-only
        authenticate: 0x6985 means 'yes', 0x6A80 'no')."""
        if not 0 < len(kid) < 256:
            return False
        return self.u2f(cid, 2, 7, bytes(32) + app + bytes([len(kid)]) + kid)[1] == 0x6985

    def make_credential(self, cid, req):
        cdh = field(req, 1, bytes, True)
        rp = field(req, 2, dict, True)
        params = field(req, 4, list, True)
        exclude = field(req, 5, list) or []
        opts = field(req, 7, dict) or {}
        rp_id = rp.get("id")
        if not isinstance(rp_id, str):
            raise Fail(0x14)
        if len(cdh) != 32:
            raise Fail(0x03)
        if not any(isinstance(x, dict) and x.get("alg") == -7 and x.get("type") == "public-key"
                   for x in params):
            raise Fail(0x26)  # UNSUPPORTED_ALGORITHM: the phone only makes ES256 keys
        if opts.get("rk") or opts.get("uv"):
            raise Fail(0x2B)  # UNSUPPORTED_OPTION
        if 8 in req:
            raise Fail(0x35)  # PIN_NOT_SET: we never advertise a PIN
        app = hashlib.sha256(rp_id.encode()).digest()
        for x in exclude:
            kid = x.get("id") if isinstance(x, dict) else None
            if isinstance(kid, bytes) and self.owns(cid, app, kid):
                raise Fail(0x19)  # CREDENTIAL_EXCLUDED
        body, sw = self.u2f(cid, 1, 3, cdh + app, wait=True)
        if sw == 0x6A80:
            raise Fail(0x27)  # OPERATION_DENIED: fingerprint refused
        if sw != 0x9000 or len(body) < 67 or body[0] != 5 or len(body) < 67 + body[66]:
            raise Fail(0x7F)
        pub, kid, rest = body[1:66], body[67:67 + body[66]], body[67 + body[66]:]
        try:
            n = der_len(rest)
        except ValueError:
            raise Fail(0x7F)
        cert, sig = rest[:n], rest[n:]
        # fido-u2f attestation: the phone's REGISTER signature already covers
        # 0x00 || rpIdHash || clientDataHash || credentialId || publicKey.
        cose = {1: 2, 3: -7, -1: 1, -2: pub[1:33], -3: pub[33:65]}
        auth = (app + b"\x41" + bytes(4) + bytes(16) + struct.pack(">H", len(kid)) + kid
                + cbor_encode(cose))
        return cbor_encode({1: "fido-u2f", 2: auth, 3: {"sig": sig, "x5c": [cert]}})

    def get_assertion(self, cid, req):
        raise Fail(0x01)


def phone(apdu):
    with socket.create_connection(PHONE, timeout=5) as s:
        s.sendall(struct.pack(">H", len(apdu)) + apdu)
        f = s.makefile("rb")
        head = f.read(2)
        if len(head) < 2:
            raise OSError("phone closed the connection")
        n = struct.unpack(">H", head)[0]
        body = f.read(n)
    if len(body) != n:
        raise OSError("short read from phone")
    if DEBUG:
        print("apdu", apdu.hex()[:60], "->", body.hex()[:60], file=sys.stderr, flush=True)
    return body


def main():
    fd = os.open("/dev/uhid", os.O_RDWR)

    def send(report):
        ev = struct.pack("<IH", 12, 64) + report  # UHID_INPUT2
        os.write(fd, ev.ljust(UHID_SIZE, b"\0"))

    os.write(fd, struct.pack(  # UHID_CREATE2
        "<I128s64s64sHHIIII4096s", 11, b"Mellon", b"", b"", len(RDESC),
        3, 0x1209, 0x0001, 0, 0, RDESC))
    ctap = Ctap(send, phone, CTAP2)
    while True:
        ev = os.read(fd, UHID_SIZE)
        kind = struct.unpack("<I", ev[:4])[0]
        if kind == 6:  # UHID_OUTPUT
            size = struct.unpack("<H", ev[4100:4102])[0]
            data = ev[4:4 + size]
            ctap.packet(data[1:65] if size == 65 else data[:64])
        elif kind == 3:  # UHID_STOP
            return


if __name__ == "__main__":
    sys.exit(main())
