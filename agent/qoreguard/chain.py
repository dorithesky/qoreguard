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
from qorsdk.key_integrity import should_attach_proof_of_possession, sign_proof_of_possession
from qorsdk.messages import msg as compose
from qorsdk.sign_eth import sign_classical_eth, sign_hybrid_eth
from qorsdk.tx import build_hybrid_tx, broadcast, hybrid_sign_and_broadcast
from qorsdk.unified import UnifiedAccount

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
        pub = st.get("pubkey", b"")
        if isinstance(pub, str):
            import base64

            try:
                pub = base64.b64decode(pub)
            except Exception:
                pub = b""
        return {
            "registered": bool(st.get("registered", False)),
            "pubkey": pub or b"",
            "algorithm_id": st.get("algorithm_id"),
            "possession_proven": st.get("possession_proven"),
        }

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
        if not res.get("already_registered", True):
            time.sleep(wait)
        return {
            "already_registered": bool(res.get("already_registered", False)),
            "tx_hash": res.get("tx_hash"),
            "pubkey_len": len(kp.public_key),
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
            mode="sync",
        )
        outcome = self._outcome(resp)
        if outcome.code == 0 and outcome.tx_hash:
            outcome = self._with_inclusion(outcome)
        return outcome

    def _with_inclusion(self, outcome: TxOutcome, timeout: float = 25.0) -> TxOutcome:
        """Wait for the tx to be included and decode its execution results.

        A relayed payment that the contract refused still lands with `code: 0`;
        only the execution result inside it says no. `sync` broadcast has no
        result yet, so this reads the delivered transaction.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                body = self.tx(outcome.tx_hash)
            except Exception:
                time.sleep(1.5)
                continue
            txr = body.get("tx_response") or {}
            if not txr.get("height") or txr.get("height") == "0":
                time.sleep(1.5)
                continue
            try:
                executions = decode_execution_results(body)
            except Exception:
                executions = []
            return TxOutcome(
                tx_hash=outcome.tx_hash, code=int(txr.get("code", 0) or 0),
                raw_log=(txr.get("raw_log") or "")[:600], executions=executions,
            )
        return outcome

    def ensure_pqc_eth(self, owner: UnifiedAccount, wait: float = 6.0) -> dict:
        """Register the owner's canonical ML-DSA-87 key (eth-signed, idempotent).

        The registration transaction is the one message the chain accepts with
        the classical key alone (a hybrid signature would need the very key being
        registered), so it is signed with `sign_classical_eth`.
        """
        st = self.pqc_status(owner.cosmos)
        if st["registered"]:
            return {"already_registered": True, "tx_hash": None}
        acct_no, seq = self.account_state(owner.cosmos)
        attach = False
        try:
            attach = should_attach_proof_of_possession(self.cfg.chain_id, self.cfg.rest)
        except Exception:
            attach = False
        fields = {
            "sender": owner.cosmos,
            "public_key": owner.pqc.public_key,
            "algorithm_id": 1,
            "ecdsa_pubkey": owner.public_key,
            "key_type": "hybrid",
        }
        if attach:
            fields["proof_of_possession"] = sign_proof_of_possession(
                chain_id=self.cfg.chain_id, account=owner.cosmos, keypair=owner.pqc
            )
        message = compose.pqc.register_pqc_key_v2(**fields)
        fee = {"amount": [{"denom": "uqor", "amount": str(self.cfg.fee_uqor)}], "gas": str(self.cfg.gas)}
        built = sign_classical_eth(
            owner, self.cfg.chain_id, acct_no, [message], fee, seq, memo="qoreguard:register-pqc"
        )
        resp = broadcast(self.cfg.rest, built.tx_raw_bytes, mode="sync")
        outcome = self._outcome(resp)
        if outcome.code != 0:
            raise RuntimeError(f"PQC registration refused: {outcome.raw_log}")
        time.sleep(wait)
        return {"already_registered": False, "tx_hash": outcome.tx_hash, "proof_of_possession": attach}

    def send_owner(
        self,
        owner: UnifiedAccount,
        messages: list[Any],
        memo: str = "",
        gas: int | None = None,
        fee_uqor: int | None = None,
    ) -> TxOutcome:
        """Hybrid-signed native transaction from the eth-native owner account."""
        gas = gas or self.cfg.gas
        fee_uqor = fee_uqor or self.cfg.fee_uqor
        acct_no, seq = self.account_state(owner.cosmos)
        fee = {"amount": [{"denom": "uqor", "amount": str(fee_uqor)}], "gas": str(gas)}
        resp = hybrid_sign_and_broadcast(
            lambda version: sign_hybrid_eth(
                owner,
                self.cfg.chain_id,
                acct_no,
                messages,
                fee,
                seq,
                memo=memo,
                sign_bytes_version=version,
            ),
            chain_id=self.cfg.chain_id,
            rest_url=self.cfg.rest,
            mode="sync",
        )
        outcome = self._outcome(resp)
        if outcome.code == 0 and outcome.tx_hash:
            outcome = self._with_inclusion(outcome)
        return outcome

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
