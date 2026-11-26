"""Who holds what.

The treasury owner is an **eth-native (unified) account**: one key, one 20-byte
identity, rendered as `qor1…` on the native lane and `0x…` on the EVM lane. That
matters here — the EVM lane attributes a transaction to the keccak-derived
address of the signing key, so a plain Cosmos-native account (whose 20 bytes come
from ripemd160(sha256(pub))) has a `0x` form the EVM lane will not accept as a
sender. The owner therefore has to be an eth_secp256k1 account, while the
relayer, which only ever sends native-lane transactions, stays a native account.
"""
from __future__ import annotations

from dataclasses import dataclass

from qorsdk import Secp256k1Account, derive_native_account
from qorsdk.unified import UnifiedAccount, derive_unified_account

from .config import Settings


@dataclass(frozen=True)
class Treasury:
    owner: UnifiedAccount
    relayer: Secp256k1Account

    @property
    def owner_qor(self) -> str:
        return self.owner.cosmos

    @property
    def owner_evm(self) -> str:
        return self.owner.evm


def load(cfg: Settings) -> Treasury:
    return Treasury(
        owner=derive_unified_account(cfg.owner_mnemonic),
        relayer=derive_native_account(cfg.relayer_mnemonic),
    )
