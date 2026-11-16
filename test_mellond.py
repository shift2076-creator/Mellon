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
