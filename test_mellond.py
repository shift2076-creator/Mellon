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


# --- CTAP2 makeCredential / getAssertion against a scripted phone ---------

import hashlib  # noqa: E402

from mellond import cbor_decode, cbor_encode  # noqa: E402

APP = hashlib.sha256(b"example.com").digest()
CDH = bytes(range(32))
PUB = b"\x04" + bytes(range(1, 33)) + bytes(range(33, 65))
KID = bytes(range(100, 116))
CERT = b"\x30\x82\x01\x00" + bytes(256)
SIG = b"\x30\x44" + bytes(range(0x44))
RP = {"id": "example.com", "name": "Example"}
ES256 = [{"alg": -7, "type": "public-key"}]


class Phone:
    """Scripted U2F phone: records APDUs, answers 6985 `waits` times first,
    knows the key handles in `owns`."""

    def __init__(self, waits=0, refuse=False, owns=(KID,)):
        self.apdus, self.waits, self.refuse, self.owns = [], waits, refuse, owns

    def __call__(self, apdu):
        self.apdus.append(apdu)
        ins, p1 = apdu[1], apdu[2]
        n = struct.unpack(">H", apdu[5:7])[0]
        d = apdu[7:7 + n]
        assert apdu[:2] == b"\0" + bytes([ins]) and apdu[3:5] == b"\0\0"
        assert apdu[7 + n:] == b"\0\0"  # extended-length Le
        if ins == 2 and p1 == 7:  # check-only
            return b"\x69\x85" if d[65:] in self.owns else b"\x6a\x80"
        if self.waits > 0:
            self.waits -= 1
            return b"\x69\x85"
        if self.refuse:
            return b"\x6a\x80"
        if ins == 1:
            return b"\x05" + PUB + bytes([len(KID)]) + KID + CERT + SIG + b"\x90\x00"
        if ins == 2:
            return b"\x01\x00\x00\x00\x2a" + SIG + b"\x90\x00"
        raise AssertionError(ins)


def mc(over=None):
    """makeCredential payload; over maps CBOR keys (ints) to values, None drops one."""
    req = {1: CDH, 2: RP, 3: {"id": b"u", "name": "u"}, 4: ES256}
    req.update(over or {})
    return b"\x01" + cbor_encode({k: v for k, v in req.items() if v is not None})


def run(c, out, payload):
    """-> (CTAP2 reply, number of keepalives sent before it)."""
    [*kas, (cmd, data)] = call(c, out, 0x10, payload)
    assert cmd == 0x10 and all(k == (0x3B, b"\x02") for k in kas)
    return data, len(kas)


def test_make_credential_wraps_u2f_register_as_fido_u2f():
    ph = Phone()
    c, out = ctap2_rig(ph)
    data, _ = run(c, out, mc())
    assert data[0] == 0
    resp = cbor_decode(data, 1)[0]
    assert resp[1] == "fido-u2f" and resp[3] == {"sig": SIG, "x5c": [CERT]}
    a = resp[2]
    assert a[:32] == APP and a[32] == 0x41 and a[33:37] == bytes(4) and a[37:53] == bytes(16)
    assert struct.unpack(">H", a[53:55])[0] == len(KID) and a[55:71] == KID
    assert cbor_decode(a, 71)[0] == {1: 2, 3: -7, -1: 1, -2: PUB[1:33], -3: PUB[33:65]}
    # REGISTER with challenge = clientDataHash, application = rpIdHash
    assert ph.apdus == [bytes([0, 1, 3, 0, 0, 0, 64]) + CDH + APP + b"\0\0"]


def test_make_credential_polls_with_keepalives_then_succeeds():
    ph = Phone(waits=3)
    c, out = ctap2_rig(ph)
    c.sleep = lambda s: None
    data, kas = run(c, out, mc())
    assert data[0] == 0 and kas == 3
    assert len(set(ph.apdus)) == 1  # the phone matches polls by identical request


def test_make_credential_times_out_and_can_be_refused():
    c, out = ctap2_rig(Phone(waits=10 ** 9))
    t = [0]
    c.clock = lambda: t[0]
    c.sleep = lambda s: t.__setitem__(0, t[0] + 10)
    assert run(c, out, mc())[0] == b"\x2f"  # USER_ACTION_TIMEOUT
    c, out = ctap2_rig(Phone(refuse=True))
    assert run(c, out, mc())[0] == b"\x27"  # OPERATION_DENIED


def test_make_credential_rejects_what_the_phone_cannot_do():
    c, out = ctap2_rig(Phone())
    assert run(c, out, mc())[0][0] == 0
    for kw, code in [({1: None}, 0x14), ({2: None}, 0x14), ({4: None}, 0x14),
                     ({2: {"name": "x"}}, 0x14), ({1: b"short"}, 0x03),
                     ({1: "text"}, 0x11),
                     ({4: [{"alg": -257, "type": "public-key"}]}, 0x26),
                     ({7: {"rk": True}}, 0x2B), ({7: {"uv": True}}, 0x2B),
                     ({8: b"x"}, 0x35)]:
        assert run(c, out, mc(kw))[0] == bytes([code]), kw
    assert run(c, out, b"\x01\xff")[0] == b"\x12"  # truncated CBOR
    assert run(c, out, b"\x01" + cbor_encode([1]))[0] == b"\x12"  # not a map


def test_make_credential_exclude_list_uses_check_only():
    ph = Phone()
    c, out = ctap2_rig(ph)
    other = b"o" * 16
    ex = [{"id": other, "type": "public-key"}, {"id": KID, "type": "public-key"}]
    assert run(c, out, mc({5: ex}))[0] == b"\x19"  # CREDENTIAL_EXCLUDED
    assert [a[2] for a in ph.apdus] == [7, 7]  # no fingerprint prompt was started
    assert run(c, out, mc({5: ex[:1]}))[0][0] == 0


def test_phone_unreachable_during_ctap2():
    def down(apdu):
        raise ConnectionRefusedError

    c, out = ctap2_rig(down)
    assert run(c, out, mc())[0] == b"\x7f"


def ga(over=None):
    req = {1: "example.com", 2: CDH, 3: [{"id": KID, "type": "public-key"}]}
    req.update(over or {})
    return b"\x02" + cbor_encode({k: v for k, v in req.items() if v is not None})


def test_get_assertion_reuses_the_phones_u2f_signature():
    ph = Phone()
    c, out = ctap2_rig(ph)
    data, _ = run(c, out, ga())
    assert data[0] == 0
    resp = cbor_decode(data, 1)[0]
    assert resp[1] == {"id": KID, "type": "public-key"}
    # authData = rpIdHash || flags || counter, so authData || clientDataHash is
    # byte-for-byte what the phone signed: rpIdHash || flags || counter || challenge
    assert resp[2] == APP + b"\x01\x00\x00\x00\x2a"
    assert resp[3] == SIG
    assert ph.apdus[-1] == bytes([0, 2, 3, 0, 0, 0, 81]) + CDH + APP + bytes([16]) + KID + b"\0\0"


def test_get_assertion_picks_the_allowed_credential_the_phone_owns():
    ph = Phone()
    c, out = ctap2_rig(ph)
    allow = [{"id": b"a" * 16, "type": "public-key"}, {"id": b"b" * 16, "type": "public-key"},
             {"id": KID, "type": "public-key"}]
    data, _ = run(c, out, ga({3: allow}))
    assert cbor_decode(data, 1)[0][1]["id"] == KID
    assert [a[2] for a in ph.apdus] == [7, 7, 7, 3]  # three silent probes, one prompt


def test_get_assertion_errors():
    c, out = ctap2_rig(Phone())
    for over, code in [({3: [{"id": b"z" * 16, "type": "public-key"}]}, 0x2E),
                       ({3: []}, 0x2E), ({3: None}, 0x2E),  # discoverable: not supported
                       ({1: None}, 0x14), ({2: None}, 0x14), ({2: b"x"}, 0x03),
                       ({5: {"uv": True}}, 0x2B), ({5: {"up": False}}, 0x2B),
                       ({6: b"x"}, 0x35)]:
        assert run(c, out, ga(over))[0] == bytes([code]), over
    c, out = ctap2_rig(Phone(refuse=True))
    assert run(c, out, ga())[0] == b"\x27"


def test_get_assertion_waits_for_the_fingerprint():
    ph = Phone(waits=2)
    c, out = ctap2_rig(ph)
    c.sleep = lambda s: None
    data, kas = run(c, out, ga())
    assert data[0] == 0 and kas == 2
