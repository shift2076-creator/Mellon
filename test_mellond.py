import struct

from mellond import BCAST, Ctap


def rig(forward=lambda apdu: b"\x90\x00"):
    out = []
    return Ctap(out.append, forward), out


def init_pkt(cid, cmd, data):
    pkt = struct.pack(">IBH", cid, 0x80 | cmd, len(data)) + data[:57]
    return pkt.ljust(64, b"\0")


def test_init_allocates_channel_and_hides_cbor():
    c, out = rig()
    c.packet(init_pkt(BCAST, 0x06, b"12345678"))
    cid, cmd, n = struct.unpack(">IBH", out[0][:7])
    assert (cid, cmd, n) == (BCAST, 0x86, 17)
    assert out[0][7:15] == b"12345678"
    assert struct.unpack(">I", out[0][15:19])[0] == 1
    assert out[0][23] & 0x08  # NMSG


def test_msg_reassembles_request_and_fragments_reply():
    seen = []
    c, out = rig(lambda apdu: seen.append(apdu) or bytes(range(100)) + b"\x90\x00")
    req = bytes(71)
    c.packet(init_pkt(1, 0x03, req))
    c.packet(struct.pack(">IB", 1, 0) + req[57:].ljust(59, b"\0"))
    assert seen == [req]
    assert len(out) == 2  # 102 bytes = 57 (init) + 45 (one continuation)
    assert out[0][4] == 0x83 and out[1][4] == 0


def test_phone_unreachable_returns_ctaphid_error():
    def down(apdu):
        raise ConnectionRefusedError

    c, out = rig(down)
    c.packet(init_pkt(1, 0x03, b"\0" * 4))
    assert out[0][4] == 0xBF and out[0][7] == 0x7F


def test_cbor_is_rejected_so_clients_use_u2f():
    c, out = rig()
    c.packet(init_pkt(1, 0x10, b"\x04"))
    assert out[0][4] == 0xBF and out[0][7] == 0x01


def test_phone_closing_without_reply_is_an_oserror():
    import socket
    import threading

    import pytest

    import mellond

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True).start()
    mellond.PHONE = srv.getsockname()
    with pytest.raises(OSError):
        mellond.phone(b"\0" * 7)


def test_cbor_known_vectors():
    from mellond import cbor_decode, cbor_encode

    for obj, hx in [(0, "00"), (24, "1818"), (-1, "20"), (-7, "26"), (1000, "1903e8"),
                    (b"\x01", "4101"), ("a", "6161"), ([1, 2], "820102"),
                    ({1: 2}, "a10102"), (True, "f5"), (False, "f4"), (None, "f6")]:
        assert cbor_encode(obj).hex() == hx
        assert cbor_decode(bytes.fromhex(hx))[0] == obj


def test_cbor_maps_are_canonical():
    from mellond import cbor_encode

    # CTAP2 canonical order: shorter encoded keys first, then bytewise.
    assert cbor_encode({-3: 0, 3: 0, -1: 0, 1: 0}).hex() == "a4010003002000" + "2200"
    assert cbor_encode({"sig": 1, "fmt": 2, "x5c": 3}).hex().startswith("a3")
    assert list(__import__("mellond").cbor_decode(
        cbor_encode({"bb": 1, "a": 2, "ccc": 3}))[0]) == ["a", "bb", "ccc"]


def test_cbor_decode_rejects_garbage():
    import pytest

    from mellond import cbor_decode

    for bad in ["", "18", "5a00", "7f", "9f", "c0", "f90000", "a1810102", "4201"]:
        with pytest.raises((ValueError, IndexError)):
            cbor_decode(bytes.fromhex(bad))


def frames(cid, cmd, data):
    """Fragment a CTAPHID message into 64-byte reports."""
    pkts = [struct.pack(">IBH", cid, 0x80 | cmd, len(data)) + data[:57]]
    data, seq = data[57:], 0
    while data:
        pkts.append(struct.pack(">IB", cid, seq) + data[:59])
        data, seq = data[59:], seq + 1
    return [p.ljust(64, b"\0") for p in pkts]


def collect(out, cid=1):
    """Reassemble replies into [(cmd, data)]; cmd has the 0x80 bit cleared."""
    msgs, cur = [], None
    for p in out:
        assert struct.unpack(">I", p[:4])[0] == cid
        if p[4] & 0x80:
            cur = [p[4] & 0x7F, struct.unpack(">H", p[5:7])[0], p[7:64]]
            msgs.append(cur)
        else:
            cur[2] += p[5:64]
    return [(m[0], m[2][:m[1]]) for m in msgs]


def ctap2_rig(forward=lambda apdu: b"\x90\x00"):
    out = []
    return Ctap(out.append, forward, ctap2=True), out


def call(c, out, cmd, data, cid=1):
    """Send one CTAPHID message; return the replies it produced."""
    del out[:]
    for p in frames(cid, cmd, data):
        c.packet(p)
    return collect(out, cid)


def test_ctap2_off_by_default():
    from mellond import CTAP2

    assert not CTAP2
    c, out = rig()
    assert call(c, out, 0x10, b"\x04") == [(0x3F, b"\x01")]


def test_ctap2_init_advertises_cbor_not_nmsg():
    c, out = ctap2_rig()
    c.packet(init_pkt(BCAST, 0x06, b"12345678"))
    assert out[0][23] == 0x04  # CBOR set, NMSG clear


def test_get_info():
    from mellond import cbor_decode

    c, out = ctap2_rig()
    [(cmd, data)] = call(c, out, 0x10, b"\x04")
    assert cmd == 0x10 and data[0] == 0
    info = cbor_decode(data, 1)[0]
    assert info[1] == ["U2F_V2", "FIDO_2_0"]
    assert info[3] == bytes(16)
    assert info[4] == {"rk": False, "up": True}  # no clientPin/uv advertised


def test_ctap2_unknown_command_and_cancel():
    c, out = ctap2_rig()
    assert call(c, out, 0x10, b"\x77") == [(0x10, b"\x01")]
    assert call(c, out, 0x11, b"") == []  # cancel: silent
