"""QoreSeal: the invoice archive only the owner can open.

A treasury agent's invoices are the most sensitive thing it handles — amounts,
what the studio buys, who it buys from. QoreGuard therefore never leaves them
in plaintext next to the payment records:

1. the vendor signs the invoice (ed25519, verified before anything else),
2. the agent seals that signed document to the **owner's ML-KEM-1024 key**
   (`qorechain-qoreseal`, hybrid X25519 + ML-KEM, envelope bound to chain id,
   purpose and an AAD of the invoice id), and
3. only a 32-byte digest of the sealed envelope goes on chain, in the payment's
   memo. The chain — and anyone reading this public repository — sees a
   commitment, never the invoice.

The owner re-derives the same sealing key from the same mnemonic
(`seal_seed_from_mnemonic`), so the archive can be opened offline, years later,
without any service: `open_invoice()` returns the exact bytes that were sealed,
and the vendor's signature over them still verifies.

Anyone without the ML-KEM secret key — a quantum computer included, since
ML-KEM-1024 is the post-quantum KEM — gets an authentication failure, not a
padded plaintext.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import qoreseal

#: Bound into every envelope, so a sealed invoice cannot be replayed as
#: something else (another purpose, another chain, another invoice id).
PURPOSE = "qoreguard/invoice/v1"


@dataclass(frozen=True)
class Sealed:
    """One sealed invoice: where it is, and what the chain commits to."""

    invoice_id: str
    path: Path
    envelope_sha256: str
    envelope_len: int
    plaintext_sha256: str

    @property
    def memo(self) -> str:
        """The invariant part of the on-chain memo: a commitment to this exact
        envelope. 40 hex characters keep the memo short and the commitment
        unforgeable."""
        return f"seal:v1:{self.envelope_sha256[:40]}"

    def as_dict(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "envelope": str(self.path),
            "envelope_sha256": self.envelope_sha256,
            "envelope_len": self.envelope_len,
            "plaintext_sha256": self.plaintext_sha256,
            "memo_commitment": self.memo,
        }


def seal_keys(mnemonic: str) -> "qoreseal.SealKeyPair":
    """The owner's QoreSeal key pair, derived from the account mnemonic."""
    return qoreseal.seal_keys_from_seed(qoreseal.seal_seed_from_mnemonic(mnemonic))


def _aad(invoice_id: str) -> bytes:
    return f"qoreguard:invoice:{invoice_id}".encode()


def seal_invoice(
    keys: "qoreseal.SealKeyPair",
    invoice_id: str,
    plaintext: bytes,
    out_dir: Path,
    *,
    chain_id: str = "qorechain-diana",
) -> Sealed:
    """Encrypt one signed invoice to the owner's key and write the envelope."""
    envelope = qoreseal.seal_envelope(
        chain_id=chain_id,
        purpose=PURPOSE,
        recipients=[
            qoreseal.SealRecipient(
                mlkem_public_key=keys.mlkem_public_key,
                x25519_public_key=keys.x25519_public_key,
            )
        ],
        plaintext=plaintext,
        aad=_aad(invoice_id),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{invoice_id}.sealed"
    path.write_bytes(envelope)
    return Sealed(
        invoice_id=invoice_id,
        path=path,
        envelope_sha256=hashlib.sha256(envelope).hexdigest(),
        envelope_len=len(envelope),
        plaintext_sha256=hashlib.sha256(plaintext).hexdigest(),
    )


def open_invoice(
    keys: "qoreseal.SealKeyPair",
    sealed: Sealed | Path,
    invoice_id: str,
    *,
    chain_id: str = "qorechain-diana",
) -> bytes:
    """Decrypt a sealed invoice with the owner's keys.

    Raises `qoreseal.QoreSealError` if the envelope was sealed to another
    recipient, for another chain, purpose or invoice id, or if it was modified.
    """
    path = sealed.path if isinstance(sealed, Sealed) else sealed
    return qoreseal.open_envelope(
        chain_id=chain_id,
        purpose=PURPOSE,
        envelope=path.read_bytes(),
        keys=keys,
        aad=_aad(invoice_id),
    )


def roundtrip_check(keys: "qoreseal.SealKeyPair", sealed: Sealed, *, chain_id: str = "qorechain-diana") -> dict:
    """Open the archive, prove it matches, and prove a stranger cannot."""
    plaintext = open_invoice(keys, sealed, sealed.invoice_id, chain_id=chain_id)
    stranger_refused = False
    try:
        other = qoreseal.generate_seal_keys()
        open_invoice(other, sealed, sealed.invoice_id, chain_id=chain_id)
    except Exception:
        stranger_refused = True
    return {
        "opened_sha256": hashlib.sha256(plaintext).hexdigest(),
        "matches_plaintext": hashlib.sha256(plaintext).hexdigest() == sealed.plaintext_sha256,
        "wrong_key_refused": stranger_refused,
    }


def load_sealed(record: dict) -> Sealed:
    return Sealed(
        invoice_id=record["invoice_id"],
        path=Path(record["envelope"]),
        envelope_sha256=record["envelope_sha256"],
        envelope_len=record["envelope_len"],
        plaintext_sha256=record["plaintext_sha256"],
    )


__all__ = ["PURPOSE", "Sealed", "seal_keys", "seal_invoice", "open_invoice", "roundtrip_check", "load_sealed"]
