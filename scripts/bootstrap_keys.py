#!/usr/bin/env python3
"""Create throwaway QoreChain testnet key material for QoreGuard.

Writes .keys/keys.env (mode 600, gitignored) with:
  - the owner account, eth-native (unified): one key rendered as qor1… and 0x…
  - the relayer, a native account that only pays fees for relayed requests
  - two vendor accounts, eth-native, so their 0x addresses are ordinary EVM
    recipients
  - the agent's ed25519 hot key (signs intents, holds nothing)
  - fresh ed25519 invoice keys per vendor (see scripts/sign_invoices.py)

Nothing here touches mainnet, and every key is a throwaway testnet key.
Run:  python scripts/bootstrap_keys.py
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from qorsdk import derive_native_account, generate_mnemonic
from qorsdk.unified import derive_unified_account

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
    vendor2_mn = generate_mnemonic(256)

    owner = derive_unified_account(owner_mn)
    relayer = derive_native_account(relayer_mn)
    vendor = derive_unified_account(vendor_mn)
    vendor2 = derive_unified_account(vendor2_mn)

    seed = secrets.token_bytes(32)
    (KEYS / "agent_ed25519.key").write_bytes(seed)
    os.chmod(KEYS / "agent_ed25519.key", 0o600)

    ENV.write_text(
        "\n".join(
            [
                "# QoreGuard throwaway testnet key material — NEVER commit, never use on mainnet",
                f'QORE_OWNER_MNEMONIC="{owner_mn}"',
                f'QORE_RELAYER_MNEMONIC="{relayer_mn}"',
                f'QORE_VENDOR_MNEMONIC="{vendor_mn}"',
                f'QORE_VENDOR2_MNEMONIC="{vendor2_mn}"',
                "QORE_AGENT_KEY_PATH=.keys/agent_ed25519.key",
                "",
            ]
        )
    )
    os.chmod(ENV, 0o600)

    print("wrote", ENV)
    print(f"owner    qor1 {owner.cosmos}   0x {owner.evm}")
    print(f"relayer  qor1 {relayer.address}")
    print(f"vendor1  qor1 {vendor.cosmos}   0x {vendor.evm}")
    print(f"vendor2  qor1 {vendor2.cosmos}   0x {vendor2.evm}")
    pk = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print(f"agent    ed25519 pubkey {pk.hex()}")
    print("\nfund owner + relayer from the faucet, then run the CLI.")


if __name__ == "__main__":
    main()
