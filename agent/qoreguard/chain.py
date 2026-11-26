"""Thin, explicit wrapper around the QoreChain testnet (qorechain-diana).

Everything the agent needs to talk to the chain lives here: account state,
post-quantum key registration, the authenticator link that gives the agent its
mandatory limits, the scoped EVM window, and the relayer path that submits an
agent-signed `MsgExecuteEVM`.

Only testnet. Every key is a throwaway key with no value.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from qorsdk import (
    AuthNonce,
    PqcKeypair,
    Secp256k1Account,
    decode_tx_error,
    derive_native_account,
    derive_pqc_canonical,
    evm_auth_sign_bytes_v2,
    fetch_auth_nonce,
    fetch_authenticators,
    fetch_pqc_status,
    fetch_spending_rules,
    get_evm_window,
)
from qorsdk import decode_execution_results, ensure_pqc_registered
from qorsdk.tx import build_hybrid_tx, hybrid_sign_and_broadcast

from .config import Settings


@dataclass
class TxOutcome:
    tx_hash: str
    code: int
    raw_log: str
    executions: list[Any]

    @property
    def ok(self) -> bool:
        return self.code == 0


class Chain:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._http = httpx.Client(timeout=40.0)

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        r = self._http.get(self.cfg.rest + path, params=params)
        r.raise_for_status()
        return r.json()

    def account_state(self, address: str) -> tuple[int, int]:
        """(account_number, sequence) from the auth module."""
        data = self.get(f"/cosmos/auth/v1beta1/accounts/{address}")
        acct = data["account"]
        base = acct.get("base_account") or acct.get("base_vesting_account", {}).get("base_account") or acct
        return int(base["account_number"]), int(base["sequence"])

    def balances(self, address: str) -> dict[str, int]:
        data = self.get(f"/cosmos/bank/v1beta1/balances/{address}")
        out: dict[str, int] = {}
        for coin in data.get("balances", []):
            out[coin["denom"]] = int(coin["amount"])
        return out

    def qor(self, address: str) -> float:
        return self.balances(address).get("uqor", 0) / 1_000_000

    def pqc_status(self, address: str) -> dict:
        st = fetch_pqc_status(self.cfg.rest, address)
        return {"registered": bool(getattr(st, "registered", False)), "pubkey": getattr(st, "public_key", b"")}

    def auth_nonce(self, owner: str, agent_pubkey: bytes) -> AuthNonce:
        return fetch_auth_nonce(self, owner, agent_pubkey)

    def spending_rules(self, owner: str) -> Any:
        return fetch_spending_rules(self, owner)

    def authenticators(self, owner: str) -> Any:
        return fetch_authenticators(self, owner)

    def evm_window(self, address: str) -> Any:
        return get_evm_window(self, address)

    def tx(self, tx_hash: str) -> dict:
        return self.get(f"/cosmos/tx/v1beta1/txs/{tx_hash}")

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def ensure_pqc(self, account: Secp256k1Account, mnemonic: str, wait: float = 6.0) -> dict:
        """Register the account's canonical ML-DSA-87 key (idempotent)."""
        kp = derive_pqc_canonical(account.address, mnemonic)
        acct_no, seq = self.account_state(account.address)
        fee = {"amount": [{"denom": "uqor", "amount": str(self.cfg.fee_uqor)}], "gas": str(self.cfg.gas)}
        res = ensure_pqc_registered(
            account=account,
            pqc_keypair=kp,
            chain_id=self.cfg.chain_id,
            account_number=acct_no,
            sequence=seq,
            rest_url=self.cfg.rest,
            fee=fee,
        )
        if not getattr(res, "already_registered", True):
            time.sleep(wait)
        return {
            "already_registered": bool(getattr(res, "already_registered", False)),
            "tx_hash": getattr(res, "tx_hash", None),
            "pubkey_sha256_len": len(kp.public_key),
        }

    def send_hybrid(
        self,
        account: Secp256k1Account,
        pqc_keypair: PqcKeypair,
        messages: list[Any],
        memo: str = "",
        gas: int | None = None,
        fee_uqor: int | None = None,
    ) -> TxOutcome:
        """Sign with the account's classical + ML-DSA-87 keys and broadcast."""
        gas = gas or self.cfg.gas
        fee_uqor = fee_uqor or self.cfg.fee_uqor
        acct_no, seq = self.account_state(account.address)
        fee = {"amount": [{"denom": "uqor", "amount": str(fee_uqor)}], "gas": str(gas)}

        resp = hybrid_sign_and_broadcast(
            lambda version: build_hybrid_tx(
                account=account,
                pqc_keypair=pqc_keypair,
                messages=messages,
                fee=fee,
                chain_id=self.cfg.chain_id,
                account_number=acct_no,
                sequence=seq,
                memo=memo,
                sign_bytes_version=version,
            ),
            chain_id=self.cfg.chain_id,
            rest_url=self.cfg.rest,
            mode="block",
        )
        return self._outcome(resp)

    def _outcome(self, resp: dict) -> TxOutcome:
        txr = resp.get("tx_response") or resp
        tx_hash = txr.get("txhash", "")
        code = int(txr.get("code", 0) or 0)
        raw_log = txr.get("raw_log", "") or ""
        executions: list[Any] = []
        if code == 0:
            try:
                executions = decode_execution_results(resp)
            except Exception:  # a response without execution results is fine
                executions = []
        elif raw_log:
            try:
                raw_log = f"{raw_log} ({decode_tx_error(txr)})"
            except Exception:
                pass
        return TxOutcome(tx_hash=tx_hash, code=code, raw_log=raw_log[:600], executions=executions)

    # ------------------------------------------------------------------ #
    # Agent-specific building blocks
    # ------------------------------------------------------------------ #
    def evm_auth_digest(
        self,
        owner: str,
        agent_pubkey: bytes,
        to: str,
        value_wei: int,
        data: bytes,
        gas_limit: int,
        nonce: int,
    ) -> bytes:
        """The digest the linked agent key signs for one EVM call.

        v2 (chain v3.2.3+) also binds the gas limit, so a relayer cannot replay
        an authorisation with another limit. Both live networks verify v2.
        """
        return evm_auth_sign_bytes_v2(
            self.cfg.chain_id,
            owner,
            agent_pubkey,
            to=to,
            value=str(value_wei),
            data=data,
            nonce=nonce,
            gas_limit=gas_limit,
        )
