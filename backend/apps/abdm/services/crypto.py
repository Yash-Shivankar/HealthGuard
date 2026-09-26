"""Encryption for HIP to HIU data transfer (AUDIT 4.2 M2).

ABDM moves health information between a Health Information Provider and a
Health Information User encrypted end to end, so the gateway relays ciphertext
it cannot read. The scheme is ephemeral Diffie-Hellman plus an AEAD cipher:
each side generates a key pair, they exchange public keys and nonces through
the consent flow, derive a shared secret, and the payload is encrypted with it.

## What is verified here and what is not

**The cryptography is standard and is fully tested**: X25519 key agreement,
HKDF-SHA256 derivation, AES-256-GCM. Round-trip, tamper detection, wrong-key
rejection and nonce uniqueness all have tests, and none of that depends on
ABDM.

**The wire envelope is not verified** - which fields ABDM expects, their names,
their encodings, and whether the salt/nonce are combined the way this does it.
That is the same unverified layer as ``endpoints.py`` and for the same reason
(AUDIT 11.3). Check it against the sandbox before enabling.

## Why not roll anything

Every primitive here comes from ``cryptography``. There is no custom padding,
no custom KDF, no hand-written cipher mode. AES-GCM is used because it
authenticates as well as encrypts: a payload altered in transit fails to
decrypt rather than decrypting to something plausible, which on a clinical
record is the difference between a failure and a silent corruption.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

#: AES-256.
KEY_LENGTH = 32
#: 96 bits, the size GCM is specified for. Larger or smaller needs extra
#: derivation and buys nothing.
NONCE_LENGTH = 12
SALT_LENGTH = 32

#: Bound into the derivation so a key derived for this purpose cannot be
#: reused for another one even if the same shared secret appears elsewhere.
HKDF_INFO = b"healthguard-abdm-hip-hiu-v1"


class DecryptionError(Exception):
    """The payload could not be decrypted, or was altered in transit.

    One exception for both: distinguishing "wrong key" from "tampered" tells an
    attacker which of the two they achieved.
    """


@dataclass(frozen=True)
class KeyMaterial:
    """One side's ephemeral key pair plus the nonce it contributes.

    Ephemeral per transfer, never stored. A long-lived key here would mean one
    compromise retrospectively decrypts every record ever sent - the property
    ephemeral keys exist to prevent.
    """

    private_key: X25519PrivateKey
    nonce: bytes

    @property
    def public_key_b64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            encoding=Encoding.Raw, format=PublicFormat.Raw
        )
        return base64.b64encode(raw).decode("ascii")

    @property
    def nonce_b64(self) -> str:
        return base64.b64encode(self.nonce).decode("ascii")


@dataclass(frozen=True)
class EncryptedPayload:
    """What crosses the wire.

    ABDM-SPECIFIC: verify. The field names and encoding are a reasonable
    reading of the specification, not a confirmed one.
    """

    ciphertext_b64: str
    sender_public_key_b64: str
    sender_nonce_b64: str

    def as_dict(self) -> dict[str, str]:
        return {
            "encryptedData": self.ciphertext_b64,
            "publicKey": self.sender_public_key_b64,
            "nonce": self.sender_nonce_b64,
        }


def generate_key_material() -> KeyMaterial:
    """A fresh ephemeral key pair and nonce for one transfer."""
    return KeyMaterial(
        private_key=X25519PrivateKey.generate(),
        nonce=os.urandom(SALT_LENGTH),
    )


def _derive_key(
    *, private_key: X25519PrivateKey, peer_public_key_b64: str, salt: bytes
) -> bytes:
    """X25519 agreement, then HKDF-SHA256 to a 256-bit AES key.

    The raw DH output is **not** used as a key directly: it is not uniformly
    distributed, and feeding it straight to a cipher is a well-known mistake.
    HKDF is the step that fixes that.
    """
    try:
        peer = X25519PublicKey.from_public_bytes(base64.b64decode(peer_public_key_b64))
    except Exception as exc:  # noqa: BLE001 - any malformed key is one error
        raise DecryptionError("Peer public key is not a valid X25519 key.") from exc

    shared = private_key.exchange(peer)

    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        info=HKDF_INFO,
    ).derive(shared)


def _combine_nonces(sender_nonce: bytes, receiver_nonce: bytes) -> tuple[bytes, bytes]:
    """Both sides contribute, so neither alone controls the salt or the IV.

    A nonce chosen entirely by one party is a nonce that party can repeat, and
    a repeated GCM nonce under the same key is catastrophic - it leaks the
    XOR of two plaintexts and the authentication key. XOR-ing both
    contributions means a repeat needs both sides to repeat simultaneously.

    Returns ``(hkdf_salt, gcm_iv)``.
    """
    salt = bytes(a ^ b for a, b in zip(sender_nonce, receiver_nonce))
    # The IV is the tail of the combined material; GCM wants 96 bits.
    return salt, salt[-NONCE_LENGTH:]


def encrypt(
    *,
    plaintext: bytes,
    sender: KeyMaterial,
    receiver_public_key_b64: str,
    receiver_nonce_b64: str,
) -> EncryptedPayload:
    """Encrypt a payload for one receiver."""
    receiver_nonce = base64.b64decode(receiver_nonce_b64)
    salt, iv = _combine_nonces(sender.nonce, receiver_nonce)

    key = _derive_key(
        private_key=sender.private_key,
        peer_public_key_b64=receiver_public_key_b64,
        salt=salt,
    )
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)

    return EncryptedPayload(
        ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
        sender_public_key_b64=sender.public_key_b64,
        sender_nonce_b64=sender.nonce_b64,
    )


def decrypt(
    *,
    payload: EncryptedPayload,
    receiver: KeyMaterial,
) -> bytes:
    """Decrypt a payload addressed to us.

    Raises :class:`DecryptionError` on a wrong key **and** on tampering. GCM
    authenticates, so an altered ciphertext fails here rather than decrypting
    to something plausible - on a clinical record that is the difference
    between a visible failure and a silent corruption.
    """
    sender_nonce = base64.b64decode(payload.sender_nonce_b64)
    salt, iv = _combine_nonces(sender_nonce, receiver.nonce)

    key = _derive_key(
        private_key=receiver.private_key,
        peer_public_key_b64=payload.sender_public_key_b64,
        salt=salt,
    )

    try:
        return AESGCM(key).decrypt(iv, base64.b64decode(payload.ciphertext_b64), None)
    except InvalidTag as exc:
        raise DecryptionError(
            "Payload failed authentication: wrong key, or altered in transit."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - malformed base64 and friends
        raise DecryptionError("Payload could not be decrypted.") from exc
