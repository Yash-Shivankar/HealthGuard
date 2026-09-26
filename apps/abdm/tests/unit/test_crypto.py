"""Tests for HIP-to-HIU encryption (AUDIT 4.2 M2).

The cryptography here is standard and therefore fully testable without a
sandbox: round-trip, tamper detection, wrong-key rejection, nonce uniqueness.
None of it depends on ABDM.

What these tests do **not** establish is that the wire envelope matches what
ABDM expects - that is the unverified layer, and it is the same one as
``endpoints.py``.
"""

from __future__ import annotations

import base64

import pytest

from apps.abdm.services import crypto


@pytest.fixture
def parties():
    """Two sides of a transfer, each with ephemeral keys and a nonce."""
    return crypto.generate_key_material(), crypto.generate_key_material()


def _encrypt(plaintext: bytes, sender, receiver) -> crypto.EncryptedPayload:
    return crypto.encrypt(
        plaintext=plaintext,
        sender=sender,
        receiver_public_key_b64=receiver.public_key_b64,
        receiver_nonce_b64=receiver.nonce_b64,
    )


class TestRoundTrip:
    def test_the_receiver_can_read_what_the_sender_sent(self, parties):
        sender, receiver = parties
        payload = _encrypt(b'{"resourceType":"Bundle"}', sender, receiver)

        assert crypto.decrypt(payload=payload, receiver=receiver) == b'{"resourceType":"Bundle"}'

    def test_a_realistic_bundle_round_trips(self, parties):
        sender, receiver = parties
        plaintext = ("x" * 50_000).encode("utf-8")

        payload = _encrypt(plaintext, sender, receiver)
        assert crypto.decrypt(payload=payload, receiver=receiver) == plaintext

    def test_unicode_survives(self, parties):
        """Patient names are not ASCII."""
        sender, receiver = parties
        plaintext = "नमस्ते — Dr. Ünal".encode("utf-8")

        payload = _encrypt(plaintext, sender, receiver)
        assert crypto.decrypt(payload=payload, receiver=receiver).decode("utf-8") == (
            "नमस्ते — Dr. Ünal"
        )

    def test_the_ciphertext_is_not_the_plaintext(self, parties):
        sender, receiver = parties
        payload = _encrypt(b"sensitive clinical content", sender, receiver)

        assert b"sensitive" not in base64.b64decode(payload.ciphertext_b64)


class TestTamperDetection:
    """AES-GCM authenticates. An altered payload must fail rather than decrypt
    to something plausible - on a clinical record that is the difference
    between a visible failure and a silent corruption.
    """

    def test_a_modified_ciphertext_is_rejected(self, parties):
        sender, receiver = parties
        payload = _encrypt(b"original", sender, receiver)

        raw = bytearray(base64.b64decode(payload.ciphertext_b64))
        raw[0] ^= 0x01
        tampered = crypto.EncryptedPayload(
            ciphertext_b64=base64.b64encode(bytes(raw)).decode("ascii"),
            sender_public_key_b64=payload.sender_public_key_b64,
            sender_nonce_b64=payload.sender_nonce_b64,
        )

        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(payload=tampered, receiver=receiver)

    def test_a_swapped_sender_key_is_rejected(self, parties):
        sender, receiver = parties
        impostor = crypto.generate_key_material()
        payload = _encrypt(b"original", sender, receiver)

        swapped = crypto.EncryptedPayload(
            ciphertext_b64=payload.ciphertext_b64,
            sender_public_key_b64=impostor.public_key_b64,
            sender_nonce_b64=payload.sender_nonce_b64,
        )

        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(payload=swapped, receiver=receiver)

    def test_a_modified_nonce_is_rejected(self, parties):
        sender, receiver = parties
        payload = _encrypt(b"original", sender, receiver)

        raw = bytearray(base64.b64decode(payload.sender_nonce_b64))
        raw[0] ^= 0xFF
        altered = crypto.EncryptedPayload(
            ciphertext_b64=payload.ciphertext_b64,
            sender_public_key_b64=payload.sender_public_key_b64,
            sender_nonce_b64=base64.b64encode(bytes(raw)).decode("ascii"),
        )

        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(payload=altered, receiver=receiver)


class TestWrongRecipient:
    def test_a_third_party_cannot_decrypt(self, parties):
        """The whole point of encrypting end to end: the gateway relays the
        payload and cannot read it.
        """
        sender, receiver = parties
        eavesdropper = crypto.generate_key_material()
        payload = _encrypt(b"clinical content", sender, receiver)

        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(payload=payload, receiver=eavesdropper)

    def test_a_malformed_peer_key_is_rejected_clearly(self, parties):
        sender, _ = parties

        with pytest.raises(crypto.DecryptionError):
            crypto.encrypt(
                plaintext=b"x",
                sender=sender,
                receiver_public_key_b64="not-a-key",
                receiver_nonce_b64=base64.b64encode(b"0" * 32).decode(),
            )

    def test_the_error_does_not_distinguish_wrong_key_from_tampering(self, parties):
        """Telling them apart tells an attacker which of the two they achieved."""
        sender, receiver = parties
        other = crypto.generate_key_material()
        payload = _encrypt(b"x", sender, receiver)

        with pytest.raises(crypto.DecryptionError) as wrong_key:
            crypto.decrypt(payload=payload, receiver=other)

        raw = bytearray(base64.b64decode(payload.ciphertext_b64))
        raw[-1] ^= 0x01
        with pytest.raises(crypto.DecryptionError) as tampered:
            crypto.decrypt(
                payload=crypto.EncryptedPayload(
                    ciphertext_b64=base64.b64encode(bytes(raw)).decode("ascii"),
                    sender_public_key_b64=payload.sender_public_key_b64,
                    sender_nonce_b64=payload.sender_nonce_b64,
                ),
                receiver=receiver,
            )

        assert str(wrong_key.value) == str(tampered.value)


class TestKeyMaterial:
    def test_keys_are_ephemeral(self):
        """A long-lived key would mean one compromise retrospectively decrypts
        every record ever sent.
        """
        assert (
            crypto.generate_key_material().public_key_b64
            != crypto.generate_key_material().public_key_b64
        )

    def test_nonces_are_unique(self):
        nonces = {crypto.generate_key_material().nonce_b64 for _ in range(50)}
        assert len(nonces) == 50

    def test_the_public_key_is_32_raw_bytes(self):
        """X25519 public keys are 32 bytes; anything else means the encoding
        is wrong and the peer will fail to parse it.
        """
        raw = base64.b64decode(crypto.generate_key_material().public_key_b64)
        assert len(raw) == 32

    def test_both_sides_contribute_to_the_nonce(self, parties):
        """A nonce chosen entirely by one party is a nonce that party can
        repeat, and a repeated GCM nonce under the same key is catastrophic.
        """
        sender, receiver = parties
        other_receiver = crypto.generate_key_material()

        first = _encrypt(b"same plaintext", sender, receiver)
        second = _encrypt(b"same plaintext", sender, other_receiver)

        # Same sender, same plaintext, different receiver contribution ->
        # different ciphertext.
        assert first.ciphertext_b64 != second.ciphertext_b64

    def test_the_wire_shape_is_stable(self, parties):
        """ABDM-SPECIFIC: these field names are unverified. The test pins the
        shape so a change to it is a deliberate act rather than a drift.
        """
        sender, receiver = parties
        as_dict = _encrypt(b"x", sender, receiver).as_dict()

        assert set(as_dict) == {"encryptedData", "publicKey", "nonce"}
