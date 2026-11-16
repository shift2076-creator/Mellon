#!/usr/bin/env python3
"""mellond: a virtual U2F key (via /dev/uhid) that forwards each request to a phone.

Stdlib only. The phone speaks 2-byte big-endian length + raw U2F APDU over TCP
(reach it with: adb forward tcp:8765 localabstract:mellon).
"""
import os
import socket
import struct
import sys

PHONE = ("127.0.0.1", 8765)
BCAST = 0xFFFFFFFF
PING, MSG, INIT, ERROR = 0x01, 0x03, 0x06, 0x3F
CAP_NMSG = 0x08  # no CTAPHID_CBOR: clients fall back to U2F
# FIDO usage page 0xF1D0, 64-byte in/out reports, no report id.
RDESC = bytes.fromhex(
    "06d0f10901a1010920150026ff007508954081020921150026ff00750895409102c0")
UHID_SIZE = 4376


class Ctap:
    """CTAPHID framing. send(report: bytes) emits one 64-byte report;
    forward(apdu: bytes) -> bytes asks the phone."""

    def __init__(self, send, forward):
        self.send, self.forward = send, forward
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
                ">8sIBBBBB", data[:8], new, 2, 0, 1, 0, CAP_NMSG))
        elif cid == BCAST:
            self.reply(cid, ERROR, b"\x0b")  # invalid channel
        elif cmd == PING:
            self.reply(cid, PING, data)
        elif cmd == MSG:
            try:
                self.reply(cid, MSG, self.forward(data))
            except OSError:
                self.reply(cid, ERROR, b"\x7f")  # phone unreachable
        else:
            self.reply(cid, ERROR, b"\x01")  # invalid command


def phone(apdu):
    with socket.create_connection(PHONE, timeout=5) as s:
        s.sendall(struct.pack(">H", len(apdu)) + apdu)
        f = s.makefile("rb")
        n = struct.unpack(">H", f.read(2))[0]
        body = f.read(n)
    if len(body) != n:
        raise OSError("short read from phone")
    return body


def main():
    fd = os.open("/dev/uhid", os.O_RDWR)

    def send(report):
        ev = struct.pack("<IH", 12, 64) + report  # UHID_INPUT2
        os.write(fd, ev.ljust(UHID_SIZE, b"\0"))

    os.write(fd, struct.pack(  # UHID_CREATE2
        "<I128s64s64sHHIIII4096s", 11, b"Mellon", b"", b"", len(RDESC),
        3, 0x1209, 0x0001, 0, 0, RDESC))
    ctap = Ctap(send, phone)
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
