"""Configuration and throwaway key material for the QoreGuard demo.

Every key here is a testnet key created by `scripts/bootstrap_keys.py`; the
file that holds them, `.keys/keys.env`, is gitignored and mode 600.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> None:
    """Read KEY=value lines into os.environ without overriding what is set."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


load_env_file(ROOT / ".keys" / "keys.env")
load_env_file(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    chain_id: str
    rest: str
    rpc: str
    evm_rpc: str
    evm_chain_id: int
    owner_mnemonic: str
    relayer_mnemonic: str
    vendor_mnemonic: str
    agent_key_path: Path

    # Fees: the network floor is 0.1 uqor per gas. 0.15 uqor/gas keeps a
    # hybrid transaction ahead of the floor (v3.2.8 mempool orders by fee/gas).
    gas: int = 1_500_000
    fee_uqor: int = 225_000
    evm_gas: int = 900_000
    evm_gas_price_wei: int = 120_000_000_000  # 120 gwei (base fee is 100 gwei)


def settings() -> Settings:
    def need(name: str) -> str:
        value = os.environ.get(name, "")
        if not value:
            raise SystemExit(
                f"missing {name}: run `python scripts/bootstrap_keys.py` (writes .keys/keys.env) "
                "or copy config/testnet.env.example to .env"
            )
        return value

    return Settings(
        chain_id=os.environ.get("QORE_CHAIN_ID", "qorechain-diana"),
        rest=os.environ.get("QORE_REST", "https://api-testnet.qore.host"),
        rpc=os.environ.get("QORE_RPC", "https://rpc-testnet.qore.host"),
        evm_rpc=os.environ.get("QORE_EVM_RPC", "https://evm-testnet.qore.host"),
        evm_chain_id=int(os.environ.get("QORE_EVM_CHAIN_ID", "9800")),
        owner_mnemonic=need("QORE_OWNER_MNEMONIC"),
        relayer_mnemonic=os.environ.get("QORE_RELAYER_MNEMONIC", ""),
        vendor_mnemonic=os.environ.get("QORE_VENDOR_MNEMONIC", ""),
        agent_key_path=ROOT / os.environ.get("QORE_AGENT_KEY_PATH", ".keys/agent_ed25519.key"),
    )
