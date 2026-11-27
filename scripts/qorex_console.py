#!/usr/bin/env python3
"""Record one message signature the browser console can re-verify.

The console page (`web/qorex.html`) verifies an arbitrary-message signature
against the owner's ML-DSA-87 key *on chain*, in the browser, with
`verifyMessageWithChain` from `@qorechain/qoresign/network` — the same call a
dApp makes after a wallet's `signArbitrary`.

This script produces the recorded signature in exactly that wire format:

  * the data is the byte string the page displays,
  * the signature is a hybrid secp256k1 + ML-DSA-87 signature over
    `"qorechain-msg-v1|" + chain_id + "|" + data` (spec/message-signing-v1.md),
  * the ML-DSA-87 public key is the one registered for the owner on chain,
    fetched from REST at the moment of signing.

It also proves the negatives the page then reproduces: a changed byte, a
classical-only signature, and another account's key are all refused.

    python scripts/qorex_console.py        # writes demo/qorex-console.json
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import qoresign  # noqa: E402
from qorsdk import fetch_pqc_status  # noqa: E402

OUT = ROOT / "demo" / "qorex-console.json"


def load_keys() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".keys" / "keys.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"')
    return values


def main() -> int:
    addrs = json.loads((ROOT / "demo" / "addresses.json").read_text())
    keys = load_keys()
    chain_id = addrs["chain_id"]
    owner = addrs["owner"]["bech32"]

    status = fetch_pqc_status(addrs["rest"], owner)
    if not status.get("registered"):
        print("owner has no ML-DSA-87 key on chain", file=sys.stderr)
        return 1
    pq_pub = status["pubkey"]
    pq_pub = base64.b64decode(pq_pub) if isinstance(pq_pub, str) else pq_pub

    nonce = json.loads((ROOT / "demo" / "p1.json").read_text())["approval"]["approval_nonce"]
    data = (
        "QoreGuard console check\n"
        f"contract {addrs['contract']}\n"
        f"owner {addrs['owner']['evm']}\n"
        f"treasury approval nonce {nonce}\n"
        "purpose: prove the owner's ML-DSA-87 key signs these exact bytes"
    ).encode()

    classical = qoresign.derive_classical_key(keys["QORE_OWNER_MNEMONIC"], qoresign.ClassicalKeyType.ETH_SECP256K1)
    pq = qoresign.derive_canonical_pq_key(keys["QORE_OWNER_MNEMONIC"], owner)
    signature = qoresign.sign_message(chain_id, owner, data, classical, pq_key=pq)
    signature_json = qoresign.message_signature_to_json(signature)

    # The check the browser will run, run here first against the chain key.
    qoresign.verify_message(chain_id, owner, data, signature, pq_public_key=pq_pub)
    verified = True

    negatives: dict[str, str] = {}

    def refusal(fn) -> str:
        try:
            fn()
            return "ACCEPTED — the signature is not bound"
        except Exception as exc:  # noqa: BLE001 - any refusal is the result
            code = getattr(exc, "code", type(exc).__name__)
            return f"refused: {code}"

    negatives["one byte changed"] = refusal(
        lambda: qoresign.verify_message(chain_id, owner, data[:-1] + b"!", signature, pq_public_key=pq_pub)
    )
    negatives["another chain id"] = refusal(
        lambda: qoresign.verify_message("qorechain-vladi", owner, data, signature, pq_public_key=pq_pub)
    )

    # A classical-only signature over the same data must not pass.
    classical_only = qoresign.sign_message(chain_id, owner, data, classical)
    negatives["classical-only signature"] = refusal(
        lambda: qoresign.verify_message(
            chain_id, owner, data, classical_only, pq_public_key=pq_pub, require_pq=True
        )
    )

    # A signature made by another account's key must not pass.
    other = qoresign.derive_classical_key(keys["QORE_VENDOR_MNEMONIC"], qoresign.ClassicalKeyType.ETH_SECP256K1)
    other_sig = qoresign.sign_message(chain_id, addrs["vendors"][0]["payee"], data, other)
    negatives["another key"] = refusal(
        lambda: qoresign.verify_message(chain_id, owner, data, other_sig, pq_public_key=pq_pub)
    )

    record = {
        "created_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "chain_id": chain_id,
        "rest": addrs["rest"],
        "signer": owner,
        "signer_evm": addrs["owner"]["evm"],
        "data_utf8": data.decode(),
        "data_hex": "0x" + data.hex(),
        "signature": json.loads(signature_json),
        "signature_json": signature_json,
        "pq_public_key_len": len(pq_pub),
        "pq_public_key_source": f"/qorechain/pqc/v1/account/{owner}",
        "verified_against_chain_key": verified,
        "negatives": negatives,
        "how": (
            "qoresign.sign_message over 'qorechain-msg-v1|' + chain_id + '|' + data with the owner's "
            "hybrid key — the exact bytes window.qorex.native.signArbitrary signs in the wallet. "
            "The browser re-verifies it with verifyMessageWithChain; no key is used there."
        ),
    }
    OUT.write_text(json.dumps(record, indent=2) + "\n")
    print(f"wrote {OUT}")
    print(f"signature {len(record['signature_json'])} bytes of JSON, pq key {len(pq_pub)}B")
    print("negatives:", json.dumps(negatives, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
