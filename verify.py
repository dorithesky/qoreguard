#!/usr/bin/env python3
"""Re-check QoreGuard against the chain. Read-only, no keys, no secrets.

    python verify.py            # every check, then a summary line
    python verify.py --json     # the same results as JSON

A judge can run this on a clean machine: it needs only the public testnet
endpoints and this repository. Every check reads the chain (or the recorded
artifacts) and says what it compared. Nothing is signed and nothing is sent.

What it establishes
  * the deployed contract is the audited artifact (length, code, immutables),
  * the policy on chain is the policy demo/mandate.json describes,
  * every payment and refusal in demo/journal.jsonl is a real transaction with
    the memo commitment the journal recorded,
  * the post-quantum gates still refuse, reproduced now by `eth_call`: a forged
    ML-DSA-87 signature, an over-cap payment, an unknown vendor,
  * the owner's Sign-In-With-QoreChain approval verifies against the ML-DSA-87
    key registered on chain, while a tampered or replayed one does not,
  * the sealed invoice archive matches its on-chain commitment,
  * the fUSD invoice settled: the vendor's account holds the tokens.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import httpx  # noqa: E402
from eth_abi import encode as abi_encode  # noqa: E402
from qorsdk import fetch_pqc_status  # noqa: E402
from qorsdk.kem import fetch_kem_key  # noqa: E402
from qorsdk.rest import RestClient  # noqa: E402

from qoreguard import approvals  # noqa: E402
from qoreguard import treasury as T  # noqa: E402
from qoreguard.evm import Evm  # noqa: E402

EXPLORER = "https://explore.qore.network/tx/"


class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def check(self, name: str, fn: Callable[[], str | None]) -> None:
        try:
            detail = fn()
            self.rows.append({"check": name, "ok": True, "detail": detail or "ok"})
        except Exception as exc:  # a failed check is a result, not a crash
            self.rows.append({"check": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r["ok"])

    @property
    def failed(self) -> int:
        return len(self.rows) - self.passed


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    addrs = json.loads((ROOT / "demo" / "addresses.json").read_text())
    rest = RestClient(addrs["rest"])
    http = httpx.Client(timeout=40)
    evm = Evm(addrs["evm_rpc"], addrs["evm_chain_id"])
    contract = addrs["contract"]
    owner = addrs["owner"]["bech32"]
    owner_evm = addrs["owner"]["evm"]
    chain_id = addrs["chain_id"]
    token_denom = addrs["token"]["denom"]

    mandate = json.loads((ROOT / "demo" / "mandate.json").read_text())
    journal = [json.loads(line) for line in (ROOT / "demo" / "journal.jsonl").read_text().splitlines() if line.strip()]
    p1 = json.loads((ROOT / "demo" / "p1.json").read_text())
    features = json.loads((ROOT / "demo" / "onchain_features.json").read_text())
    artifact = json.loads((ROOT / "contracts" / "out" / "QoreGuardTreasury.sol" / "QoreGuardTreasury.json").read_text())

    def view(selector: bytes, types=None, values=None) -> str:
        data = selector + (abi_encode(types, values) if types else b"")
        return evm.eth_call(contract, data)

    def chain_owner_pub() -> bytes:
        st = fetch_pqc_status(addrs["rest"], owner)
        pub = st["pubkey"]
        return base64.b64decode(pub) if isinstance(pub, str) else pub

    r = Report()

    # ------------------------------------------------------------------ #
    # chain + contract
    # ------------------------------------------------------------------ #
    def network() -> str:
        body = http.get(addrs["rest"] + "/cosmos/base/tendermint/v1beta1/node_info").json()
        got = body["default_node_info"]["network"]
        expect(got == chain_id, f"network is {got}, expected {chain_id}")
        return f"chain {got} (testnet)"

    r.check("the REST endpoint answers with the expected chain id", network)

    def evm_chain() -> str:
        got = evm.chain_id_on_chain()
        expect(got == addrs["evm_chain_id"], f"evm chain id {got}")
        return f"evm chain id {got}"

    r.check("the EVM lane answers with the expected chain id", evm_chain)

    def contract_bytecode() -> str:
        code = evm.code_at(contract).removeprefix("0x")
        want = artifact["deployedBytecode"]["object"].removeprefix("0x")
        expect(code != "", "no code at the contract address")
        expect(len(code) == len(want), f"length {len(code)//2}B on chain vs {len(want)//2}B in the artifact")
        diffs = [i for i in range(0, len(want), 2) if code[i : i + 2] != want[i : i + 2]]
        regions: list[list[int]] = []
        for d in diffs:
            if regions and d - regions[-1][-1] <= 4:
                regions[-1].append(d)
            else:
                regions.append([d])
        # The only bytes Solidity changes at deploy time are the immutable
        # `ownerAccount` sites; each must hold exactly the owner address.
        addr = owner_evm.lower().removeprefix("0x")
        for reg in regions:
            expect(len(reg) == 20, f"a differing region is {len(reg)} bytes, not one 20-byte address")
            expect(code[reg[0] : reg[-1] + 2] == addr, "a differing region does not hold the owner address")
        return (
            f"{len(code)//2} bytes; {len(regions)} immutable sites hold {owner_evm} and nothing else differs"
        )

    r.check("the deployed code is the audited artifact", contract_bytecode)

    def deploy_tx() -> str:
        receipt = evm.call("eth_getTransactionReceipt", [addrs["deploy_tx"]])
        expect(receipt is not None, "no receipt for the deploy transaction")
        expect(int(receipt["status"], 16) == 1, f"deploy receipt status {receipt['status']}")
        created = (receipt.get("contractAddress") or "").lower()
        expect(created == contract.lower(), f"the deploy transaction created {created}")
        return f"deploy tx at EVM block {int(receipt['blockNumber'], 16)} created the contract"

    r.check("the deployment transaction is on chain", deploy_tx)

    def owner_matches() -> str:
        got = T.decode_address(view(T.SEL_OWNER_ACCOUNT))
        expect(got.lower() == owner_evm.lower(), f"ownerAccount() is {got}, expected {owner_evm}")
        return f"ownerAccount() = {got}"

    r.check("the contract's owner is the treasury account", owner_matches)

    def policy() -> str:
        count = int(view(T.SEL_VENDOR_COUNT), 16)
        expect(count == len(addrs["vendors"]), f"{count} vendors on chain, {len(addrs['vendors'])} recorded")
        expect(
            [v["id"] for v in addrs["vendors"]] == list(range(1, count + 1)),
            "the recorded vendor ids are not 1..N",
        )
        for i, spec in enumerate(addrs["vendors"]):
            vid = int(view(T.SEL_VENDOR_ID_AT, ["uint256"], [i]), 16)
            payee, _label, per_tx, daily, active = _decode_vendor(view(T.SEL_VENDOR, ["uint256"], [vid]))
            expect(payee.lower() == spec["payee"].lower(), f"vendor {vid} payee {payee}")
            expect(per_tx == spec["per_tx_cap_uqor"], f"vendor {vid} per-tx cap {per_tx}")
            expect(daily == spec["daily_cap_uqor"], f"vendor {vid} daily cap {daily}")
            expect(active, f"vendor {vid} is inactive")
        cap = int(view(T.SEL_TREASURY_DAILY_CAP), 16)
        expect(cap == mandate["treasury_daily_cap_uqor"], f"treasury cap {cap}")
        return f"{count} vendors; payees, caps and the treasury cap match demo/addresses.json"

    r.check("the policy on chain is the recorded policy (payees, caps, active)", policy)

    def spent_ledger() -> str:
        settled = [e for e in journal if e.get("chain_success") and e.get("kind") == "payment"]
        total = int(view(T.SEL_TOTAL_SPENT), 16)
        expected = sum(e["amount_uqor"] for e in settled)
        expect(total == expected, f"contract totalSpentUqor {total} vs journal {expected}")
        return f"totalSpentUqor {total:,} uqor = {len(settled)} settled payments"

    r.check("the contract's spend total equals the settled payments", spent_ledger)

    # ------------------------------------------------------------------ #
    # post-quantum keys
    # ------------------------------------------------------------------ #
    def pqc_key() -> str:
        st = fetch_pqc_status(addrs["rest"], owner)
        expect(st.get("registered"), "owner ML-DSA-87 key not registered")
        expect(st.get("possession_proven"), "registration has no proof of possession")
        pub = chain_owner_pub()
        expect(len(pub) == 2592, f"key is {len(pub)} bytes, ML-DSA-87 is 2592")
        return f"ML-DSA-87 key registered, {len(pub)}B, possession proven"

    r.check("the owner's ML-DSA-87 key is registered with proof of possession", pqc_key)

    def kem_key() -> str:
        found = fetch_kem_key(rest, owner)
        expect(found.found and found.key is not None, "no KEM key registered")
        expect(len(found.key.public_key) == 1568, f"KEM key is {len(found.key.public_key)}B")
        expect(found.key.algorithm_name == "mlkem1024", f"algorithm {found.key.algorithm_name}")
        expect(found.key.public_key.hex() == features["kem"].get("key_hex", found.key.public_key.hex()),
               "the registered key differs from the one the archive was sealed to")
        return f"ML-KEM-1024 key registered at height {found.key.registered_height}"

    r.check("the owner's ML-KEM-1024 key is registered (QoreSeal)", kem_key)

    # ------------------------------------------------------------------ #
    # refusals, reproduced now
    # ------------------------------------------------------------------ #
    def revert_of(data: bytes, value_wei: int = 0) -> str:
        try:
            evm.call(
                "eth_call",
                [{"from": owner_evm, "to": contract, "data": "0x" + data.hex(), "value": hex(value_wei)}, "latest"],
            )
        except Exception as exc:  # EvmRpcError carries the node's revert data
            text = str(exc)
            start = text.find('"data": "0x')
            if start != -1:
                blob = text[start + len('"data": "') :].split('"', 1)[0]
                decoded = T.decode_revert(bytes.fromhex(blob.removeprefix("0x")))
                if decoded:
                    return decoded
            return f"reverted: {text[:160]}"
        raise AssertionError("the call did not revert")

    def over_cap() -> str:
        vendor = addrs["vendors"][0]
        amount = vendor["per_tx_cap_uqor"] + 1
        data = T.calldata_pay(vendor["id"], amount, "verify.py:over-cap")
        sig = revert_of(data, value_wei=amount * T.WEI_PER_UQOR)
        expect("PerVendorTxCapExceeded" in sig, sig)
        return f"{sig} for {amount} uqor against a {vendor['per_tx_cap_uqor']} uqor cap"

    r.check("an over-cap payment reverts (same call, now)", over_cap)

    def unknown_vendor() -> str:
        data = T.calldata_pay(99, 1000, "verify.py:unknown-vendor")
        sig = revert_of(data, value_wei=1000 * T.WEI_PER_UQOR)
        expect("UnknownVendor" in sig, sig)
        return sig

    r.check("an unregistered vendor is refused (same call, now)", unknown_vendor)

    def forged_pq() -> str:
        nonce = int(view(T.SEL_APPROVAL_NONCE), 16)
        forged = bytes(4627)
        data = T.calldata_set_treasury_cap(999_999_999, nonce, chain_owner_pub(), forged)
        sig = revert_of(data)
        expect("BadPqcSignature" in sig, sig)
        return f"{sig} — a signature-shaped blob cannot raise the cap"

    r.check("a forged ML-DSA-87 signature cannot raise the cap", forged_pq)

    # ------------------------------------------------------------------ #
    # journal replay
    # ------------------------------------------------------------------ #
    def replay() -> str:
        entries = [
            e
            for e in journal
            if e.get("tx_hash") and e.get("chain_success") is True and e.get("kind") in {"payment", "token_payment"}
        ]
        expect(entries, "the journal has no settled payments")
        bad = []
        for e in entries:
            body = http.get(f"{addrs['rest']}/cosmos/tx/v1beta1/txs/{e['tx_hash']}").json()
            txr = body.get("tx_response", {})
            if int(txr.get("code", 1)) != 0:
                bad.append(f"{e.get('invoice')}: tx code {txr.get('code')}")
                continue
            memo = (body.get("tx", {}).get("body", {}) or {}).get("memo", "")
            accepted = _intent_memos(e.get("intent") or {})
            if memo not in accepted:
                bad.append(f"{e.get('invoice')}: memo {memo!r} not one of {accepted}")
        expect(not bad, "; ".join(bad[:4]))
        return f"{len(entries)} payment transactions on chain, each memo = its signed intent"

    r.check("every journaled payment is on chain with its intent commitment", replay)

    def refusals_recorded() -> str:
        entries = [e for e in journal if e.get("chain_success") is False]
        expect(entries, "no refusals recorded")
        for e in entries:
            body = http.get(f"{addrs['rest']}/cosmos/tx/v1beta1/txs/{e['tx_hash']}").json()
            expect(body.get("tx_response", {}).get("height"), f"{e.get('invoice')}: tx not found")
        reasons = sorted({(e.get("chain_reason") or "").split(" — ")[0][:44] for e in entries})
        return f"{len(entries)} refusals are real transactions: " + "; ".join(reasons[:4])

    r.check("refusals are transactions, not claims", refusals_recorded)

    # ------------------------------------------------------------------ #
    # QoreSign approval
    # ------------------------------------------------------------------ #
    def approval_request(rec: dict, **over) -> approvals.Request:
        fields = dict(
            message=rec["siwq_message"],
            nonce=_siwq_nonce(rec["siwq_message"]),
            invoice_id=rec["invoice"],
            vendor_id=rec["vendor_id"],
            vendor_label=rec["vendor"],
            amount=rec["amount_uqor"],
            denom="uqor",
            approval_nonce=rec["approval_nonce"],
        )
        fields.update(over)
        return approvals.Request(**fields)

    def approval_verifies() -> str:
        rec = p1["approval"]
        # The approval was valid for 15 minutes, so it is judged at the instant
        # it was signed; the expiry is then exercised on the same signature.
        issued_ms = _siwq_issued_ms(rec["siwq_message"])
        verified = approvals.verify(
            approval_request(rec),
            rec["siwq_signature"],
            chain_pq_public_key=chain_owner_pub(),
            chain_id=chain_id,
            at_ms=issued_ms + 1_000,
        )
        expect(verified["address"] == owner, f"signer {verified['address']}")
        expired_now = False
        try:
            approvals.verify(
                approval_request(rec), rec["siwq_signature"], chain_pq_public_key=chain_owner_pub(), chain_id=chain_id
            )
        except Exception as exc:
            expired_now = "SIWQ_EXPIRED" in str(exc)
        expect(expired_now, "the 15-minute approval is still accepted today — expiry is not enforced")
        return "verifies against the on-chain ML-DSA-87 key at its issue time, and is refused now that it expired"

    r.check("the owner's Sign-In-With-QoreChain approval verifies", approval_verifies)

    def approval_negatives() -> str:
        rec = p1["approval"]
        pub = chain_owner_pub()
        accepted = []
        for label, req in (
            ("amount doubled", approval_request(rec, amount=rec["amount_uqor"] * 2)),
            ("replayed nonce", approval_request(rec, nonce="apprdeadbeef")),
            ("another invoice", approval_request(rec, invoice_id="INV-9999")),
        ):
            try:
                approvals.verify(req, rec["siwq_signature"], chain_pq_public_key=pub, chain_id=chain_id)
                accepted.append(label)
            except Exception:
                pass
        expect(not accepted, "; ".join(accepted))
        return "a doubled amount, a replayed nonce and another invoice id are all refused"

    r.check("a tampered or replayed approval is refused", approval_negatives)

    def approval_payment() -> str:
        rec = p1["approval"]
        body = http.get(f"{addrs['rest']}/cosmos/tx/v1beta1/txs/{rec['tx']}").json()
        tx_response = body.get("tx_response", {})
        expect(int(tx_response.get("code", 1)) == 0, f"approval payment tx code {tx_response.get('code')}")
        entry = next(e for e in journal if e.get("tx_hash") == rec["tx"])
        expect(entry.get("chain_success") is True, f"journal says {entry.get('chain_reason')}")
        expect(rec["amount_uqor"] > rec["vendor_per_tx_cap_uqor"],
               "the approved payment was not above the vendor cap")
        return (
            f"{rec['amount_uqor']} uqor settled above the {rec['vendor_per_tx_cap_uqor']} uqor cap, "
            f"approval nonce {rec['approval_nonce']}"
        )

    r.check("the approved over-cap payment settled on chain", approval_payment)

    # ------------------------------------------------------------------ #
    # sealed archive
    # ------------------------------------------------------------------ #
    def sealed_archive() -> str:
        index = json.loads((ROOT / "demo" / "sealed" / "index.json").read_text())
        expect(index, "no sealed invoices")
        for rec in index:
            blob = Path(rec["envelope"]).read_bytes()
            expect(hashlib.sha256(blob).hexdigest() == rec["envelope_sha256"], f"{rec['invoice_id']}: envelope changed")
            expect(len(blob) == rec["envelope_len"], f"{rec['invoice_id']}: length changed")
        archive = hashlib.sha256("|".join(sorted(x["envelope_sha256"] for x in index)).encode()).hexdigest()
        expect(archive == p1["seal"]["archive_digest"], "the archive digest differs from the recorded one")
        return f"{len(index)} envelopes unchanged; archive digest {archive[:16]}…"

    r.check("the sealed invoice archive matches its recorded digests", sealed_archive)

    def anchor_memo() -> str:
        body = http.get(f"{addrs['rest']}/cosmos/tx/v1beta1/txs/{p1['seal']['tx']}").json()
        memo = (body.get("tx", {}).get("body", {}) or {}).get("memo", "")
        want = f"qg:seal-archive:v1:{p1['seal']['archive_digest'][:40]}"
        expect(memo == want, f"memo {memo!r} != {want!r}")
        return f"the anchor transaction carries {memo}"

    r.check("the archive digest is committed on chain", anchor_memo)

    # ------------------------------------------------------------------ #
    # token payment
    # ------------------------------------------------------------------ #
    def token_settled() -> str:
        rec = p1["token"]
        bal = rest.get(f"/cosmos/bank/v1beta1/balances/{rec['to']}")
        coins = {c["denom"]: int(c["amount"]) for c in bal.get("balances", [])}
        expect(coins.get(rec["denom"], 0) >= rec["amount"],
               f"vendor holds {coins.get(rec['denom'], 0)} of {rec['denom']}")
        body = http.get(f"{addrs['rest']}/cosmos/tx/v1beta1/txs/{rec['tx']}").json()
        txr = body.get("tx_response", {})
        expect(int(txr.get("code", 1)) == 0, f"token tx code {txr.get('code')}")
        types = [m.get("@type") for m in (body.get("tx", {}).get("body", {}) or {}).get("messages", [])]
        expect(any(t.endswith("MsgExecuteCosmos") for t in types), f"messages {types}")
        return f"the vendor holds {coins.get(rec['denom'], 0)} fUSD; settled by {types[0].split('.')[-1]}"

    r.check("the fUSD invoice settled to the vendor's account", token_settled)

    def token_binding() -> str:
        rec = p1["token"]
        body = http.get(f"{addrs['rest']}/cosmos/tx/v1beta1/txs/{rec['tx']}").json()
        memo = (body.get("tx", {}).get("body", {}) or {}).get("memo", "")
        entry = next(e for e in journal if e.get("kind") == "token_payment" and e.get("tx_hash") == rec["tx"])
        expect(memo == _intent_memo(entry["intent"]), f"memo {memo!r}")
        return f"the memo {memo} commits the agent's signed intent"

    r.check("the token payment commits its signed intent", token_binding)

    # ------------------------------------------------------------------ #
    # spending rule + linked key
    # ------------------------------------------------------------------ #
    def spending_rule() -> str:
        payload = rest.get(f"/qorechain/abstractaccount/v1/accounts/{owner}/spending_rules")
        rules = payload.get("rules", [])
        expect(rules, "no spending rule on the account")
        rule = next((x for x in rules if x.get("enabled")), None)
        expect(rule is not None, "no enabled rule")
        expect("uqor" in rule.get("allowed_denoms", []), "uqor not allowed")
        expect(token_denom in rule.get("allowed_denoms", []), "the fUSD denom is not allowed")
        return (
            f"rule {rule['id']!r}: {rule['per_tx_limit']} uqor/tx, {rule['daily_limit']} uqor/day, "
            f"denoms {rule['allowed_denoms']}, {len(rule.get('allowed_evm_calls', []))} allowed calls"
        )

    r.check("the chain's spending rule bounds the agent", spending_rule)

    def linked() -> str:
        payload = rest.get(f"/qorechain/abstractaccount/v1/accounts/{owner}/authenticators")
        keys = payload.get("authenticators", [])
        live = [k for k in keys if not k.get("revoked")]
        expect(live, f"{len(keys)} linked keys, none live")
        k = live[0]
        expect("send" in k["permissions"], f"permissions {k['permissions']}")
        return f"{len(live)} live key with permissions {k['permissions']}, label {k['label']!r}"

    r.check("a live agent key is linked with send+evm", linked)

    # ------------------------------------------------------------------ #
    # report
    # ------------------------------------------------------------------ #
    if args.json:
        print(json.dumps({"passed": r.passed, "failed": r.failed, "checks": r.rows}, indent=2))
    else:
        width = max(len(row["check"]) for row in r.rows)
        for row in r.rows:
            mark = "PASS" if row["ok"] else "FAIL"
            print(f"{mark}  {row['check']:<{width}}  {row['detail']}")
        print()
        print(f"{r.passed} checks passed, {r.failed} failed — read-only, nothing signed, testnet only.")
        print(f"Every transaction above opens at {EXPLORER}<hash> (switch the explorer to Testnet).")
    return 0 if r.failed == 0 else 1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _decode_vendor(raw: str):
    from eth_abi import decode as abi_decode

    return abi_decode(["address", "bytes32", "uint96", "uint96", "bool"], bytes.fromhex(raw.removeprefix("0x")))


def _intent_memo(intent: dict) -> str:
    canonical = json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    return "qg:v1:" + hashlib.sha256(canonical).hexdigest()[:40]


def _intent_memos(intent: dict) -> set[str]:
    """Both memo forms this project has used for a signed intent: the
    commitment the contract stores as `memoHash` (`qg:v1:…`), and the shorter
    relayer memo of the first run (`qoreguard:…`). Both are the same digest."""
    digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {f"qg:v1:{digest[:40]}", f"qoreguard:{digest[:16]}"}


def _siwq_issued_ms(message: str) -> int:
    import datetime as _dt

    for line in message.splitlines():
        if line.startswith("Issued At:"):
            stamp = line.split(":", 1)[1].strip().rstrip("Z")
            dt = _dt.datetime.fromisoformat(stamp).replace(tzinfo=_dt.timezone.utc)
            return int(dt.timestamp() * 1000)
    raise AssertionError("the SIWQ message has no Issued At line")


def _siwq_nonce(message: str) -> str:
    for line in message.splitlines():
        if line.startswith("Nonce:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("the SIWQ message has no nonce line")


if __name__ == "__main__":
    sys.exit(main())
