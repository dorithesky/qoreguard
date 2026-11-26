#!/usr/bin/env python3
"""Vendors sign their invoices; the agent refuses anything it cannot authenticate.

An invoice is a message, and a payment instruction that arrives unsigned is just
text someone could have written. Each vendor signs the canonical invoice string
with its own ed25519 invoice key, whose public half the owner registered for
that vendor in the treasury. Before the agent spends anything it checks:

  1. the invoice really came from the vendor (signature verifies), and
  2. the vendor it names is the one registered in the mandate.

The tampered copy (`INV-1008`) shows what happens to a message whose amount was
changed after signing: it is refused before any transaction exists.

Run:  python scripts/sign_invoices.py
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
UNSIGNED = ROOT / "demo" / "invoices.unsigned.json"
OUT = ROOT / "demo" / "invoices.json"


def vendor_key(label: str) -> tuple[Ed25519PrivateKey, bytes]:
    path = ROOT / ".keys" / f"vendor_{label}.ed25519"
    if path.exists():
        seed = path.read_bytes()
    else:
        seed = secrets.token_bytes(32)
        path.write_bytes(seed)
        os.chmod(path, 0o600)
    key = Ed25519PrivateKey.from_private_bytes(seed)
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, pub


def canonical(invoice: dict) -> bytes:
    return "|".join(
        [
            "QoreGuard.invoice.v1",
            invoice["id"],
            invoice["vendor"],
            str(invoice["amount_uqor"]),
            invoice["memo"],
            invoice.get("due", ""),
        ]
    ).encode()


def main() -> None:
    unsigned = json.loads(UNSIGNED.read_text())
    keys: dict[str, tuple[Ed25519PrivateKey, bytes]] = {}
    signed = []
    for inv in unsigned["invoices"]:
        if inv["vendor"] not in keys:
            keys[inv["vendor"]] = vendor_key(inv["vendor"])
        key, pub = keys[inv["vendor"]]
        if inv.get("tamper_note"):
            # Sign the honest version, then publish the altered one: exactly what
            # a tampered invoice looks like to the agent.
            sig = key.sign(canonical(dict(inv, amount_uqor=10_000)))
        else:
            sig = key.sign(canonical(inv))
        row = dict(inv)
        row["signer_pubkey"] = pub.hex()
        row["signature"] = sig.hex()
        signed.append(row)
        print(f"signed {row['id']:<10} {row['vendor']:<14} {row['amount_uqor']:>8} uqor")

    OUT.write_text(json.dumps({"invoices": signed}, indent=2) + "\n")
    print("\ninvoice signing keys (paste into demo/mandate.json):")
    for label, (_, pub) in keys.items():
        print(f'  "{label}": "{pub.hex()}"')
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
