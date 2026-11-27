"""QoreSign: the owner's approval, signed on the owner's own device.

An agent that pays bills is only trustworthy if "the owner said yes" is a
signature, not a flag in a JSON file. QoreGuard reads approvals as
**Sign In With QoreChain** messages (`qorechain-qoresign`):

* The owner's wallet renders a SIWQ message and signs it with the *hybrid* key
  pair — secp256k1 for the classical layer, ML-DSA-87 (FIPS 204) for the
  post-quantum one. No browser session, no site login, no shared secret.
* The agent verifies it with `verify_siwq(require_pq=True)` against the
  **ML-DSA-87 key that is registered for the owner account on chain**
  (`fetch_pqc_status`), not against a key the agent was handed. A classical-only
  signature is refused: the Q-Day attacker who has the owner's ECDSA key still
  cannot approve a payment.
* The message binds the exact invoice, vendor, amount and the treasury
  contract's current `approvalNonce`, and it expires. A replayed approval, an
  approval for another amount, or one whose nonce has moved on is refused
  before a transaction is built.

Why SIWQ rather than a bespoke JSON blob: the format is specified
(`spec/siwq-v1.md`), implemented by every QoreChain wallet and library, and
`verify_siwq` is the same check a dApp performs for a sign-in — so an approval
here is a signature the ecosystem already knows how to read.
"""
from __future__ import annotations

import datetime as _dt
import secrets
from dataclasses import dataclass

import qoresign

#: The RFC-3986 authority that asks for approvals. Fixed, because it is part of
#: what the owner signs and what the verifier checks.
DOMAIN = "qoreguard.approval"
URI = "qorechain:qoreguard.approval"
#: How long an approval stays usable, seconds.
DEFAULT_TTL_SECONDS = 15 * 60


def statement(invoice_id: str, vendor_id: int, vendor_label: str, amount: int, denom: str, approval_nonce: int) -> str:
    """The one line the owner reads and signs. Everything the payment commits
    to is in it, so the owner is approving that payment and nothing else."""
    unit = "fUSD" if denom.startswith("erc20:") else "uqor"
    return (
        f"Approve {invoice_id}: pay vendor {vendor_id} ({vendor_label}) "
        f"{amount} {unit}, treasury approval nonce {approval_nonce}"
    )


def _iso(when: _dt.datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Request:
    """What the owner's wallet needs to render, and what the agent checks."""

    message: str
    nonce: str
    invoice_id: str
    vendor_id: int
    vendor_label: str
    amount: int
    denom: str
    approval_nonce: int

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "nonce": self.nonce,
            "invoice_id": self.invoice_id,
            "vendor_id": self.vendor_id,
            "vendor_label": self.vendor_label,
            "amount": self.amount,
            "denom": self.denom,
            "approval_nonce": self.approval_nonce,
        }


def build_request(
    *,
    account: str,
    chain_id: str,
    invoice_id: str,
    vendor_id: int,
    vendor_label: str,
    amount: int,
    denom: str,
    approval_nonce: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: _dt.datetime | None = None,
) -> Request:
    """Render the SIWQ message the owner will sign."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    nonce = "appr" + secrets.token_hex(6)
    fields = qoresign.SiwqFields(
        domain=DOMAIN,
        address=account,
        uri=URI,
        chain_id=chain_id,
        nonce=nonce,
        issued_at=_iso(now),
        statement=statement(invoice_id, vendor_id, vendor_label, amount, denom, approval_nonce),
        expiration_time=_iso(now + _dt.timedelta(seconds=ttl_seconds)),
    )
    return Request(
        message=qoresign.create_siwq_message(fields),
        nonce=nonce,
        invoice_id=invoice_id,
        vendor_id=vendor_id,
        vendor_label=vendor_label,
        amount=amount,
        denom=denom,
        approval_nonce=approval_nonce,
    )


def sign(request: Request, mnemonic: str, account: str) -> str:
    """Sign on the owner's side: hybrid secp256k1 + ML-DSA-87, as a wallet does.

    Returns the signature as JSON, the transport form every implementation
    shares. In a real deployment this runs in the owner's wallet; in the demo it
    runs from the owner's `.keys/keys.env` mnemonic, which the agent never has.
    """
    classical = qoresign.derive_classical_key(mnemonic, qoresign.ClassicalKeyType.ETH_SECP256K1)
    pq = qoresign.derive_canonical_pq_key(mnemonic, account)
    signature = qoresign.sign_siwq(request.message, classical, pq_key=pq)
    return qoresign.siwq_signature_to_json(signature)


def verify(
    request: Request,
    signature_json: str,
    *,
    chain_pq_public_key: bytes,
    chain_id: str,
) -> dict:
    """Verify an approval against the owner's on-chain ML-DSA-87 key.

    Raises `qoresign.QoreSignError` when the signature is not the owner's, is
    classical-only, is for another domain or nonce, or has expired.
    """
    signature = qoresign.siwq_signature_from_json(signature_json)
    fields = qoresign.verify_siwq(
        request.message,
        signature,
        domain=DOMAIN,
        nonce=request.nonce,
        pq_public_key=chain_pq_public_key,
        chain_id=chain_id,
        require_pq=True,
    )
    expected = statement(
        request.invoice_id, request.vendor_id, request.vendor_label, request.amount, request.denom, request.approval_nonce
    )
    if fields.statement != expected:
        raise qoresign.QoreSignError(
            "SIWQ_STATEMENT_MISMATCH",
            f"approval is for {fields.statement!r}, expected {expected!r}",
        )
    return {
        "address": fields.address,
        "statement": fields.statement,
        "nonce": fields.nonce,
        "issued_at": fields.issued_at,
        "expiration_time": fields.expiration_time,
    }


__all__ = ["DOMAIN", "URI", "Request", "statement", "build_request", "sign", "verify"]
