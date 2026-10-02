"""A software passkey (WebAuthn authenticator) for tests: real P-256 keys and signatures in the
formats browsers send, so the app's checks run through py_webauthn unchanged."""
import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(self, counting=False):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.counting = counting  # synced passkeys (iCloud, Google) always report 0
        self.sign_count = 0

    @property
    def id(self):
        return b64url(self.credential_id)

    def cose_key(self):
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"),
                            -3: numbers.y.to_bytes(32, "big")})

    def auth_data(self, rp_id, attested=False):
        flags = 0x01 | 0x04 | (0x40 if attested else 0)  # user present, verified, attested
        data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags])
        data += struct.pack(">I", self.sign_count)
        if attested:
            data += bytes(16) + struct.pack(">H", len(self.credential_id))
            data += self.credential_id + self.cose_key()
        return data

    @staticmethod
    def client_data(kind, options, origin):
        return json.dumps({"type": kind, "challenge": options["challenge"], "origin": origin,
                           "crossOrigin": False}).encode()

    def register(self, options, origin):
        """What navigator.credentials.create() gives, as passkeys.js sends it."""
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {},
                                   "authData": self.auth_data(options["rp"]["id"], True)})
        return {"id": self.id, "rawId": self.id, "type": "public-key",
                "clientExtensionResults": {}, "authenticatorAttachment": "platform",
                "response": {"clientDataJSON": b64url(self.client_data("webauthn.create",
                                                                       options, origin)),
                             "attestationObject": b64url(attestation),
                             "transports": ["internal", "hybrid"]}}

    def sign(self, options, origin, rp_id=None):
        """What navigator.credentials.get() gives, as passkeys.js sends it."""
        if self.counting:
            self.sign_count += 1
        auth = self.auth_data(rp_id or options["rpId"])
        client = self.client_data("webauthn.get", options, origin)
        signature = self.key.sign(auth + hashlib.sha256(client).digest(),
                                  ec.ECDSA(hashes.SHA256()))
        return {"id": self.id, "rawId": self.id, "type": "public-key",
                "clientExtensionResults": {}, "authenticatorAttachment": "platform",
                "response": {"clientDataJSON": b64url(client), "authenticatorData": b64url(auth),
                             "signature": b64url(signature), "userHandle": None}}
