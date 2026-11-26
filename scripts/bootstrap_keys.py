#!/usr/bin/env python3
"""Create throwaway QoreChain testnet key material for QoreGuard.

Writes .keys/keys.env (mode 600, gitignored) with:
  - the owner account (canonical PQC-required treasury account)
  - the relayer account (gas only, submits the agent's requests)
  - the agent ed25519 hot key (signs spend intents, holds nothing)
  - a demo vendor account (recipient of the first payments)

Nothing here touches mainnet, and every key is a throwaway testnet key.
Run:  python scripts/bootstrap_keys.py
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from qorsdk import derive_native_account, generate_mnemonic, derive_pqc_canonical
from qorsdk.address import bech32_to_hex

ROOT = Path(__file__).resolve().parent.parent
KEYS = ROOT / ".keys"
ENV = KEYS / "keys.env"


def main() -> None:
    if ENV.exists():
        raise SystemExit(f"{ENV} already exists — refusing to overwrite key material")

    KEYS.mkdir(parents=True, exist_ok=True)
    os.chmod(KEYS, 0o700)

    owner_mn = generate_mnemonic(256)
    relayer_mn = generate_mnemonic(256)
    vendor_mn = generate_mnemonic(256)

    owner = derive_native_account(owner_mn)
    relayer = derive_native_account(relayer_mn)
    vendor = derive_native_account(vendor_mn)

    agent_seed = secrets.token_bytes(32)
    (KEYS / "agent_ed25519.key").write_bytes(agent_seed)
    os.chmod(KEYS / "agent_ed25519.key", 0o600)

    lines = [
        "# QoreGuard throwaway testnet key material — NEVER commit, never use on mainnet",
        f"QORE_OWNER_MNEMONIC=\"{owner_mn}\"",
        f"QORE_RELAYER_MNEMONIC=\"{relayer_mn}\"",
        f"QORE_VENDOR_MNEMONIC=\"{vendor_mn}\"",
        "QORE_AGENT_KEY_PATH=.keys/agent_ed25519.key",
        "",
    ]
    ENV.write_text("\n".join(lines))
    os.chmod(ENV, 0o600)

    owner_pqc = derive_pqc_canonical(owner.address, owner_mn)
    relayer_pqc = derive_pqc_canonical(relayer.address, relayer_mn)

    print("wrote", ENV)
    for name, acct, pqc in (
        ("owner  ", owner, owner_pqc),
        ("relayer", relayer, relayer_pqc),
        ("vendor ", vendor, None),
    ):
        print(
            f"{name} native={acct.address}  evm={bech32_to_hex(acct.address)}"
            + (f"  pqc_pk_sha256={__import__('hashlib').sha256(pqc.public_key).hexdigest()[:16]}" if pqc else "")
        )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    pk = Ed25519PrivateKey.from_private_bytes(agent_seed).public_key()
    raw = pk.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print(f"agent   ed25519_pubkey={raw.hex()}")


if __name__ == "__main__":
    main()
