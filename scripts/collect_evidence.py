#!/usr/bin/env python3
"""Collect the on-chain evidence into docs/EVIDENCE.md.

Reads the deployment record, the agent journal and the chain itself (vendors,
caps, rules, window, transaction results) and writes a document a reviewer can
check hash by hash.

    python scripts/collect_evidence.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from qoreguard import treasury as T  # noqa: E402
from qoreguard.chain import Chain  # noqa: E402
from qoreguard.config import settings  # noqa: E402
from qoreguard.evm import Evm  # noqa: E402
from qoreguard import accounts as acct_mod  # noqa: E402
from qorsdk import derive_native_account  # noqa: E402
from qorsdk.address import bech32_to_hex  # noqa: E402

OUT = ROOT / "docs" / "EVIDENCE.md"
EXPLORER = "https://explore.qore.network/tx/"


def main() -> int:
    cfg = settings()
    chain = Chain(cfg)
    evm = Evm(cfg.evm_rpc, cfg.evm_chain_id)
    dep_path = ROOT / ".keys" / "deployment.json"
    if not dep_path.exists():
        print("no deployment yet")
        return 1
    dep = json.loads(dep_path.read_text())
    contract = dep["contract"]

    treasury = acct_mod.load(cfg)
    owner = treasury.owner
    relayer = treasury.relayer
    owner_pqc = owner.pqc
    agent_pub = (ROOT / ".keys" / "agent_ed25519.key").read_bytes()

    lines: list[str] = []
    add = lines.append

    add("# Evidence — what the testnet actually recorded\n")
    add(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from "
        f"`scripts/collect_evidence.py`. Network `{cfg.chain_id}`, EVM chain id "
        f"{cfg.evm_chain_id}. Every hash below can be opened at {EXPLORER}<hash> "
        "with the explorer switched to **Testnet**.\n"
    )

    add("## Accounts\n")
    add("| Role | bech32 | 0x form | test QOR now |")
    add("| --- | --- | --- | ---: |")
    for name, qor_addr, evm_addr in (
        ("owner (eth-native)", owner.cosmos, owner.evm),
        ("relayer (native)", relayer.address, bech32_to_hex(relayer.address)),
        ("vendor 1 payee", "0x305e5935929844bECA43aB6376226a7916A2d129", "0x305e5935929844bECA43aB6376226a7916A2d129"),
        ("vendor 2 payee", "0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D", "0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D"),
    ):
        try:
            bal = (chain.qor(qor_addr) if qor_addr.startswith("qor1") else 0.0)
        except Exception:
            bal = 0.0
        if not qor_addr.startswith("qor1"):
            bal = evm.balance_wei(qor_addr) / 1e18
        add(f"| {name} | `{qor_addr}` | `{evm_addr}` | {bal:.3f} |")
    add("")

    add("## Post-quantum keys\n")
    st = chain.pqc_status(owner.cosmos)
    add(f"- owner ML-DSA-87 public key registered: **{st['registered']}**, {len(st['pubkey'])} bytes")
    add(f"- canonical derivation: `shake256(\"qorechain:pqc:v1|\" + address + \"|\" + mnemonic)`")
    add(f"- public key sha256: `{__import__('hashlib').sha256(owner_pqc.public_key).hexdigest()}`")
    add("")

    add("## EVM window\n")
    w = chain.evm_window(owner.cosmos)
    add(f"- live: **{w.live}**")
    add(f"- opened at height {w.opened_height}, expires at {w.expiry_height} ({w.remaining_blocks} blocks left)")
    add(f"- transactions: {w.used_txs} used of {w.max_txs}; value: {w.used_value} of {w.max_value} uqor used")
    add(f"- allowed targets: {list(w.allowed_targets) or 'none (deployment window)'}")
    add("")

    add("## The agent's on-chain limits\n")
    try:
        rules = chain.spending_rules(owner.cosmos)
        for rule in rules.rules:
            add(f"- rule `{rule.get('id')}`: per_tx {rule.get('per_tx_limit')} uqor, "
                f"daily {rule.get('daily_limit')} uqor, denoms {rule.get('allowed_denoms')}")
            for call in rule.get("allowed_evm_calls", []) or []:
                add(f"  - allowed EVM call: `{call}`")
        for auth in rules.authenticator_rules:
            add(f"- linked key `{auth.scheme}`: {auth.pubkey_hex[:32]}…")
    except Exception as exc:
        add(f"- (could not read rules: {type(exc).__name__})")
    add("")

    ops_path = ROOT / "demo" / "operations.jsonl"
    if ops_path.exists():
        add("## Owner actions on chain\n")
        add("| action | by | tx | note |")
        add("| --- | --- | --- | --- |")
        for line in ops_path.read_text().splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            tx = o.get("tx", "")
            add(f"| {o['kind']} | {o['actor']} | [`{tx[:18]}…`]({EXPLORER}{tx}) | {o.get('note','')} |")
        add("")

    add("## Treasury contract\n")
    add(f"- address: `{contract}`")
    add(f"- deploy tx: `{dep.get('tx')}` (block {dep.get('block')})")
    for selector, label in (
        (T.SEL_TREASURY_DAILY_CAP, "treasury daily cap (uqor)"),
        (T.SEL_TREASURY_SPENT_TODAY, "spent today (uqor)"),
        (T.SEL_TOTAL_SPENT, "total paid (uqor)"),
        (T.SEL_APPROVAL_NONCE, "PQ approval nonce"),
        (T.SEL_VENDOR_COUNT, "vendors"),
    ):
        try:
            raw = evm.eth_call(contract, selector)
            add(f"- {label}: {int(raw, 16) if raw and raw != '0x' else 0}")
        except Exception:
            pass
    add("")

    add("## Vendors\n")
    add("| id | payee | per-tx cap | daily cap | active |")
    add("| ---: | --- | ---: | ---: | :---: |")
    try:
        from eth_abi import decode as abi_decode
        from eth_abi import encode as abi_encode

        count = int(evm.eth_call(contract, T.SEL_VENDOR_COUNT), 16)
        for i in range(count):
            vid = int(evm.eth_call(contract, T.SEL_VENDOR_ID_AT + abi_encode(["uint256"], [i])), 16)
            raw = evm.eth_call(contract, T.SEL_VENDOR + abi_encode(["uint256"], [vid]))
            payee, _label, per_tx, daily, active = abi_decode(
                ["address", "bytes32", "uint96", "uint96", "bool"], bytes.fromhex(raw[2:])
            )
            add(f"| {vid} | `{payee}` | {per_tx} | {daily} | {'yes' if active else 'no'} |")
    except Exception as exc:
        add(f"| — | (could not read vendors: {type(exc).__name__}) | | | |")
    add("")

    add("## Transactions\n")
    journal_path = ROOT / "demo" / "journal.jsonl"
    if journal_path.exists():
        add("| invoice | vendor | amount (QOR) | result | tx |")
        add("| --- | --- | ---: | --- | --- |")
        for e in [json.loads(l) for l in journal_path.read_text().splitlines() if l.strip()]:
            result = "PAID" if e.get("chain_success") else "REFUSED"
            reason = (e.get("chain_reason") or "")[:120].replace("|", "/")
            tx = e.get("tx_hash") or ""
            add(
                f"| {e.get('invoice','')} | {e.get('vendor_label','')} | "
                f"{(e.get('amount_uqor',0)/1_000_000):.3f} | {result}"
                + (f" — {reason}" if reason else "")
                + f" | [`{tx[:16]}…`]({EXPLORER}{tx}) |"
            )
    else:
        add("_no journal yet_")
    add("")

    add("## Verifying a payment yourself\n")
    add("```bash")
    add("    curl -s https://api-testnet.qore.host/cosmos/tx/v1beta1/txs/<hash> | jq .tx_response.code")
    add("```")
    add("")
    add("A relayed payment is a `MsgExecuteEVM`; the transaction is signed hybrid by the")
    add("relayer, and the payment inside is authorised by the agent's ed25519 signature")
    add("over `evmAuthSignBytesV2`. A refused payment keeps transaction `code: 0` and")
    add("carries `abstractaccount_execution_failed` with the contract's reason — that is")
    add("the refusal, not a failed transaction.")

    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
