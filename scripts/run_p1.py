#!/usr/bin/env python3
"""The second testnet run: sealed invoices, a QoreSign approval, a token payment.

    python scripts/run_p1.py all

Steps (each idempotent where it can be):

1. ``seal``   — every signed invoice is encrypted to the owner's ML-KEM-1024
                key; one hybrid transaction anchors the archive digest on chain.
2. ``link``   — the owner links a fresh agent key whose spending rule also
                allows the fUSD test token (the previous key was revoked).
3. ``approve``— an over-cap invoice is approved by the owner as a
                Sign-In-With-QoreChain message, verified against the owner's
                ML-DSA-87 key **as registered on chain**, then paid through the
                contract's `payWithApproval`.
4. ``token``  — a 500 fUSD invoice is paid by the agent on the native lane
                (`MsgExecuteCosmos`), bounded by the same spending rule.

Everything is recorded in ``demo/p1.json`` with the chain's answers.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import httpx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from eth_abi import decode as abi_decode  # noqa: E402
from qorsdk import (  # noqa: E402
    derive_native_account,
    derive_pqc_canonical,
    fetch_pqc_status,
    link_authenticator_msgs,
    link_proof_ed25519,
)
from qorsdk.address import bech32_to_hex  # noqa: E402
from qorsdk.messages.cosmos import MsgSend  # noqa: E402
import qoresign  # noqa: E402

from qoreguard import accounts as acct_mod  # noqa: E402
from qoreguard import approvals  # noqa: E402
from qoreguard import sealing  # noqa: E402
from qoreguard import treasury as T  # noqa: E402
from qoreguard.agent import Agent  # noqa: E402
from qoreguard.chain import Chain  # noqa: E402
from qoreguard.config import settings  # noqa: E402
from qoreguard.evm import Evm  # noqa: E402
from qoreguard.planner import Mandate, invoice_message  # noqa: E402

OUT = ROOT / "demo" / "p1.json"
INVOICES = ROOT / "demo" / "invoices.json"
MANDATE = ROOT / "demo" / "mandate.json"
SEALED_DIR = ROOT / "demo" / "sealed"
FUSD = "erc20:0x97143B771D34ddb9130b807b135Ae675A5a963CA"

#: The two invoices this run adds. INV-1012 is above the vendor's per-tx cap on
#: purpose (200000), so it needs the owner's approval; INV-1013 is a token invoice.
NEW_INVOICES = [
    {
        "id": "INV-1012",
        "vendor": "cloud-hosting",
        "amount_uqor": 250_000,
        "memo": "Quarter-end capacity top-up, above the automatic cap",
        "due": "2026-12-01",
    },
    {
        "id": "INV-1013",
        "vendor": "cloud-hosting",
        "amount_uqor": 500,
        "memo": "Stablecoin settlement: inference credits in fUSD",
        "due": "2026-12-02",
        "denom": FUSD,
    },
]


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    return json.loads(OUT.read_text()) if OUT.exists() else {}


def save_state(data: dict) -> None:
    OUT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build():
    cfg = settings()
    chain = Chain(cfg)
    evm = Evm(cfg.evm_rpc, cfg.evm_chain_id)
    dep = json.loads((ROOT / ".keys" / "deployment.json").read_text())
    agent = Agent(cfg, chain, evm, dep["contract"], ROOT / "demo" / "journal.jsonl")
    return cfg, chain, evm, agent, dep


def contract_view(evm: Evm, contract: str, selector: bytes, types=None, args=None) -> str:
    from eth_abi import encode as abi_encode

    data = selector + (abi_encode(types, args) if types else b"")
    return evm.eth_call(contract, data)


def balances(chain: Chain, address: str) -> dict[str, int]:
    return chain.balances(address)


# --------------------------------------------------------------------------- #
# invoices
# --------------------------------------------------------------------------- #
def ensure_invoices() -> list[dict]:
    """Add the run's invoices (signed by the vendor's own key) if missing."""
    doc = json.loads(INVOICES.read_text())
    have = {i["id"] for i in doc["invoices"]}
    seed = (ROOT / ".keys" / "vendor_cloud-hosting.ed25519").read_bytes()
    key = Ed25519PrivateKey.from_private_bytes(seed)
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    from qoreguard.planner import Invoice

    added = False
    for spec in NEW_INVOICES:
        if spec["id"] in have:
            continue
        row = dict(spec)
        sig = key.sign(invoice_message(Invoice(**row)))
        row["signer_pubkey"] = pub.hex()
        row["signature"] = sig.hex()
        doc["invoices"].append(row)
        added = True
    if added:
        INVOICES.write_text(json.dumps(doc, indent=2) + "\n")
    return doc["invoices"]


def ensure_mandate_vendor() -> None:
    """Give vendor 1 its own bech32 account for token settlement."""
    doc = json.loads(MANDATE.read_text())
    vendor = doc["vendors"][0]
    if vendor.get("token_account"):
        return
    acct = derive_native_account(os.environ["QORE_VENDOR_MNEMONIC"])
    vendor["token_account"] = acct.address
    vendor["note"] = (vendor.get("note", "") + " Token invoices are paid to `token_account`.").strip()
    MANDATE.write_text(json.dumps(doc, indent=2) + "\n")


# --------------------------------------------------------------------------- #
# 1. seal
# --------------------------------------------------------------------------- #
def cmd_seal() -> dict:
    cfg, chain, evm, agent, dep = build()
    owner = agent.owner
    keys = sealing.seal_keys(cfg.owner_mnemonic)

    invoices = ensure_invoices()
    sealed: list[dict] = []
    for inv in invoices:
        if not inv.get("signature"):
            continue
        plaintext = json.dumps(inv, sort_keys=True, separators=(",", ":")).encode()
        s = sealing.seal_invoice(keys, inv["id"], plaintext, SEALED_DIR, chain_id=cfg.chain_id)
        check = sealing.roundtrip_check(keys, s, chain_id=cfg.chain_id)
        rec = s.as_dict() | {"roundtrip": check}
        sealed.append(rec)
    (SEALED_DIR / "index.json").write_text(json.dumps(sealed, indent=2) + "\n")

    archive = "|".join(sorted(r["envelope_sha256"] for r in sealed))
    import hashlib

    archive_digest = hashlib.sha256(archive.encode()).hexdigest()
    memo = f"qg:seal-archive:v1:{archive_digest[:40]}"

    state = load_state()
    if state.get("seal", {}).get("tx"):
        print("archive already anchored:", state["seal"]["tx"])
        return state["seal"]

    # An anchor is a memo on a real transfer: one uqor to the owner's own
    # account. The value is irrelevant; the memo is the commitment.
    send = MsgSend(from_address=owner.cosmos, to_address=owner.cosmos, amount=[{"denom": "uqor", "amount": "1"}])
    outcome = chain.send_owner(
        owner, [{"type_url": "/cosmos.bank.v1beta1.MsgSend", "value": send}], memo=memo
    )
    time.sleep(3)
    body = chain.tx(outcome.tx_hash)
    on_chain_memo = (body.get("tx", {}).get("body", {}) or {}).get("memo", "")
    record = {
        "tx": outcome.tx_hash,
        "code": outcome.code,
        "memo": memo,
        "memo_read_back": on_chain_memo,
        "memo_matches": on_chain_memo == memo,
        "archive_digest": archive_digest,
        "invoices_sealed": len(sealed),
        "sealed": sealed,
        "checked_at": stamp(),
    }
    if outcome.code != 0:
        raise SystemExit(f"anchor refused: {outcome.raw_log}")
    state["seal"] = record
    save_state(state)
    print(json.dumps({k: record[k] for k in ("tx", "memo", "invoices_sealed", "memo_matches")}, indent=2))
    return record


# --------------------------------------------------------------------------- #
# 2. re-link the agent key (fUSD added to the rule)
# --------------------------------------------------------------------------- #
def cmd_link() -> dict:
    cfg, chain, evm, agent, dep = build()
    owner = agent.owner
    mandate = Mandate.load(str(MANDATE))
    proof = link_proof_ed25519(
        agent.key.private_bytes_raw(),
        chain_id=cfg.chain_id,
        account_address=owner.cosmos,
    )
    expiry = int(time.time()) + mandate.agent_link_hours * 3600
    rule = {
        "id": "qoreguard-agent-v2",
        "per_tx_limit": int(mandate.agent_per_tx_cap_uqor),
        "daily_limit": int(mandate.agent_daily_cap_uqor),
        "allowed_denoms": ["uqor", FUSD],
        "allowed_evm_calls": [
            f"{dep['contract']}/0x{T.SEL_PAY.hex()}",
            f"{dep['contract']}/0x{T.SEL_PAY_WITH_APPROVAL.hex()}",
        ],
        "enabled": True,
    }
    msgs = link_authenticator_msgs(
        owner=owner.cosmos,
        chain_id=cfg.chain_id,
        scheme=proof.scheme,
        pubkey=proof.pubkey,
        permissions=["send", "evm"],
        expiry_unix=expiry,
        possession_proof=proof.possession_proof,
        label="qoreguard-agent",
        spending_rule=rule,
    )
    outcome = chain.send_owner(owner, msgs, memo="qoreguard:link-agent-v2")
    time.sleep(4)
    nonce = chain.auth_nonce(owner.cosmos, agent.pubkey)
    record = {
        "tx": outcome.tx_hash,
        "code": outcome.code,
        "log": outcome.raw_log[:300],
        "agent_pubkey": agent.pubkey.hex(),
        "permissions": ["send", "evm"],
        "allowed_denoms": rule["allowed_denoms"],
        "per_tx_limit_uqor": rule["per_tx_limit"],
        "daily_limit_uqor": rule["daily_limit"],
        "nonce": nonce.nonce,
        "linked": nonce.linked,
        "checked_at": stamp(),
    }
    state = load_state()
    state["link"] = record
    save_state(state)
    print(json.dumps({k: record[k] for k in ("tx", "code", "linked", "nonce")}, indent=2))
    if outcome.code != 0:
        raise SystemExit("link refused: " + outcome.raw_log)
    return record


# --------------------------------------------------------------------------- #
# 3. QoreSign approval for an over-cap invoice
# --------------------------------------------------------------------------- #
def cmd_approve() -> dict:
    cfg, chain, evm, agent, dep = build()
    owner = agent.owner
    mandate = Mandate.load(str(MANDATE))
    invoice = next(i for i in mandate.invoices if i.id == "INV-1012")
    vendor = mandate.vendor(invoice.vendor)
    amount = invoice.amount_uqor

    # The sealed copy of this invoice (from step 1) is what the payment commits to.
    index = json.loads((SEALED_DIR / "index.json").read_text())
    sealed_rec = next(r for r in index if r["invoice_id"] == invoice.id)
    sealed = sealing.load_sealed(sealed_rec)

    approval_nonce = int(contract_view(evm, dep["contract"], T.SEL_APPROVAL_NONCE), 16)
    request = approvals.build_request(
        account=owner.cosmos,
        chain_id=cfg.chain_id,
        invoice_id=invoice.id,
        vendor_id=vendor.id,
        vendor_label=vendor.label,
        amount=amount,
        denom="uqor",
        approval_nonce=approval_nonce,
    )
    signature_json = approvals.sign(request, cfg.owner_mnemonic, owner.cosmos)

    chain_pub = fetch_pqc_status(cfg.rest, owner.cosmos)["pubkey"]
    import base64

    chain_pub = base64.b64decode(chain_pub) if isinstance(chain_pub, str) else chain_pub
    verified = approvals.verify(
        request, signature_json, chain_pq_public_key=chain_pub, chain_id=cfg.chain_id
    )

    # Negative checks, run on the same approval: each must be refused.
    negatives = {}
    tampered = approvals.Request(**{**request.as_dict(), "amount": amount * 2})
    for label, req, sig in (
        ("amount changed after approval", tampered, signature_json),
        ("replayed under another nonce", approvals.Request(**{**request.as_dict(), "nonce": "apprdeadbeef"}), signature_json),
    ):
        try:
            approvals.verify(req, sig, chain_pq_public_key=chain_pub, chain_id=cfg.chain_id)
            negatives[label] = "ACCEPTED (bad)"
        except Exception as exc:  # qoresign.QoreSignError
            negatives[label] = f"refused: {getattr(exc, 'code', type(exc).__name__)}"

    memo_prefix = f"{invoice.id} {invoice.memo}"
    intent = agent.build_intent(vendor.id, vendor.label, amount, memo_prefix)
    payload = T.payload_exception(vendor.id, amount, intent.onchain_memo)
    onchain_sig = agent.owner_sign(T.PAY_WITH_APPROVAL, payload, approval_nonce)
    decision = agent.pay(vendor.id, vendor.label, amount, memo_prefix, approval=onchain_sig, intent=intent)

    record = {
        "invoice": invoice.id,
        "vendor_id": vendor.id,
        "vendor": vendor.label,
        "amount_uqor": amount,
        "vendor_per_tx_cap_uqor": vendor.per_tx_cap_uqor,
        "approval_nonce": approval_nonce,
        "siwq_message": request.message,
        "siwq_signature": signature_json,
        "siwq_verified_against_chain_key": verified,
        "chain_key_len": len(chain_pub),
        "negatives": negatives,
        "sealed_envelope_sha256": sealed.envelope_sha256,
        "sealed_memo": sealed.memo,
        "intent_memo": intent.onchain_memo,
        "tx": decision.tx_hash,
        "paid": decision.chain_success,
        "chain_reason": decision.chain_reason,
        "checked_at": stamp(),
    }
    state = load_state()
    state["approval"] = record
    save_state(state)
    print(json.dumps({k: record[k] for k in ("tx", "paid", "chain_reason", "negatives")}, indent=2))
    return record


# --------------------------------------------------------------------------- #
# 4. token payment
# --------------------------------------------------------------------------- #
def cmd_token() -> dict:
    cfg, chain, evm, agent, dep = build()
    mandate = Mandate.load(str(MANDATE))
    invoice = next(i for i in mandate.invoices if i.id == "INV-1013")
    vendor = mandate.vendor(invoice.vendor)
    owner = agent.owner
    index = json.loads((SEALED_DIR / "index.json").read_text())
    sealed_rec = next(r for r in index if r["invoice_id"] == invoice.id)
    sealed = sealing.load_sealed(sealed_rec)

    before_owner = balances(chain, owner.cosmos)
    before_vendor = balances(chain, vendor.token_account)
    try:
        decision = agent.pay_token(
            vendor.id,
            vendor.label,
            invoice.amount_uqor,
            invoice.denom,
            vendor.token_account,
            f"{invoice.id} {invoice.memo}",
        )
    except Exception as exc:
        # A refused execution still leaves a trace in the journal with tx hash.
        record = {"error": str(exc), "checked_at": stamp()}
        state = load_state()
        state["token"] = record
        save_state(state)
        raise
    time.sleep(4)
    after_owner = balances(chain, owner.cosmos)
    after_vendor = balances(chain, vendor.token_account)
    record = {
        "invoice": invoice.id,
        "vendor_id": vendor.id,
        "vendor": vendor.label,
        "amount": invoice.amount_uqor,
        "denom": invoice.denom,
        "to": vendor.token_account,
        "sealed_envelope_sha256": sealed.envelope_sha256,
        "tx": decision.tx_hash,
        "paid": decision.chain_success,
        "chain_reason": decision.chain_reason,
        "owner_fusd_before": before_owner.get(invoice.denom, 0),
        "owner_fusd_after": after_owner.get(invoice.denom, 0),
        "vendor_fusd_after": after_vendor.get(invoice.denom, 0),
        "checked_at": stamp(),
    }
    state = load_state()
    state["token"] = record
    save_state(state)
    print(json.dumps({k: record[k] for k in ("tx", "paid", "chain_reason", "owner_fusd_before", "owner_fusd_after", "vendor_fusd_after")}, indent=2))
    return record


if __name__ == "__main__":
    ensure_mandate_vendor()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    order = ["seal", "link", "approve", "token"] if cmd == "all" else [cmd]
    for step in order:
        print(f"\n== {step}")
        {"seal": cmd_seal, "link": cmd_link, "approve": cmd_approve, "token": cmd_token}[step]()
