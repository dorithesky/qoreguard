"""The agent: a hot ed25519 key with no funds and no ability to surprise anyone.

For every invoice the agent
  1. decides (planner.py) whether the mandate allows it,
  2. writes a signed intent (ed25519) that pins vendor, amount, memo and the
     authenticator nonce it is about to spend,
  3. signs the chain's `evmAuthSignBytesV2` digest with the same key,
  4. hands a `MsgExecuteEVM` to the relayer, which pays the fee, and
  5. records the chain's answer — including refusals, which are the point.

Nothing here enforces a limit. Every limit is enforced by the chain
(`x/abstractaccount` spending rule + allowed EVM calls) and by the treasury
contract. That is the whole design: a bug or a stolen agent key cannot lift
them.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from qorsdk import derive_native_account, derive_pqc_canonical, pqc_sign
from qorsdk.authenticator import execute_evm_msg

from . import treasury as T
from .chain import Chain
from .config import Settings
from .evm import Evm, uqor_to_wei
from .treasury import WEI_PER_UQOR


def load_agent_key(path: Path) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


def agent_pubkey(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


@dataclass
class Intent:
    vendor_id: int
    vendor_label: str
    amount_uqor: int
    memo: str
    auth_nonce: int
    issued_at: str
    agent_pubkey: str

    def canonical_bytes(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()

    @property
    def digest(self) -> str:
        return __import__("hashlib").sha256(self.canonical_bytes()).hexdigest()

    @property
    def onchain_memo(self) -> str:
        """What the chain records: a commitment to this exact signed intent."""
        return f"qg:v1:{self.digest[:40]}"


@dataclass
class Decision:
    invoice_id: str
    vendor_id: int
    vendor_label: str
    amount_uqor: int
    memo: str
    action: str  # "pay" | "pay_with_approval" | "skip"
    reason: str
    intent: Intent | None = None
    intent_signature: str | None = None
    tx_hash: str | None = None
    chain_success: bool | None = None
    chain_reason: str | None = None
    journal: dict = field(default_factory=dict)


class Journal:
    """Append-only JSONL evidence file: every decision, hash and outcome."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        record = {"logged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
        with self.path.open("a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]


@dataclass
class AgentConfig:
    contract: str
    evm_gas_limit: int = 250_000


class Agent:
    def __init__(self, cfg: Settings, chain: Chain, evm: Evm, contract: str, journal: Path) -> None:
        self.cfg = cfg
        self.chain = chain
        self.evm = evm
        self.contract = contract
        self.journal = Journal(journal)
        self.key = load_agent_key(cfg.agent_key_path)
        self.pubkey = agent_pubkey(self.key)
        self.owner = derive_native_account(cfg.owner_mnemonic)
        self.relayer = derive_native_account(cfg.relayer_mnemonic)
        self.owner_pqc = derive_pqc_canonical(self.owner.address, cfg.owner_mnemonic)
        self.relayer_pqc = derive_pqc_canonical(self.relayer.address, cfg.relayer_mnemonic)

    # ------------------------------------------------------------------ #
    # Intent + signature
    # ------------------------------------------------------------------ #
    def _sign_intent(self, intent: Intent) -> bytes:
        return self.key.sign(intent.canonical_bytes())

    def _auth_digest(self, to: str, value_wei: int, data: bytes, gas_limit: int, nonce: int) -> bytes:
        return self.chain.evm_auth_digest(
            owner=self.owner.address,
            agent_pubkey=self.pubkey,
            to=to,
            value_wei=value_wei,
            data=data,
            gas_limit=gas_limit,
            nonce=nonce,
        )

    # ------------------------------------------------------------------ #
    # One payment
    # ------------------------------------------------------------------ #
    def build_intent(self, vendor_id: int, vendor_label: str, amount_uqor: int, memo: str) -> Intent:
        """The exact intent for the next payment (nonce read from the chain now)."""
        nonce = self.chain.auth_nonce(self.owner.address, self.pubkey).nonce
        return Intent(
            vendor_id=vendor_id,
            vendor_label=vendor_label,
            amount_uqor=amount_uqor,
            memo=memo,
            auth_nonce=nonce,
            issued_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            agent_pubkey=self.pubkey.hex(),
        )

    def pay(
        self,
        vendor_id: int,
        vendor_label: str,
        amount_uqor: int,
        memo: str,
        *,
        approval: T.OwnerSignature | None = None,
        intent: Intent | None = None,
    ) -> Decision:
        nonce = self.chain.auth_nonce(self.owner.address, self.pubkey).nonce
        if intent is None:
            intent = self.build_intent(vendor_id, vendor_label, amount_uqor, memo)
        elif intent.auth_nonce != nonce:
            raise RuntimeError(
                f"intent was built for auth nonce {intent.auth_nonce} but the chain expects {nonce}"
            )
        intent_sig = self._sign_intent(intent)

        if approval is None:
            data = T.calldata_pay(vendor_id, amount_uqor, intent.onchain_memo)
            action = "pay"
        else:
            data = T.calldata_pay_with_approval(
                vendor_id,
                amount_uqor,
                intent.onchain_memo,
                approval.nonce,
                approval.pubkey,
                approval.signature,
            )
            action = "pay_with_approval"

        value_wei = uqor_to_wei(amount_uqor)
        digest = self._auth_digest(
            to=self.contract,
            value_wei=value_wei,
            data=data,
            gas_limit=self.cfg.evm_gas,
            nonce=nonce,
        )
        auth_sig = self.key.sign(digest)

        msg = execute_evm_msg(
            relayer=self.relayer.address,
            account=self.owner.address,
            scheme="ed25519",
            pubkey=self.pubkey,
            signature=auth_sig,
            to=self.contract,
            value=str(value_wei),
            data=data,
            gas_limit=self.cfg.evm_gas,
            nonce=nonce,
        )
        outcome = self.chain.send_hybrid(
            self.relayer, self.relayer_pqc, [msg], memo=f"qoreguard:{intent.digest[:16]}"
        )

        decision = Decision(
            invoice_id=memo.split(" ")[0] if memo else "",
            vendor_id=vendor_id,
            vendor_label=vendor_label,
            amount_uqor=amount_uqor,
            memo=memo,
            action=action,
            reason="within mandate",
            intent=intent,
            intent_signature=intent_sig.hex(),
            tx_hash=outcome.tx_hash,
        )

        if outcome.code != 0:
            decision.chain_success = False
            decision.chain_reason = f"tx rejected: {outcome.raw_log}"
        elif outcome.executions:
            ex = outcome.executions[0]
            decision.chain_success = bool(ex.success)
            if not ex.success:
                decision.chain_reason = ex.reason or ex.vm_error or "execution failed"
        else:
            decision.chain_success = None
            decision.chain_reason = "no execution result in the response"

        self.journal.append(
            {
                "kind": "payment",
                "action": action,
                "invoice": decision.invoice_id,
                "vendor_id": vendor_id,
                "vendor_label": vendor_label,
                "amount_uqor": amount_uqor,
                "memo": memo,
                "onchain_memo": intent.onchain_memo,
                "intent": asdict(intent),
                "intent_signature": intent_sig.hex(),
                "auth_nonce": nonce,
                "auth_signature": auth_sig.hex(),
                "auth_digest": digest.hex(),
                "tx_hash": decision.tx_hash,
                "chain_success": decision.chain_success,
                "chain_reason": decision.chain_reason,
            }
        )
        return decision

    # ------------------------------------------------------------------ #
    # Owner-side actions (used by the CLI, not by the agent loop)
    # ------------------------------------------------------------------ #
    def owner_calls_contract(self, data: bytes, *, gas: int = 400_000) -> str:
        """Owner submits one EVM call from their own account, inside a window."""
        nonce = self.chain.account_state(self.owner.address)[1]
        return self.evm.send(
            self.owner.private_key,
            to=self.contract,
            data=data,
            gas=gas,
            nonce=nonce,
        )

    def owner_sign(self, action: bytes, payload_hash: bytes, nonce: int) -> T.OwnerSignature:
        return T.owner_sign(
            self.owner_pqc,
            self.cfg.evm_chain_id,
            self.contract,
            action,
            payload_hash,
            nonce,
            pqc_sign,
        )


def verify_intent(intent_bytes: bytes, signature: bytes, pubkey: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, intent_bytes)
        return True
    except InvalidSignature:
        return False


def memo_commitment(intent_bytes: bytes, prefix_len: int = 40) -> str:
    """The memo string the chain records for an intent (a commitment to it)."""
    import hashlib

    return "qg:v1:" + hashlib.sha256(intent_bytes).hexdigest()[:prefix_len]


__all__ = [
    "Agent",
    "Decision",
    "Intent",
    "Journal",
    "WEI_PER_UQOR",
    "memo_commitment",
    "verify_intent",
]
