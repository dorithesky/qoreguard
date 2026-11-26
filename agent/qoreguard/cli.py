"""QoreGuard command line — the operator surface used to build, run and audit.

    python -m qoreguard.cli status
    python -m qoreguard.cli register
    python -m qoreguard.cli window open --txs 1 --value 1000000 --blocks 100
    python -m qoreguard.cli deploy
    python -m qoreguard.cli setup
    python -m qoreguard.cli link
    python -m qoreguard.cli run
    python -m qoreguard.cli approve INV-1002 --amount 500000
    python -m qoreguard.cli revoke

Every command prints what the chain answered, not what was intended.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from qorsdk import derive_native_account, derive_pqc_canonical, link_authenticator_msgs, link_proof_ed25519
from qorsdk.address import bech32_to_hex

from . import treasury as T
from .agent import Agent, Journal
from .chain import Chain
from .config import ROOT, Settings, settings
from .evm import Evm, uqor_to_wei
from .planner import Mandate, annotate_with_llm, plan

ARTIFACT = ROOT / "contracts" / "out" / "QoreGuardTreasury.sol" / "QoreGuardTreasury.json"
DEPLOYMENT = ROOT / ".keys" / "deployment.json"
MANDATE_PATH = ROOT / "demo" / "mandate.json"


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #
def build(cfg: Settings) -> tuple[Chain, Evm, Agent]:
    chain = Chain(cfg)
    evm = Evm(cfg.evm_rpc, cfg.evm_chain_id)
    deployment = json.loads(DEPLOYMENT.read_text()) if DEPLOYMENT.exists() else {}
    contract = deployment.get("contract", "0x" + "0" * 40)
    agent = Agent(cfg, chain, evm, contract, ROOT / "demo" / "journal.jsonl")
    return chain, evm, agent


def week(value: float) -> str:
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def say(title: str, **fields) -> None:
    print(f"\n== {title}")
    for k, v in fields.items():
        print(f"   {k}: {v}")


def contract_view(evm: Evm, contract: str, selector: bytes, arg_types: list[str] | None = None, args: list | None = None) -> str:
    data = selector + (abi_encode(arg_types, args) if arg_types else b"")
    return evm.eth_call(contract, data)


def require_deployment() -> dict:
    if not Path(DEPLOYMENT).exists():
        raise SystemExit("no deployment yet — run `deploy` first")
    return json.loads(Path(DEPLOYMENT).read_text())


# ---------------------------------------------------------------------- #
# commands
# ---------------------------------------------------------------------- #
def cmd_status(cfg: Settings, args: argparse.Namespace) -> None:
    chain, evm, agent = build(cfg)
    owner = derive_native_account(cfg.owner_mnemonic)
    relayer = derive_native_account(cfg.relayer_mnemonic)
    kp = derive_pqc_canonical(owner.address, cfg.owner_mnemonic)

    say("network", chain_id=cfg.chain_id, rest=cfg.rest, evm_chain_id=evm.chain_id_on_chain())
    for name, acct in (("owner", owner), ("relayer", relayer)):
        try:
            bal = chain.qor(acct.address)
            acct_no, seq = chain.account_state(acct.address)
        except Exception:
            bal, acct_no, seq = 0.0, -1, -1
        st = chain.pqc_status(acct.address).get("registered")
        say(
            name,
            address=acct.address,
            evm=bech32_to_hex(acct.address),
            qor=week(bal),
            account_number=acct_no,
            sequence=seq,
            pqc_registered=st,
        )

    w = chain.evm_window(owner.address)
    say(
        "evm window",
        live=w.live,
        remaining_blocks=w.remaining_blocks,
        remaining_txs=w.remaining_txs,
        remaining_value_uqor=w.remaining_value,
        targets=list(w.allowed_targets),
    )
    nonce = chain.auth_nonce(owner.address, agent.pubkey)
    say("agent linker key", pubkey=agent.pubkey.hex()[:32] + "…", nonce=nonce.nonce, linked=nonce.linked)

    if Path(DEPLOYMENT).exists():
        dep = require_deployment()
        c = dep["contract"]
        count = int(contract_view(evm, c, T.SEL_VENDOR_COUNT), 16)
        say(
            "treasury contract",
            address=c,
            owner_account=T.decode_address(contract_view(evm, c, T.SEL_OWNER_ACCOUNT)),
            vendors=count,
            approval_nonce=int(contract_view(evm, c, T.SEL_APPROVAL_NONCE), 16),
            treasury_daily_cap_uqor=int(contract_view(evm, c, T.SEL_TREASURY_DAILY_CAP), 16),
            spent_today_uqor=int(contract_view(evm, c, T.SEL_TREASURY_SPENT_TODAY), 16),
            total_spent_uqor=int(contract_view(evm, c, T.SEL_TOTAL_SPENT), 16),
        )
        for i in range(count):
            vid = int(contract_view(evm, c, T.SEL_VENDOR_ID_AT, ["uint256"], [i]), 16)
            raw = contract_view(evm, c, T.SEL_VENDOR, ["uint256"], [vid])
            payee, label, per_tx, daily, active = abi_decode(
                ["address", "bytes32", "uint96", "uint96", "bool"], bytes.fromhex(raw[2:])
            )
            spent = int(contract_view(evm, c, T.SEL_SPENT_TODAY, ["uint256"], [vid]), 16)
            say(
                f"vendor {vid}",
                payee=payee,
                label_hash="0x" + label.hex()[:16],
                per_tx_cap_uqor=per_tx,
                daily_cap_uqor=daily,
                spent_today_uqor=spent,
                active=active,
            )


def cmd_register(cfg: Settings, args: argparse.Namespace) -> None:
    chain, _, agent = build(cfg)
    for name, mnemonic in (
        ("owner", cfg.owner_mnemonic),
        ("relayer", cfg.relayer_mnemonic),
        ("vendor", cfg.vendor_mnemonic),
    ):
        if not mnemonic:
            continue
        acct = derive_native_account(mnemonic)
        if chain.qor(acct.address) == 0:
            print(f"{name}: no test QOR yet at {acct.address} — fund it from the faucet first")
            continue
        res = chain.ensure_pqc(acct, mnemonic)
        say(f"register {name}", address=acct.address, **{k: str(v) for k, v in res.items()})
    print("\nkey registration is idempotent: an already-registered key sends nothing")


def cmd_window(cfg: Settings, args: argparse.Namespace) -> None:
    chain, evm, agent = build(cfg)
    owner = derive_native_account(cfg.owner_mnemonic)
    if args.action == "status":
        w = chain.evm_window(owner.address)
        say(
            "evm window",
            live=w.live,
            opened_height=w.opened_height,
            expiry_height=w.expiry_height,
            remaining_blocks=w.remaining_blocks,
            max_txs=w.max_txs,
            used_txs=w.used_txs,
            remaining_txs=w.remaining_txs,
            remaining_value_uqor=w.remaining_value,
            targets=list(w.allowed_targets),
        )
        return
    if args.action == "close":
        from qorsdk.messages.qorechain import MsgCloseEVMWindow

        outcome = chain.send_hybrid(
            owner, agent.owner_pqc, [MsgCloseEVMWindow(sender=owner.address)], memo="qoreguard:close-window"
        )
        say("close window", tx=outcome.tx_hash, code=outcome.code, log=outcome.raw_log)
        return

    from qorsdk.messages.qorechain import MsgOpenEVMWindow

    msg = MsgOpenEVMWindow(
        sender=owner.address,
        blocks=args.blocks,
        max_txs=args.txs,
        max_value=str(args.value),
        native_only=False,
        allowed_targets=tuple(args.target or ()),
    )
    outcome = chain.send_hybrid(owner, agent.owner_pqc, [msg], memo="qoreguard:open-window")
    if outcome.code != 0:
        raise SystemExit(f"window refused: {outcome.raw_log}")
    w = chain.evm_window(owner.address)
    say(
        "window opened",
        tx=outcome.tx_hash,
        blocks=args.blocks,
        max_txs=args.txs,
        max_value_uqor=args.value,
        targets=list(args.target or ()),
        expiry_height=w.expiry_height,
    )
    print("   opening advanced the account sequence — EVM nonces are read after this point")


def cmd_deploy(cfg: Settings, args: argparse.Namespace) -> None:
    chain, evm, agent = build(cfg)
    owner = derive_native_account(cfg.owner_mnemonic)
    cap = args.treasury_cap
    nonce = chain.account_state(owner.address)[1]
    tx = evm.deploy(
        owner.private_key,
        str(ARTIFACT),
        ["address", "uint96"],
        [bech32_to_hex(owner.address), cap],
        gas=args.gas,
        nonce=nonce,
    )
    receipt = evm.wait(tx)
    if not receipt.ok:
        raise SystemExit(f"deploy reverted (tx {tx})")
    contract = receipt.contract_address
    if not contract:
        raise SystemExit(f"deploy receipt carries no contract address (tx {tx})")
    DEPLOYMENT.write_text(json.dumps({"contract": contract, "tx": tx, "block": receipt.block_number}, indent=2))
    say(
        "deployed QoreGuardTreasury",
        contract=contract,
        tx=tx,
        block=receipt.block_number,
        gas_used=receipt.gas_used,
        treasury_daily_cap_uqor=cap,
    )


def cmd_setup(cfg: Settings, args: argparse.Namespace) -> None:
    """Owner registers the vendors and the treasury cap, each PQ-signed."""
    chain, evm, agent = build(cfg)
    dep = require_deployment()
    contract = dep["contract"]
    mandate = Mandate.load(args.mandate)

    nonce = int(contract_view(evm, contract, T.SEL_APPROVAL_NONCE), 16)
    for vendor in mandate.vendors:
        if args.only and vendor.label not in args.only:
            continue
        payload = T.payload_add_vendor(
            vendor.id, vendor.payee, vendor.label, vendor.per_tx_cap_uqor, vendor.daily_cap_uqor
        )
        sig = agent.owner_sign(T.ADD_VENDOR, payload, nonce)
        data = T.calldata_add_vendor(
            vendor.id,
            vendor.payee,
            vendor.label,
            vendor.per_tx_cap_uqor,
            vendor.daily_cap_uqor,
            nonce,
            sig.pubkey,
            sig.signature,
        )
        tx = agent.owner_calls_contract(data, gas=args.gas)
        receipt = evm.wait(tx)
        say(
            f"addVendor {vendor.label}",
            vendor_id=vendor.id,
            payee=vendor.payee,
            per_tx_cap_uqor=vendor.per_tx_cap_uqor,
            daily_cap_uqor=vendor.daily_cap_uqor,
            status="ok" if receipt.ok else "REVERTED",
            tx=tx,
            approval_nonce=nonce,
            signed_message=f"{len(sig.message)}B, sig {len(sig.signature)}B",
        )
        if not receipt.ok:
            raise SystemExit("addVendor reverted — stop and inspect")
        nonce += 1
        time.sleep(1)

    if args.set_cap:
        payload = T.payload_set_treasury_cap(mandate.treasury_daily_cap_uqor)
        sig = agent.owner_sign(T.SET_TREASURY_CAP, payload, nonce)
        data = T.calldata_set_treasury_cap(
            mandate.treasury_daily_cap_uqor, nonce, sig.pubkey, sig.signature
        )
        tx = agent.owner_calls_contract(data, gas=args.gas)
        receipt = evm.wait(tx)
        say("setTreasuryCap", cap=mandate.treasury_daily_cap_uqor, status="ok" if receipt.ok else "REVERTED", tx=tx)


def cmd_link(cfg: Settings, args: argparse.Namespace) -> None:
    """Owner links the agent's ed25519 key to the treasury account, with limits."""
    chain, evm, agent = build(cfg)
    dep = require_deployment()
    contract = dep["contract"]
    mandate = Mandate.load(args.mandate)
    owner = derive_native_account(cfg.owner_mnemonic)

    proof = link_proof_ed25519(
        agent.key.private_bytes_raw() if hasattr(agent.key, "private_bytes_raw") else _raw(agent.key),
        chain_id=cfg.chain_id,
        account_address=owner.address,
    )
    expiry = int(time.time()) + mandate.agent_link_hours * 3600
    rule = {
        "id": "qoreguard-agent",
        "per_tx_limit": str(mandate.agent_per_tx_cap_uqor),
        "daily_limit": str(mandate.agent_daily_cap_uqor),
        "allowed_denoms": ["uqor"],
        "allowed_evm_calls": [
            f"{contract}/0x{T.SEL_PAY.hex()}",
            f"{contract}/0x{T.SEL_PAY_WITH_APPROVAL.hex()}",
        ],
        "enabled": True,
    }
    msgs = link_authenticator_msgs(
        owner=owner.address,
        chain_id=cfg.chain_id,
        scheme=proof.scheme,
        pubkey=proof.pubkey,
        permissions=["send", "evm"],
        expiry_unix=expiry,
        possession_proof=proof.possession_proof,
        label="qoreguard-agent",
        spending_rule=rule,
    )
    outcome = chain.send_hybrid(owner, agent.owner_pqc, msgs, memo="qoreguard:link-agent")
    say(
        "link agent key",
        owner=owner.address,
        agent_pubkey=agent.pubkey.hex(),
        permissions="send, evm",
        per_tx_limit_uqor=mandate.agent_per_tx_cap_uqor,
        daily_limit_uqor=mandate.agent_daily_cap_uqor,
        expiry=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry)),
        allowed_evm_calls=rule["allowed_evm_calls"],
        tx=outcome.tx_hash,
        code=outcome.code,
        log=outcome.raw_log if outcome.code else "ok",
    )
    rules = chain.spending_rules(owner.address)
    say("chain now enforces", rules=rules)


def cmd_run(cfg: Settings, args: argparse.Namespace) -> None:
    chain, evm, agent = build(cfg)
    mandate = Mandate.load(args.mandate)
    journal = Journal(ROOT / "demo" / "journal.jsonl")
    paid = {e["invoice"] for e in journal.entries() if e.get("kind") == "payment" and e.get("chain_success")}

    items = annotate_with_llm(plan(mandate, paid), mandate)
    print(f"\n== plan ({len(items)} invoices)")
    for item in items:
        print(f"   {item.invoice.id:<10} {item.invoice.vendor:<14} {item.invoice.amount_uqor:>8} uqor  "
              f"{item.action:<5} {item.reason}")

    for item in items:
        if item.action != "pay" or item.vendor is None:
            continue
        print(f"\n-- {item.invoice.id}: {item.invoice.memo}")
        decision = agent.pay(
            item.vendor.id,
            item.vendor.label,
            item.invoice.amount_uqor,
            f"{item.invoice.id} {item.invoice.memo}",
        )
        print(f"   tx      {decision.tx_hash}")
        print(f"   result  {'PAID' if decision.chain_success else 'REFUSED'} "
              f"{'' if decision.chain_success else '— ' + str(decision.chain_reason)}")
        print(f"   intent  {decision.intent.digest[:32]}… signed {len(decision.intent_signature)//2}B")


def cmd_approve(cfg: Settings, args: argparse.Namespace) -> None:
    """Owner signs one over-cap payment; the agent submits it."""
    chain, evm, agent = build(cfg)
    dep = require_deployment()
    mandate = Mandate.load(args.mandate)
    invoice = next((i for i in mandate.invoices if i.id == args.invoice), None)
    if invoice is None:
        raise SystemExit(f"no invoice {args.invoice} in {args.mandate}")
    vendor = mandate.vendor(invoice.vendor)
    amount = args.amount or invoice.amount_uqor

    nonce = int(contract_view(evm, dep["contract"], T.SEL_APPROVAL_NONCE), 16)
    memo_prefix = f"{invoice.id} {invoice.memo}"
    # The signature binds the memo commitment the agent will put on chain, so the
    # owner approves one exact payment, not a shape of payment.
    intent = agent.build_intent(vendor.id, vendor.label, amount, memo_prefix)
    intent_memo = intent.onchain_memo
    payload = T.payload_exception(vendor.id, amount, intent_memo)
    sig = agent.owner_sign(T.PAY_WITH_APPROVAL, payload, nonce)
    say(
        "owner approved one payment",
        invoice=invoice.id,
        vendor=vendor.label,
        amount_uqor=amount,
        approval_nonce=nonce,
        signed_message=f"{len(sig.message)}B",
        signature=f"{len(sig.signature)}B ML-DSA-87",
        memo_commitment=intent_memo,
    )
    decision = agent.pay(vendor.id, vendor.label, amount, memo_prefix, approval=sig, intent=intent)
    print(f"   tx      {decision.tx_hash}")
    print(f"   result  {'PAID (with post-quantum approval)' if decision.chain_success else 'REFUSED — ' + str(decision.chain_reason)}")


def cmd_revoke(cfg: Settings, args: argparse.Namespace) -> None:
    chain, evm, agent = build(cfg)
    from qorsdk.messages import qorechain

    owner = derive_native_account(cfg.owner_mnemonic)
    msg = qorechain.abstractaccount.revoke_authenticator(
        owner=owner.address,
        account_address=owner.address,
        scheme="ed25519",
        pubkey=agent.pubkey,
    )
    outcome = chain.send_hybrid(owner, agent.owner_pqc, [msg], memo="qoreguard:revoke-agent")
    say("revoke agent key", tx=outcome.tx_hash, code=outcome.code, log=outcome.raw_log if outcome.code else "ok")


def cmd_verify(cfg: Settings, args: argparse.Namespace) -> None:
    """Re-check every journal entry: intent signature, memo commitment, chain state."""
    from .agent import verify_intent

    journal = Journal(ROOT / "demo" / "journal.jsonl")
    entries = journal.entries()
    if not entries:
        print("journal is empty")
        return
    ok = bad = 0
    for e in entries:
        intent = e["intent"]
        canonical = json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
        sig_ok = verify_intent(canonical, bytes.fromhex(e["intent_signature"]), agent_pubkey_from(intent))
        memo_ok = e["onchain_memo"].startswith("qg:v1:") and e["onchain_memo"] == (
            "qg:v1:" + __import__("hashlib").sha256(canonical).hexdigest()[:40]
        )
        ok += 1 if sig_ok and memo_ok else 0
        bad += 0 if sig_ok and memo_ok else 1
        print(f"{'OK  ' if sig_ok and memo_ok else 'BAD '} {e['invoice']:<10} {e['amount_uqor']:>8} uqor "
              f"intent-sig={sig_ok} memo={memo_ok} tx={e['tx_hash'][:12]}…")
    print(f"\n{ok} verified, {bad} bad")


def agent_pubkey_from(intent: dict) -> bytes:
    return bytes.fromhex(intent["agent_pubkey"])


def _raw(key) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


# ---------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qoreguard", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="read every state the project depends on")

    sub.add_parser("register", help="register ML-DSA-87 keys (idempotent)")

    w = sub.add_parser("window", help="scoped EVM window: open / status / close")
    w.add_argument("action", choices=["open", "status", "close"])
    w.add_argument("--blocks", type=int, default=100)
    w.add_argument("--txs", type=int, default=1)
    w.add_argument("--value", type=int, default=1_000_000, help="max_value in uqor")
    w.add_argument("--target", action="append", help="allowed EVM target (0x address), repeatable")

    d = sub.add_parser("deploy", help="deploy the treasury contract from the owner account")
    d.add_argument("--treasury-cap", type=int, default=1_200_000)
    d.add_argument("--gas", type=int, default=6_000_000)

    s = sub.add_parser("setup", help="owner adds vendors and sets the cap (PQ-signed)")
    s.add_argument("--mandate", default=str(MANDATE_PATH))
    s.add_argument("--only", action="append")
    s.add_argument("--set-cap", action="store_true")
    s.add_argument("--gas", type=int, default=600_000)

    l = sub.add_parser("link", help="link the agent key with a spending rule")
    l.add_argument("--mandate", default=str(MANDATE_PATH))

    r = sub.add_parser("run", help="run the agent over the mandate")
    r.add_argument("--mandate", default=str(MANDATE_PATH))

    a = sub.add_parser("approve", help="owner approves one over-cap payment")
    a.add_argument("invoice")
    a.add_argument("--amount", type=int)
    a.add_argument("--mandate", default=str(MANDATE_PATH))

    sub.add_parser("revoke", help="revoke the agent key")

    sub.add_parser("verify", help="re-verify the journal")

    args = parser.parse_args(argv)
    cfg = settings()
    handlers = {
        "status": cmd_status,
        "register": cmd_register,
        "window": cmd_window,
        "deploy": cmd_deploy,
        "setup": cmd_setup,
        "link": cmd_link,
        "run": cmd_run,
        "approve": cmd_approve,
        "revoke": cmd_revoke,
        "verify": cmd_verify,
    }
    handlers[args.command](cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
