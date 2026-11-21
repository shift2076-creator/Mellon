"""Interop check for MELLON_CTAP2: python-fido2 (a real CTAP2 client) talks to
mellond's Ctap through CTAPHID framing; the 'phone' is a software U2F key that
uses real ECDSA, so every signature is verified cryptographically.
Skipped unless `pip install fido2 cryptography` is available."""
import datetime
import os
import struct

import pytest

pytest.importorskip("fido2")
pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from fido2.attestation import FidoU2FAttestation  # noqa: E402
from fido2.cose import ES256  # noqa: E402
from fido2.ctap import CtapDevice, CtapError  # noqa: E402
from fido2.ctap2 import Ctap2  # noqa: E402
from fido2.hid import CAPABILITY  # noqa: E402

from mellond import Ctap  # noqa: E402

RAW = serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint


class SoftU2F:
    """What the Android app does, in software."""

    def __init__(self):
        self.keys, self.ctr, self.touches = {}, 0, 0
        self.att = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Mellon")])
        now = datetime.datetime(2026, 1, 1)
        self.cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                     .public_key(self.att.public_key()).serial_number(1)
                     .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=9999))
                     .sign(self.att, hashes.SHA256()))

    def __call__(self, apdu):
        ins, p1 = apdu[1], apdu[2]
        n = struct.unpack(">H", apdu[5:7])[0]
        d = apdu[7:7 + n]
        if ins == 1:
            chal, app = d[:32], d[32:64]
            key, kid = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
            self.keys[kid] = (key, app)
            pub = key.public_key().public_bytes(*RAW)
            sig = self.att.sign(b"\0" + app + chal + kid + pub, ec.ECDSA(hashes.SHA256()))
            cert = self.cert.public_bytes(serialization.Encoding.DER)
            self.touches += 1
            return b"\x05" + pub + bytes([len(kid)]) + kid + cert + sig + b"\x90\x00"
        chal, app, kid = d[:32], d[32:64], d[65:65 + d[64]]
        if kid not in self.keys or self.keys[kid][1] != app:
            return b"\x6a\x80"
        if p1 == 7:
            return b"\x69\x85"
        self.ctr += 1
        self.touches += 1
        c = struct.pack(">I", self.ctr)
        sig = self.keys[kid][0].sign(app + b"\x01" + c + chal, ec.ECDSA(hashes.SHA256()))
        return b"\x01" + c + sig + b"\x90\x00"


class Device(CtapDevice):
    """A CTAPHID channel straight into mellond.Ctap (no /dev/uhid)."""

    def __init__(self, phone):
        self.out = []
        self.ctap = Ctap(self.out.append, phone, ctap2=True)

    @property
    def capabilities(self):
        return CAPABILITY.CBOR

    def call(self, cmd, data=b"", event=None, on_keepalive=None):
        self.out.clear()
        pkts = [struct.pack(">IBH", 1, 0x80 | cmd, len(data)) + data[:57]]
        data, seq = data[57:], 0
        while data:
            pkts.append(struct.pack(">IB", 1, seq) + data[:59])
            data, seq = data[59:], seq + 1
        for p in pkts:
            self.ctap.packet(p.ljust(64, b"\0"))
        first = [p for p in self.out if p[4] & 0x80 and p[4] != 0xBB][-1]
        n = struct.unpack(">H", first[5:7])[0]
        i = self.out.index(first)
        body = first[7:64] + b"".join(p[5:64] for p in self.out[i + 1:])
        return body[:n]

    def close(self):
        pass

    @classmethod
    def list_devices(cls):
        return iter(())


def test_python_fido2_registers_and_authenticates():
    soft = SoftU2F()
    dev = Device(soft)
    ctap = Ctap2(dev)
    info = ctap.info
    assert "FIDO_2_0" in info.versions and not info.options.get("rk")

    cdh = os.urandom(32)
    rp = {"id": "example.com", "name": "Example"}
    att = ctap.make_credential(cdh, rp, {"id": b"u", "name": "u"},
                               [{"alg": -7, "type": "public-key"}])
    assert att.fmt == "fido-u2f"
    FidoU2FAttestation().verify(att.att_stmt, att.auth_data, cdh)  # raises if invalid
    cred = att.auth_data.credential_data
    assert bytes(cred.aaguid) == bytes(16)  # fido-u2f requires an all-zero aaguid
    pub = ES256.from_cryptography_key(
        ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), soft.keys[cred.credential_id][0].public_key().public_bytes(*RAW)))
    assert dict(cred.public_key) == dict(pub)

    cdh2 = os.urandom(32)
    asr = ctap.get_assertion("example.com", cdh2,
                             [{"id": cred.credential_id, "type": "public-key"}])
    assert asr.auth_data.counter == 1 and asr.auth_data.flags & 1
    pub.verify(asr.auth_data + cdh2, asr.signature)  # raises if invalid

    with pytest.raises(CtapError) as e:  # a credential we never made
        ctap.get_assertion("example.com", cdh2, [{"id": b"x" * 16, "type": "public-key"}])
    assert e.value.code == CtapError.ERR.NO_CREDENTIALS
    with pytest.raises(CtapError) as e:  # excluded: already registered for this rp
        ctap.make_credential(cdh, rp, {"id": b"u", "name": "u"},
                             [{"alg": -7, "type": "public-key"}],
                             exclude_list=[{"id": cred.credential_id, "type": "public-key"}])
    assert e.value.code == CtapError.ERR.CREDENTIAL_EXCLUDED
    assert soft.touches == 2  # register + assertion; the probes never prompt
