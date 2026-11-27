#!/usr/bin/env python3
"""The invoice guard, proved against the testnet.

The first deployment (v1) settled the demo run. This script deploys the current
contract — the one that records `paidInvoices` — and drives every duplicate case
through it on `qorechain-diana`:

    python scripts/run_guard.py all          # invoices, deploy, setup, link, pay, token

Steps, each idempotent where it can be:

1. ``invoices`` — sign the three guard invoices with the vendor's own key
                  (INV-1014 0.15 QOR, INV-1015 0.50 QOR above the vendor cap,
                  INV-1016 5 fUSD on the token lane).
2. ``deploy``   — open a scoped EVM window and deploy the guarded contract.
3. ``setup``    — PQ-signed addVendor x2 and setTreasuryCap on it.
4. ``link``     — re-link the agent key to the new contract's two calls.
5. ``pay``      — the duplicate matrix: the same invoice twice on the agent
                  lane, twice with approval, and once each way across lanes.
                  The first payment of INV-1014 is made by a *separate* process
                  (`python -m qoreguard.cli run`), and a second process is shown
                  skipping it; the duplicate attempts come from this process,
                  which never reads that journal.
6. ``token``    — INV-1016 through the same `cli run` path, so the general run
                  pays a token invoice in its own currency (5 fUSD, not QOR).

Everything is recorded in ``demo/guard.json`` with the chain's answers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from eth_abi import encode as abi_encode  # noqa: E402
from eth_abi import decode as abi_decode  # noqa: E402
from qorsdk import fetch_pqc_status  # noqa: E402
from qorsdk.messages import msg as compose  # noqa: E402

from qoreguard import accounts as acct_mod  # noqa: E402
from qoreguard import approvals  # noqa: E402
from qoreguard import treasury as T  # noqa: E402
from qoreguard.agent import Agent  # noqa: E402
from qoreguard.chain import Chain  # noqa: E402
from qoreguard.config import ROOT as REPO, settings  # noqa: E402
from qoreguard.evm import Evm, uqor_to_wei  # noqa: E402
from qoreguard.planner import Invoice, Mandate, invoice_message  # noqa: E402

STATE = ROOT / "demo" / "guard.json"
INVOICES = ROOT / "demo" / "invoices.json"
MANDATE = ROOT / "demo" / "mandate.json"
ADDRESSES = ROOT / "demo" / "addresses.json"
DEPLOYMENT = ROOT / ".keys" / "deployment.json"
ARTIFACT = ROOT / "contracts" / "out" / "QoreGuardTreasury.sol" / "QoreGuardTreasury.json"
FUSD = "erc20:0x97143B771D34ddb9130b807b135Ae675A5a963CA"

GUARD_INVOICES = [
    {
        "id": "INV-1014",
        "vendor": "cloud-hosting",
        "amount_uqor": 150_000,
        "memo": "Edge cache, one month, settled once",
        "due": "2026-12-03",
    },
    {
        "id": "INV-1015",
        "vendor": "cloud-hosting",
        "amount_uqor": 500_000,
        "memo": "Incident replay capacity, above the automatic cap",
        "due": "2026-12-03",
    },
    {
        "id": "INV-1016",
        "vendor": "cloud-hosting",
        "amount_uqor": 5,
        "memo": "Inference credits, five fUSD, paid by the general run",
        "due": "2026-12-03",
        "denom": FUSD,
    },
]


# --------------------------------------------------------------------------- #
# state + chain helpers
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build():
    cfg = settings()
    chain = Chain(cfg)
    evm = Evm(cfg.evm_rpc, cfg.evm_chain_id)
    dep = json.loads(DEPLOYMENT.read_text()) if DEPLOYMENT.exists() else {}
    contract = dep.get("contract", "0x" + "0" * 40)
    agent = Agent(cfg, chain, evm, contract, ROOT / "demo" / "journal.jsonl")
    return cfg, chain, evm, agent, dep


def view(evm: Evm, contract: str, selector: bytes, types=None, values=None) -> int:
    out = evm.eth_call(contract, selector + (abi_encode(types, values) if types else b""))
    return int(out, 16) if out and out != "0x" else 0


def open_window(chain: Chain, agent: Agent, *, blocks: int, txs: int, value: int, targets: list[str]) -> dict:
    owner = agent.owner
    msg = compose.pqc.open_evm_window(
        sender=owner.cosmos,
        blocks=blocks,
        max_txs=txs,
        max_value=value,
        native_only=False,
        allowed_targets=targets,
    )
    outcome = chain.send_owner(owner, [msg], memo="qoreguard:guard-window")
    if outcome.code != 0:
        raise SystemExit(f"window refused: {outcome.raw_log}")
    w = chain.evm_window(owner.cosmos)
    return {
        "tx": outcome.tx_hash,
        "blocks": blocks,
        "max_txs": txs,
        "max_value_uqor": value,
        "targets": targets,
        "expiry_height": w.expiry_height,
    }


# --------------------------------------------------------------------------- #
# 1. invoices
# --------------------------------------------------------------------------- #
def cmd_invoices() -> dict:
    doc = json.loads(INVOICES.read_text())
    have = {i["id"] for i in doc["invoices"]}
    seed = (ROOT / ".keys" / "vendor_cloud-hosting.ed25519").read_bytes()
    key = Ed25519PrivateKey.from_private_bytes(seed)
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    added = 0
    for spec in GUARD_INVOICES:
        if spec["id"] in have:
            continue
        row = dict(spec)
        row["signature"] = key.sign(invoice_message(Invoice(**row))).hex()
        row["signer_pubkey"] = pub.hex()
        doc["invoices"].append(row)
        added += 1
    if added:
        INVOICES.write_text(json.dumps(doc, indent=2) + "\n")

    mandate = Mandate.load(str(MANDATE))
    for spec in GUARD_INVOICES:
        inv = next(i for i in mandate.invoices if i.id == spec["id"])
        vendor = mandate.vendor(inv.vendor)
        assert vendor is not None
        from qoreguard.planner import verify_invoice

        if not verify_invoice(inv, vendor):
            raise SystemExit(f"{inv.id}: the vendor's signature does not verify")
    print(f"signed {added} guard invoice(s); {len(GUARD_INVOICES)} verify against the vendor key")
    return {"added": added, "invoices": [s["id"] for s in GUARD_INVOICES]}


# --------------------------------------------------------------------------- #
# 2. deploy
# --------------------------------------------------------------------------- #
def cmd_deploy() -> dict:
    state = load_state()
    if state.get("deploy", {}).get("contract"):
        print("already deployed:", state["deploy"]["contract"])
        return state["deploy"]

    cfg, chain, evm, agent, dep = build()
    owner = agent.owner
    old = dep.get("contract")

    window = open_window(chain, agent, blocks=400, txs=1, value=2_000_000, targets=[])
    nonce = chain.account_state(owner.cosmos)[1]
    tx = evm.deploy(
        owner.private_key,
        str(ARTIFACT),
        ["address", "uint96"],
        [owner.evm, 1_500_000],
        gas=6_000_000,
        gas_price=cfg.evm_gas_price_wei,
        nonce=nonce,
    )
    receipt = evm.wait(tx)
    if not receipt.ok or not receipt.contract_address:
        raise SystemExit(f"deploy failed (tx {tx})")
    contract = receipt.contract_address

    addresses = json.loads(ADDRESSES.read_text())
    previous = addresses.setdefault("previous_deployments", [])
    if old and all(p["contract"].lower() != old.lower() for p in previous):
        previous.append(
            {
                "contract": old,
                "deploy_tx": dep.get("tx"),
                "block": dep.get("block"),
                "label": "first run",
                "note": "settled the first demo run; replaced by the guarded deployment",
            }
        )
    addresses["contract"] = contract
    addresses["deploy_tx"] = tx
    addresses["deploy_block"] = receipt.block_number
    addresses["policy_checked_at"] = stamp()
    ADDRESSES.write_text(json.dumps(addresses, indent=2) + "\n")

    DEPLOYMENT.write_text(json.dumps({"contract": contract, "tx": tx, "block": receipt.block_number,
                                      "previous": old, "previous_tx": dep.get("tx")}, indent=2))
    record = {
        "contract": contract,
        "tx": tx,
        "block": receipt.block_number,
        "gas_used": receipt.gas_used,
        "window": window,
        "previous_contract": old,
        "checked_at": stamp(),
    }
    state["deploy"] = record
    save_state(state)
    print(json.dumps({k: record[k] for k in ("contract", "tx", "block", "gas_used")}, indent=2))
    return record


# --------------------------------------------------------------------------- #
# 3. setup
# --------------------------------------------------------------------------- #
def cmd_setup() -> dict:
    state = load_state()
    if state.get("setup", {}).get("done"):
        print("already set up")
        return state["setup"]

    cfg, chain, evm, agent, dep = build()
    contract = dep["contract"]
    mandate = Mandate.load(str(MANDATE))

    existing = set()
    for i in range(view(evm, contract, T.SEL_VENDOR_COUNT)):
        vid = view(evm, contract, T.SEL_VENDOR_ID_AT, ["uint256"], [i])
        payee, *_ = abi_decode(["address", "bytes32", "uint96", "uint96", "bool"],
                               bytes.fromhex(evm.eth_call(contract, T.SEL_VENDOR + abi_encode(["uint256"], [vid]))[2:]))
        if int(payee, 16) != 0:
            existing.add(vid)
    needed = [v for v in mandate.vendors if v.id not in existing]
    cap_set = view(evm, contract, T.SEL_TREASURY_DAILY_CAP) != 0
    if not needed and cap_set:
        print("vendors and cap already on chain")
        record = {"done": True, "vendors": [], "treasury_cap": None, "vendor_count": view(evm, contract, T.SEL_VENDOR_COUNT)}
        state["setup"] = record
        save_state(state)
        return record

    # Fees count against the window's max_value: 600k gas at 120 gwei is
    # 72,000 uqor per admin call, so the budget is sized in qor, not in cap units.
    window = open_window(chain, agent, blocks=400, txs=max(1, len(needed) + (0 if cap_set else 1)),
                         value=500_000, targets=[contract])

    nonce = view(evm, contract, T.SEL_APPROVAL_NONCE)
    records = []
    for vendor in needed:
        payload = T.payload_add_vendor(
            vendor.id, vendor.payee, vendor.label, vendor.per_tx_cap_uqor, vendor.daily_cap_uqor
        )
        sig = agent.owner_sign(T.ADD_VENDOR, payload, nonce)
        data = T.calldata_add_vendor(
            vendor.id, vendor.payee, vendor.label, vendor.per_tx_cap_uqor, vendor.daily_cap_uqor,
            nonce, sig.pubkey, sig.signature,
        )
        tx = agent.owner_calls_contract(data, gas=600_000)
        receipt = evm.wait(tx)
        records.append({"vendor_id": vendor.id, "label": vendor.label, "tx": tx,
                        "status": "ok" if receipt.ok else "REVERTED", "nonce": nonce})
        if not receipt.ok:
            raise SystemExit(f"addVendor {vendor.id} reverted")
        nonce += 1
        time.sleep(1)

    cap_record = None
    if not cap_set:
        payload = T.payload_set_treasury_cap(mandate.treasury_daily_cap_uqor)
        sig = agent.owner_sign(T.SET_TREASURY_CAP, payload, nonce)
        data = T.calldata_set_treasury_cap(mandate.treasury_daily_cap_uqor, nonce, sig.pubkey, sig.signature)
        tx = agent.owner_calls_contract(data, gas=600_000)
        receipt = evm.wait(tx)
        if not receipt.ok:
            raise SystemExit("setTreasuryCap reverted")
        cap_record = {"tx": tx, "uqor": mandate.treasury_daily_cap_uqor, "nonce": nonce}

    record = {
        "done": True,
        "window": window,
        "vendors": records,
        "treasury_cap": cap_record,
        "approval_nonce": view(evm, contract, T.SEL_APPROVAL_NONCE),
        "vendor_count": view(evm, contract, T.SEL_VENDOR_COUNT),
        "checked_at": stamp(),
    }
    state["setup"] = record
    save_state(state)
    print(json.dumps({"vendors": records, "treasury_cap": cap_record}, indent=2))
    return record


# --------------------------------------------------------------------------- #
# 4. link the agent to the new contract
# --------------------------------------------------------------------------- #
def cmd_link() -> dict:
    state = load_state()
    if state.get("link", {}).get("tx"):
        print("already linked:", state["link"]["tx"])
        return state["link"]

    cfg, chain, evm, agent, dep = build()
    contract = dep["contract"]
    owner = agent.owner
    mandate = Mandate.load(str(MANDATE))

    from qorsdk import link_authenticator_msgs, link_proof_ed25519

    proof = link_proof_ed25519(
        agent.key.private_bytes_raw(), chain_id=cfg.chain_id, account_address=owner.cosmos
    )
    expiry = int(time.time()) + mandate.agent_link_hours * 3600
    rule = {
        "id": "qoreguard-agent-v3",
        "per_tx_limit": int(mandate.agent_per_tx_cap_uqor),
        "daily_limit": int(mandate.agent_daily_cap_uqor),
        "allowed_denoms": ["uqor", FUSD],
        "allowed_evm_calls": [
            f"{contract}/0x{T.SEL_PAY.hex()}",
            f"{contract}/0x{T.SEL_PAY_WITH_APPROVAL.hex()}",
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
    outcome = chain.send_owner(owner, msgs, memo="qoreguard:link-agent-v3")
    if outcome.code != 0:
        raise SystemExit("link refused: " + outcome.raw_log)
    time.sleep(4)
    nonce = chain.auth_nonce(owner.cosmos, agent.pubkey)
    record = {
        "tx": outcome.tx_hash,
        "rule": rule,
        "expiry": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry)),
        "auth_nonce": nonce.nonce,
        "linked": nonce.linked,
        "checked_at": stamp(),
    }
    state["link"] = record
    save_state(state)
    print(json.dumps({k: record[k] for k in ("tx", "linked", "auth_nonce")}, indent=2))
    return record


# --------------------------------------------------------------------------- #
# 4b. mirror the owner's recorded caps on the new contract
# --------------------------------------------------------------------------- #
def cmd_caps() -> dict:
    """Vendor 2's daily cap was lowered to 0.4 QOR by a PQ-signed action on v1.

    The new deployment starts from the mandate's 0.6; this step applies the same
    owner decision to it, so the policy the dashboard and verify.py compare is
    the policy both contracts hold.
    """
    state = load_state()
    if state.get("caps", {}).get("tx"):
        print("caps already applied")
        return state["caps"]

    cfg, chain, evm, agent, dep = build()
    contract = dep["contract"]
    addresses = json.loads(ADDRESSES.read_text())
    spec = next(v for v in addresses["vendors"] if v["id"] == 2)
    raw = evm.eth_call(contract, T.SEL_VENDOR + abi_encode(["uint256"], [2]))
    _payee, _label, per_tx, daily, _active = abi_decode(
        ["address", "bytes32", "uint96", "uint96", "bool"], bytes.fromhex(raw[2:])
    )
    if daily == spec["daily_cap_uqor"] and per_tx == spec["per_tx_cap_uqor"]:
        print(f"caps already match: vendor 2 {per_tx}/{daily}")
        record = {"tx": None, "per_tx": per_tx, "daily": daily, "already": True}
        state["caps"] = record
        save_state(state)
        return record

    window = open_window(chain, agent, blocks=400, txs=1, value=100_000, targets=[contract])
    nonce = view(evm, contract, T.SEL_APPROVAL_NONCE)
    payload = T.payload_set_vendor_caps(2, spec["per_tx_cap_uqor"], spec["daily_cap_uqor"])
    sig = agent.owner_sign(T.SET_VENDOR_CAPS, payload, nonce)
    data = T.calldata_set_vendor_caps(2, spec["per_tx_cap_uqor"], spec["daily_cap_uqor"], nonce,
                                      sig.pubkey, sig.signature)
    tx = agent.owner_calls_contract(data, gas=400_000)
    receipt = evm.wait(tx)
    if not receipt.ok:
        raise SystemExit("setVendorCaps reverted")
    record = {"tx": tx, "per_tx": spec["per_tx_cap_uqor"], "daily": spec["daily_cap_uqor"],
              "window": window, "checked_at": stamp()}
    state["caps"] = record
    save_state(state)
    print(json.dumps(record, indent=2))
    return record


# --------------------------------------------------------------------------- #
# 5. the duplicate matrix
# --------------------------------------------------------------------------- #
def run_cli(*args: str) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "agent") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-m", "qoreguard.cli", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    out = (proc.stdout + proc.stderr).strip()
    print(out)
    return out


def cmd_pay() -> dict:
    """Drive the duplicate matrix, resuming from whatever is already on chain.

    The journal is the source of truth: a settled payment of an invoice on this
    contract is never repeated, and a refusal the journal already holds is not
    re-sent. Running the step twice therefore finishes the matrix instead of
    paying twice — the property the contract itself enforces.
    """
    state = load_state()
    cfg, chain, evm, agent, dep = build()
    contract = dep["contract"]
    mandate = Mandate.load(str(MANDATE))
    vendor = mandate.vendor("cloud-hosting")
    inv14 = next(i for i in mandate.invoices if i.id == "INV-1014")
    inv15 = next(i for i in mandate.invoices if i.id == "INV-1015")
    key14, key15 = T.invoice_key(inv14.id, vendor.id), T.invoice_key(inv15.id, vendor.id)

    def entries() -> list[dict]:
        path = ROOT / "demo" / "journal.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def settled(invoice_id: str) -> list[dict]:
        return [e for e in entries() if e.get("kind") == "payment" and e.get("chain_success")
                and (e.get("contract") or "").lower() == contract.lower() and e.get("invoice") == invoice_id]

    def refused(invoice_id: str, action: str) -> list[dict]:
        return [e for e in entries() if e.get("kind") == "payment" and e.get("chain_success") is False
                and (e.get("contract") or "").lower() == contract.lower()
                and e.get("invoice") == invoice_id and e.get("action") == action]

    def open_funds() -> None:
        # A relayed EVM call costs 225,000 uqor of relayer fee; keep it funded.
        relayer = chain.qor(agent.relayer.address)
        if relayer < 1.0:
            from qorsdk.messages.cosmos import MsgSend

            owner = agent.owner
            send = MsgSend(from_address=owner.cosmos, to_address=agent.relayer.address,
                           amount=[{"denom": "uqor", "amount": "3000000"}])
            out = chain.send_owner(owner, [{"type_url": "/cosmos.bank.v1beta1.MsgSend", "value": send}],
                                   memo="qoreguard:fund-relayer")
            if out.code != 0:
                raise SystemExit("funding the relayer failed: " + out.raw_log)
            time.sleep(3)

    def pay_call(inv, approval: bool):
        nonce = view(evm, contract, T.SEL_APPROVAL_NONCE)
        key = T.invoice_key(inv.id, vendor.id)
        memo = f"{inv.id} {inv.memo}"
        kw = {"invoice_key": key}
        if approval:
            sig = agent.owner_sign(T.PAY_WITH_APPROVAL, T.payload_exception(vendor.id, inv.amount_uqor, key), nonce)
            kw.update(approval=sig, intent=agent.build_intent(vendor.id, vendor.label, inv.amount_uqor, memo))
        return agent.pay(vendor.id, vendor.label, inv.amount_uqor, memo, **kw)

    payments: list[dict] = []

    def row_from_entry(label: str, e: dict, expect_paid: bool) -> None:
        row = {
            "label": label, "invoice": e["invoice"], "invoice_key": e.get("invoice_key"),
            "amount_uqor": e.get("amount_uqor"), "action": e.get("action"),
            "tx": e.get("tx_hash"), "paid": bool(e.get("chain_success")),
            "chain_reason": e.get("chain_reason"),
            "expected": "PAID" if expect_paid else "REFUSED — InvoiceAlreadyPaid",
            "checked_at": stamp(),
        }
        payments.append(row)
        print(f"   {label:<28} {'PAID' if row['paid'] else 'REFUSED'} {row['chain_reason'] or ''}")
        if expect_paid and not row["paid"]:
            raise SystemExit(f"{label}: expected a payment, got {row['chain_reason']}")
        if not expect_paid:
            if row["paid"]:
                raise SystemExit(f"{label}: the contract accepted a duplicate")
            if "InvoiceAlreadyPaid" not in (row["chain_reason"] or ""):
                raise SystemExit(f"{label}: refused for another reason: {row['chain_reason']}")

    window = open_window(chain, agent, blocks=600, txs=8, value=3_000_000, targets=[contract])

    # 1. the general run pays INV-1014 once (separate process, its own journal)
    first_out = "(already settled: %s)" % settled(inv14.id)[0]["tx_hash"] if settled(inv14.id) else ""
    if not settled(inv14.id):
        open_funds()
        first_out = run_cli("run", "--invoice", "INV-1014")
        if not settled(inv14.id):
            raise SystemExit("the general run did not settle INV-1014")
    row_from_entry("agent lane, first attempt", settled(inv14.id)[0], True)

    # 2. the app restarts: the same command, a second process, must skip it
    restart_out = run_cli("run", "--invoice", "INV-1014")
    skipped_on_restart = "already paid" in restart_out
    print(f"   restart skipped the settled invoice: {skipped_on_restart}")
    if not skipped_on_restart:
        raise SystemExit("the restart did not skip the settled invoice")

    # 3. a second agent with no journal asks the contract for the same invoice
    if refused(inv14.id, "pay"):
        row_from_entry("agent lane, second attempt", refused(inv14.id, "pay")[0], False)
    else:
        open_funds()
        decision = pay_call(inv14, approval=False)
        row = {"label": "agent lane, second attempt", "invoice": inv14.id, "invoice_key": key14,
               "amount_uqor": inv14.amount_uqor, "action": "pay", "tx": decision.tx_hash,
               "paid": bool(decision.chain_success), "chain_reason": decision.chain_reason,
               "expected": "REFUSED — InvoiceAlreadyPaid", "checked_at": stamp()}
        payments.append(row)
        print(f"   {row['label']:<28} {'PAID' if row['paid'] else 'REFUSED'} {row['chain_reason'] or ''}")
        if row["paid"] or "InvoiceAlreadyPaid" not in (row["chain_reason"] or ""):
            raise SystemExit(f"second attempt was not refused as a duplicate: {row['chain_reason']}")

    # 4. the owner approves INV-1015 once; the approval lane pays it
    if not settled(inv15.id):
        open_funds()
        decision = pay_call(inv15, approval=True)
        if not decision.chain_success:
            raise SystemExit(f"the approved payment was refused: {decision.chain_reason}")
    row_from_entry("approval lane, first attempt", settled(inv15.id)[0], True)

    # 5. the agent lane cannot revisit an invoice the owner approved
    if refused(inv15.id, "pay"):
        row_from_entry("agent lane after approval", refused(inv15.id, "pay")[0], False)
    else:
        open_funds()
        decision = pay_call(inv15, approval=False)
        row = {"label": "agent lane after approval", "invoice": inv15.id, "invoice_key": key15,
               "amount_uqor": inv15.amount_uqor, "action": "pay", "tx": decision.tx_hash,
               "paid": bool(decision.chain_success), "chain_reason": decision.chain_reason,
               "expected": "REFUSED — InvoiceAlreadyPaid", "checked_at": stamp()}
        payments.append(row)
        print(f"   {row['label']:<28} {'PAID' if row['paid'] else 'REFUSED'} {row['chain_reason'] or ''}")
        if row["paid"] or "InvoiceAlreadyPaid" not in (row["chain_reason"] or ""):
            raise SystemExit(f"the agent lane revisited an approved invoice: {row['chain_reason']}")

    # 6. a second approval for the same invoice, and 7. an approval for the
    #    invoice the agent lane already paid
    for label, inv, key, action in (
        ("approval lane, second attempt", inv15, key15, "pay_with_approval"),
        ("approval after agent lane", inv14, key14, "pay_with_approval"),
    ):
        if refused(inv.id, action):
            row_from_entry(label, refused(inv.id, action)[0], False)
            continue
        open_funds()
        decision = pay_call(inv, approval=True)
        row = {"label": label, "invoice": inv.id, "invoice_key": key, "amount_uqor": inv.amount_uqor,
               "action": action, "tx": decision.tx_hash, "paid": bool(decision.chain_success),
               "chain_reason": decision.chain_reason, "expected": "REFUSED — InvoiceAlreadyPaid",
               "checked_at": stamp()}
        payments.append(row)
        print(f"   {label:<28} {'PAID' if row['paid'] else 'REFUSED'} {row['chain_reason'] or ''}")
        if row["paid"] or "InvoiceAlreadyPaid" not in (row["chain_reason"] or ""):
            raise SystemExit(f"{label}: not refused as a duplicate: {row['chain_reason']}")

    time.sleep(3)

    from qorsdk.address import hex_to_bech32

    payee_bech32 = hex_to_bech32(vendor.payee)

    def transfers_to(tx_hash: str) -> int:
        total = 0
        body = None
        for _ in range(3):
            try:
                body = __import__("httpx").get(f"{cfg.rest}/cosmos/tx/v1beta1/txs/{tx_hash}", timeout=30).json()
                break
            except Exception:
                time.sleep(2)
        for ev in ((body or {}).get("tx_response", {}) or {}).get("events", []) or []:
            if ev.get("type") != "transfer":
                continue
            attrs = {a["key"]: a["value"] for a in ev.get("attributes", [])}
            if attrs.get("recipient") == payee_bech32:
                amount = attrs.get("amount", "")
                if amount.endswith("uqor"):
                    total += int(amount.removesuffix("uqor"))
        return total

    settled_14, settled_15 = settled(inv14.id), settled(inv15.id)
    moved = {inv14.id: sum(transfers_to(e["tx_hash"]) for e in settled_14),
             inv15.id: sum(transfers_to(e["tx_hash"]) for e in settled_15)}
    one_each = (len(settled_14) == 1 and len(settled_15) == 1
                and moved[inv14.id] == inv14.amount_uqor and moved[inv15.id] == inv15.amount_uqor)

    record_obj = {
        "window": window,
        "general_run_output": first_out,
        "restart_output": restart_out,
        "restart_skipped_settled_invoice": skipped_on_restart,
        "payments": payments,
        "payee": vendor.payee,
        "moved_to_payee_uqor": moved,
        "settlements_per_invoice": {"INV-1014": len(settled_14), "INV-1015": len(settled_15)},
        "one_deposit_each": one_each,
        "contract_total_spent_uqor": view(evm, contract, T.SEL_TOTAL_SPENT),
        "settled_invoice_count": view(evm, contract, T.SEL_SETTLED_INVOICE_COUNT),
        "checked_at": stamp(),
    }
    if not one_each:
        raise SystemExit(f"the vendor was not paid exactly once per invoice: {record_obj['settlements_per_invoice']} {moved}")
    state["pay"] = record_obj
    save_state(state)
    print(json.dumps({k: record_obj[k] for k in
                      ("one_deposit_each", "settlements_per_invoice", "moved_to_payee_uqor",
                       "contract_total_spent_uqor", "settled_invoice_count")}, indent=2))
    return record_obj


# --------------------------------------------------------------------------- #
# 6. the token invoice through the general run
# --------------------------------------------------------------------------- #
def cmd_token() -> dict:
    state = load_state()
    if state.get("token", {}).get("tx"):
        print("token invoice already settled:", state["token"]["tx"])
        return state["token"]

    cfg, chain, evm, agent, dep = build()
    mandate = Mandate.load(str(MANDATE))
    inv = next(i for i in mandate.invoices if i.id == "INV-1016")
    vendor = mandate.vendor(inv.vendor)
    before = chain.balances(vendor.token_account).get(inv.denom, 0)

    out = run_cli("run", "--invoice", "INV-1016")
    entry = json.loads((ROOT / "demo" / "journal.jsonl").read_text().splitlines()[-1])
    if entry.get("invoice") != "INV-1016" or not entry.get("chain_success"):
        raise SystemExit("the general run did not settle INV-1016")
    time.sleep(4)
    after = chain.balances(vendor.token_account).get(inv.denom, 0)
    record = {
        "invoice": inv.id,
        "amount": inv.amount_uqor,
        "currency": "fUSD",
        "denom": inv.denom,
        "to": vendor.token_account,
        "tx": entry["tx_hash"],
        "paid": True,
        "vendor_before": before,
        "vendor_after": after,
        "delta": after - before,
        "run_output": out,
        "checked_at": stamp(),
    }
    if record["delta"] != inv.amount_uqor:
        raise SystemExit(f"vendor fUSD delta {record['delta']} != {inv.amount_uqor}")
    state["token"] = record
    save_state(state)
    print(json.dumps({k: record[k] for k in ("tx", "amount", "currency", "delta")}, indent=2))
    return record


# --------------------------------------------------------------------------- #
STEPS = {
    "invoices": cmd_invoices,
    "deploy": cmd_deploy,
    "setup": cmd_setup,
    "link": cmd_link,
    "caps": cmd_caps,
    "pay": cmd_pay,
    "token": cmd_token,
}


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    order = list(STEPS) if arg == "all" else [arg]
    for step in order:
        if step not in STEPS:
            raise SystemExit(f"unknown step {step!r}; one of {', '.join(STEPS)}, all")
        print(f"\n== {step}")
        STEPS[step]()
