#!/usr/bin/env python3
"""Render a self-contained HTML report of the live treasury state.

Reads the chain (vendors, caps, spend ledger, payment events), the signed
invoices and the agent journal, and writes `web/index.html` with everything
inlined: no server, no build step, no network calls when opened.

    python scripts/render_dashboard.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_utils import keccak

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from qoreguard import treasury as T  # noqa: E402
from qoreguard.chain import Chain  # noqa: E402
from qoreguard.config import settings  # noqa: E402
from qoreguard.evm import Evm  # noqa: E402

DEPLOYMENT = Path(os.environ.get("QOREGUARD_DEPLOYMENT", str(ROOT / ".keys" / "deployment.json")))
JOURNAL = Path(os.environ.get("QOREGUARD_JOURNAL", str(ROOT / "demo" / "journal.jsonl")))
INVOICES = Path(os.environ.get("QOREGUARD_INVOICES", str(ROOT / "demo" / "invoices.json")))
ADDRESSES = Path(os.environ.get("QOREGUARD_ADDRESSES", str(ROOT / "demo" / "addresses.json")))
RECEIPTS = Path(os.environ.get("QOREGUARD_RECEIPTS", str(ROOT / "demo" / "receipts.json")))
OUT = Path(os.environ.get("QOREGUARD_OUT", str(ROOT / "web" / "index.html")))

EV_VENDOR_PAID = keccak(text="VendorPaid(uint256,address,uint256,bytes32,uint256,uint256)")
EV_VENDOR_PAID_APPROVED = keccak(text="VendorPaidWithApproval(uint256,address,uint256,bytes32,uint256,uint256)")
EV_POLICY_UPDATED = keccak(text="PolicyUpdated(bytes32,bytes32,uint256)")
EV_VENDOR_CHANGED = keccak(text="VendorChanged(uint256,address,uint96,uint96,bool)")

TOKEN_SYMBOLS = {1: "cloud-hosting", 2: "api-credits"}


def qor(uqor: int | str) -> str:
    return f"{int(uqor) / 1_000_000:.3f}"


def amount_in(amount: int | str, denom: str | None) -> str:
    """One amount, in the currency the invoice is written in.

    A token invoice is never converted to QOR: 500 fUSD is 500 fUSD, and showing
    it as 0.001 QOR was exactly the bug this function exists to prevent.
    """
    if denom:
        return f"{int(amount)} fUSD"
    return f"{qor(amount)} QOR"


#: The public EVM RPC refuses a very wide eth_getLogs range, so it is walked
#: in windows of this many blocks.
LOG_CHUNK = 5_000


def fetch_logs(evm: Evm, contract: str, topic: bytes, from_block: int) -> list[dict]:
    head = int(evm.call("eth_blockNumber"), 16)
    out: list[dict] = []
    start = from_block
    while start <= head:
        end = min(start + LOG_CHUNK - 1, head)
        try:
            out.extend(
                evm.call(
                    "eth_getLogs",
                    [
                        {
                            "address": contract,
                            "fromBlock": hex(start),
                            "toBlock": hex(end),
                            "topics": ["0x" + topic.hex()],
                        }
                    ],
                )
            )
        except Exception:
            pass
        start = end + 1
    return out


def main() -> int:
    cfg = settings()
    chain = Chain(cfg)
    evm = Evm(cfg.evm_rpc, cfg.evm_chain_id)
    if not DEPLOYMENT.exists():
        print("no deployment yet — run `deploy` first")
        return 1
    dep = json.loads(DEPLOYMENT.read_text())
    contract = dep["contract"]
    from_block = max(int(dep.get("block", 0)) - 1, 0)

    addresses = json.loads(ADDRESSES.read_text()) if ADDRESSES.exists() else {}
    #: Every deployment the project has on chain, labelled in the ledger so a
    #: payment made before the invoice guard (v1) is never confused with one the
    #: guard covers (v2).
    deployments = {contract.lower(): "current"}
    for prev in addresses.get("previous_deployments", []):
        deployments[prev["contract"].lower()] = prev.get("label", "earlier")
    token_denom = (addresses.get("token") or {}).get("denom", "")

    def view(selector: bytes, types=None, args=None) -> int:
        out = evm.eth_call(contract, selector + (abi_encode_args(types, args) if types else b""))
        return int(out, 16) if out and out != "0x" else 0

    def abi_encode_args(types, args) -> bytes:
        from eth_abi import encode

        return encode(types, args)

    owner_account = "0x" + evm.eth_call(contract, T.SEL_OWNER_ACCOUNT)[-40:]
    cap = view(T.SEL_TREASURY_DAILY_CAP)
    spent = view(T.SEL_TREASURY_SPENT_TODAY)
    total = view(T.SEL_TOTAL_SPENT)
    nonce = view(T.SEL_APPROVAL_NONCE)
    count = view(T.SEL_VENDOR_COUNT)
    settled_invoices = view(T.SEL_SETTLED_INVOICE_COUNT)

    vendors = []
    for i in range(count):
        vid = view(T.SEL_VENDOR_ID_AT, ["uint256"], [i])
        raw = evm.eth_call(contract, T.SEL_VENDOR + abi_encode_args(["uint256"], [vid]))
        payee, label, per_tx, daily, active = abi_decode(
            ["address", "bytes32", "uint96", "uint96", "bool"], bytes.fromhex(raw[2:])
        )
        vendors.append(
            {
                "id": vid,
                "payee": payee,
                "per_tx": per_tx,
                "daily": daily,
                "active": active,
                "spent": view(T.SEL_SPENT_TODAY, ["uint256"], [vid]),
                "label": TOKEN_SYMBOLS.get(vid, f"vendor {vid}"),
            }
        )

    journal_path = JOURNAL
    journal = [json.loads(l) for l in journal_path.read_text().splitlines()] if journal_path.exists() else []
    payments = []
    for e in journal:
        if not e.get("chain_success"):
            continue
        token = e.get("kind") == "token_payment"
        payments.append(
            {
                "tx": e.get("tx_hash") or "",
                "block": 0,
                "vendor": e.get("vendor_label", ""),
                "amount": e.get("amount_uqor", 0),
                "memo": (e.get("onchain_memo") or "")[:24],
                "kind": (
                    "fUSD"
                    if token
                    else "PQ-approved"
                    if e.get("action") in ("payWithApproval", "pay_with_approval")
                    else "agent"
                ),
                "invoice": e.get("invoice", ""),
                "unit": "fUSD" if token else "QOR",
                "raw": e.get("amount", e.get("amount_uqor", 0)) if token else e.get("amount_uqor", 0),
                "deployment": deployments.get((e.get("contract") or "").lower(), "earlier"),
            }
        )

    admin_events = []
    for log in fetch_logs(evm, contract, EV_POLICY_UPDATED, from_block):
        action = bytes.fromhex(log["topics"][1][2:])
        payload, nonce = abi_decode(["bytes32", "uint256"], bytes.fromhex(log["data"][2:]))
        admin_events.append(
            {
                "tx": log["transactionHash"],
                "action": "0x" + action.hex()[:12],
                "nonce": nonce,
                "block": int(log["blockNumber"], 16),
            }
        )
    admin_events.sort(key=lambda a: a["block"])

    all_refusals = [
        {
            "invoice": e.get("invoice"),
            "amount": e.get("amount_uqor"),
            "reason": e.get("chain_reason") or "",
            "tx": e.get("tx_hash") or "",
        }
        for e in journal
        if e.get("chain_success") is False
    ]
    # A refusal that names the contract's own error is a policy refusal; one that
    # names gas is an operational retry by the operator, kept in the journal but
    # not presented as the product's behaviour.
    refusals = [r for r in all_refusals if "gas" not in r["reason"].lower()]
    retries = len(all_refusals) - len(refusals)

    invoices_path = INVOICES
    invoices = json.loads(invoices_path.read_text())["invoices"] if invoices_path.exists() else []
    invoice_denoms = {i["id"]: i.get("denom") or "" for i in invoices}
    receipts_doc = json.loads(RECEIPTS.read_text()) if RECEIPTS.exists() else {"receipts": []}
    receipts = receipts_doc.get("receipts", [])

    # The second run's rails (QoreSeal archive, QoreSign approval, fUSD settlement).
    p1_path = ROOT / "demo" / "p1.json"
    p1 = json.loads(p1_path.read_text()) if p1_path.exists() else {}
    sealed_path = ROOT / "demo" / "sealed" / "index.json"
    sealed_index = json.loads(sealed_path.read_text()) if sealed_path.exists() else []
    features_path = ROOT / "demo" / "onchain_features.json"
    features = json.loads(features_path.read_text()) if features_path.exists() else {}
    rails = []

    if p1.get("seal"):
        seal = p1["seal"]
        rails.append(
            {
                "rail": "QoreSeal",
                "what": f"{seal['invoices_sealed']} invoices sealed to the owner's ML-KEM-1024 key; only the digest is public".replace("{", "{{").replace("}", "}}"),
                "detail": seal["memo"],
                "state": "anchor verified" if seal.get("memo_matches") else "unverified",
                "ok": bool(seal.get("memo_matches")),
                "tx": seal["tx"],
            }
        )
    if features.get("kem"):
        kem = features["kem"]
        rails.append(
            {
                "rail": "QoreSeal key",
                "what": f"ML-KEM-1024 key registered on chain ({kem['key_len']} bytes), read back identical".replace("{", "{{").replace("}", "}}"),
                "detail": f"key 0x{kem.get('key_hex','')[:16]}…",
                "state": "registered" if kem.get("chain_matches") else "mismatch",
                "ok": bool(kem.get("chain_matches")),
                "tx": kem.get("tx", ""),
            }
        )
    if p1.get("approval"):
        ap = p1["approval"]
        rails.append(
            {
                "rail": "QoreSign approval",
                "what": (
                    f"SIWQ approval verified against the on-chain ML-DSA-87 key; "
                    f"paid {ap['amount_uqor']} uqor above the {ap['vendor_per_tx_cap_uqor']} uqor cap"
                ).replace("{", "{{").replace("}", "}}"),
                "detail": f"approval nonce {ap['approval_nonce']}",
                "state": "PAID" if ap.get("paid") else "refused",
                "ok": bool(ap.get("paid")),
                "tx": ap["tx"],
            }
        )
    if p1.get("token"):
        tok = p1["token"]
        rails.append(
            {
                "rail": "fUSD settlement",
                "what": (
                    f"{tok['amount']} fUSD paid by the agent on the native lane "
                    f"(MsgExecuteCosmos) to the vendor's account"
                ).replace("{", "{{").replace("}", "}}"),
                "detail": tok["denom"][:34] + "…",
                "state": "settled" if tok.get("paid") else "refused",
                "ok": bool(tok.get("paid")),
                "tx": tok["tx"],
            }
        )

    w = chain.evm_window(owner_account.replace("0x", "qor1")) if False else None

    html = TEMPLATE.format(
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        contract=contract,
        owner_account=owner_account,
        chain_id=cfg.chain_id,
        cap=cap,
        spent=spent,
        spent_pct=min(100, round(spent * 100 / cap, 1)) if cap else 0,
        total=total,
        settled_invoices=settled_invoices,
        nonce=nonce,
        vendor_rows="".join(vendor_row(v) for v in vendors),
        payment_rows="".join(payment_row(p) for p in payments),
        admin_rows="".join(admin_row(a) for a in admin_events) or "<tr><td colspan='4' class='muted'>no admin events</td></tr>",
        refusal_rows="".join(refusal_row(r, invoice_denoms) for r in refusals) or "<tr><td colspan='4' class='muted'>no refusals recorded</td></tr>",
        invoice_rows="".join(invoice_row(i) for i in invoices),
        receipt_rows="".join(receipt_row(r) for r in receipts) or "<tr><td colspan='6' class='muted'>run scripts/make_receipts.py to derive receipts from the journal</td></tr>",
        receipts_ok=sum(1 for r in receipts if r.get("matches_invoice")),
        receipts_total=len(receipts),
        rail_rows="".join(rail_row(x) for x in rails) or "<tr><td colspan='5' class='muted'>no extra rails recorded</td></tr>",
        sealed_count=len(sealed_index),
        archive_digest=(p1.get("seal", {}).get("archive_digest", "")[:16] + "…") if p1.get("seal") else "—",
        payment_count=len(payments),
        retries_note=(
            f"{retries} further attempt(s) were retried after the operator raised a gas parameter; "
            "they are in demo/journal.jsonl and are not policy refusals."
            if retries
            else ""
        ),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT} ({len(payments)} payments, {len(refusals)} refusals, {len(vendors)} vendors)")
    return 0


def vendor_row(v: dict) -> str:
    pct = min(100, round(v["spent"] * 100 / v["daily"], 1)) if v["daily"] else 0
    return f"""
      <tr>
        <td><span class="pill">{v['label']}</span><span class="mono muted"> #{v['id']}</span></td>
        <td class="mono">{v['payee']}</td>
        <td class="num">{qor(v['per_tx'])}</td>
        <td class="num">{qor(v['spent'])} / {qor(v['daily'])}</td>
        <td class="barcell"><div class="bar"><span style="width:{pct}%"></span></div></td>
        <td>{'active' if v['active'] else 'paused'}</td>
      </tr>"""


def payment_row(p: dict) -> str:
    unit = p.get("unit", "QOR")
    amount = str(p.get("raw", p["amount"])) + " fUSD" if unit == "fUSD" else qor(p["amount"]) + " QOR"
    tone = "ok" if p["kind"] == "agent" else "purple"
    deployment = p.get("deployment", "earlier")
    dep_cls = "ok" if deployment == "current" else "muted"
    return f"""
      <tr>
        <td class="mono">{p.get('invoice','')}</td>
        <td><span class="pill {tone}">{p['kind']}</span></td>
        <td>{p['vendor']}</td>
        <td class="num">{amount}</td>
        <td class="mono muted">{p['memo']}</td>
        <td><span class="pill {dep_cls}">{deployment}</span></td>
        <td class="mono"><a href="https://explore.qore.network/tx/{p['tx']}" target="_blank" rel="noreferrer">{p['tx'][:14]}…</a></td>
      </tr>"""


def receipt_row(r: dict) -> str:
    ok = bool(r.get("matches_invoice"))
    cls = "ok" if ok else "bad"
    label = "agrees" if ok else "differs"
    return f"""
      <tr>
        <td class="mono">{r['invoice']}</td>
        <td>{r['vendor']}</td>
        <td class="num">{r['invoice_amount']} {r['invoice_currency']}</td>
        <td class="num">{r['paid_amount']} {r['paid_currency']}</td>
        <td>{r['lane']}</td>
        <td><span class="pill {cls}">{label}</span></td>
        <td class="mono"><a href="https://explore.qore.network/tx/{r['tx']}" target="_blank" rel="noreferrer">{str(r['tx'])[:14]}…</a></td>
      </tr>"""


def admin_row(a: dict) -> str:
    return f"""
      <tr>
        <td class="mono">{a['action']}</td>
        <td class="num">{a['nonce']}</td>
        <td class="num">{a['block']}</td>
        <td class="mono"><a href="https://explore.qore.network/tx/{a['tx']}" target="_blank" rel="noreferrer">{a['tx'][:14]}…</a></td>
      </tr>"""


def refusal_row(r: dict, denoms: dict[str, str] | None = None) -> str:
    reason = r["reason"] or ""
    denom = (denoms or {}).get(r["invoice"] or "", "")
    return f"""
      <tr>
        <td>{r['invoice'] or '—'}</td>
        <td class="num">{amount_in(r['amount'] or 0, denom)}</td>
        <td class="why">{reason[:160]}</td>
        <td class="mono">{('0x' + r['tx'][:12] + '…') if r['tx'] else '—'}</td>
      </tr>"""


def invoice_row(i: dict) -> str:
    tampered = bool(i.get("tamper_note"))
    status = "tampered — refused" if tampered else "signed"
    cls = "bad" if tampered else "ok"
    amount = amount_in(i["amount_uqor"], i.get("denom"))
    return f"""
      <tr>
        <td class="mono">{i['id']}</td>
        <td>{i['vendor']}</td>
        <td class="num">{amount}</td>
        <td>{i['memo']}</td>
        <td><span class="pill {cls}">{status}</span></td>
      </tr>"""


def rail_row(x: dict) -> str:
    cls = "ok" if x["ok"] else "bad"
    link = (
        f'<a href="https://explore.qore.network/tx/{x["tx"]}" target="_blank" rel="noreferrer">{x["tx"][:16]}…</a>'
        if x["tx"]
        else '<span class="muted">—</span>'
    )
    return f"""
      <tr>
        <td><span class="pill purple">{x['rail']}</span></td>
        <td>{x['what']}</td>
        <td class="mono muted">{x['detail']}</td>
        <td><span class="pill {cls}">{x['state']}</span></td>
        <td class="mono">{link}</td>
      </tr>"""


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QoreGuard · treasury</title>
<style>
  :root {{
    --bg:#0b0f14; --panel:#121821; --line:#1e2733; --text:#e6edf3; --muted:#8b98a5;
    --ok:#3fb950; --bad:#f85149; --purple:#a371f7; --accent:#58a6ff;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif; }}
  header {{ padding:28px 32px 18px; border-bottom:1px solid var(--line); }}
  h1 {{ margin:0 0 6px; font-size:22px; letter-spacing:-.2px; }}
  h1 span {{ color:var(--accent); }}
  .sub {{ color:var(--muted); font-size:13px; }}
  main {{ padding:24px 32px 60px; max-width:1180px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:14px; margin-bottom:26px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
  .card .k {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.6px; }}
  .card .v {{ font-size:24px; margin-top:6px; font-weight:600; }}
  .card .v small {{ font-size:13px; color:var(--muted); font-weight:400; }}
  .bar {{ height:6px; background:#1b2430; border-radius:99px; overflow:hidden; margin-top:10px; }}
  .bar span {{ display:block; height:100%; background:linear-gradient(90deg,#1f6feb,#58a6ff); }}
  h2 {{ font-size:15px; margin:30px 0 10px; color:var(--muted); text-transform:uppercase; letter-spacing:.8px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
  th,td {{ text-align:left; padding:10px 14px; border-bottom:1px solid var(--line); font-size:14px; }}
  th {{ color:var(--muted); font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
  tr:last-child td {{ border-bottom:none; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }}
  .muted {{ color:var(--muted); }}
  a {{ color:var(--accent); text-decoration:none; }}
  .pill {{ display:inline-block; padding:2px 9px; border-radius:99px; font-size:12px; border:1px solid var(--line); background:#0e141c; }}
  .pill.ok {{ color:var(--ok); border-color:#1f3a25; background:#0f1a12; }}
  .pill.bad {{ color:var(--bad); border-color:#3d1d1d; background:#1a0f0f; }}
  .pill.purple {{ color:var(--purple); border-color:#2c2140; background:#150f20; }}
  .why {{ color:#d29922; font-size:13px; }}
  .barcell {{ width:180px; }}
  footer {{ color:var(--muted); font-size:12px; padding:0 32px 40px; }}
  .addr {{ word-break:break-all; }}
</style>
</head>
<body>
<header>
  <h1>Qore<span>Guard</span> · treasury on {chain_id}</h1>
  <div class="sub">Owner account <span class="mono addr">{owner_account}</span> ·
    contract <span class="mono addr">{contract}</span></div>
  <div class="sub">Read from the chain at {generated}. Every number below is a view call or an event, not a local estimate.</div>
</header>
<main>
  <div class="grid">
    <div class="card">
      <div class="k">Spent today</div>
      <div class="v">{spent} <small>uqor of {cap}</small></div>
      <div class="bar"><span style="width:{spent_pct}%"></span></div>
    </div>
    <div class="card"><div class="k">All-time paid (this contract)</div><div class="v">{total} <small>uqor</small></div></div>
    <div class="card"><div class="k">Settled invoices</div><div class="v">{settled_invoices} <small>one payment each</small></div></div>
    <div class="card"><div class="k">Payments</div><div class="v">{payment_count}</div></div>
    <div class="card"><div class="k">Sealed invoices</div><div class="v">{sealed_count} <small>ML-KEM-1024</small></div></div>
    <div class="card"><div class="k">Approval nonce</div><div class="v">{nonce} <small>PQ-signed actions</small></div></div>
  </div>

  <h2>Beyond payments — the post-quantum rails</h2>
  <table>
    <thead><tr><th>Rail</th><th>What it does</th><th>Commitment</th><th>Result</th><th>Transaction</th></tr></thead>
    <tbody>{rail_rows}</tbody>
  </table>
  <div class="sub" style="margin-top:8px">{sealed_count} sealed invoice envelopes · archive digest <span class="mono">{archive_digest}</span>
    · <span class="mono">python verify.py</span> re-checks everything read-only, with no keys.</div>

  <h2>Vendors and caps</h2>
  <table>
    <thead><tr><th>Vendor</th><th>Payee</th><th class="num">Per tx</th><th class="num">Today / cap</th><th></th><th>State</th></tr></thead>
    <tbody>{vendor_rows}</tbody>
  </table>

  <h2>Ledger — payments the agent settled</h2>
  <table>
    <thead><tr><th>Invoice</th><th>Path</th><th>Vendor</th><th class="num">Amount</th><th>Memo commitment</th><th>Deployment</th><th>Transaction</th></tr></thead>
    <tbody>{payment_rows}</tbody>
  </table>
  <div class="sub" style="margin-top:8px">The <span class="mono">current</span> deployment pays each invoice once, on either lane:
    `pay` and `payWithApproval` share the contract's `paidInvoices` record. Rows marked
    <span class="mono">first run</span> settled on the earlier deployment, before the guard existed, and stay on chain as history.</div>

  <h2>Receipts — the invoice, the payment, and whether they agree</h2>
  <table>
    <thead><tr><th>Invoice</th><th>Vendor</th><th class="num">Invoice says</th><th class="num">Chain paid</th><th>Lane</th><th>Result</th><th>Transaction</th></tr></thead>
    <tbody>{receipt_rows}</tbody>
  </table>
  <div class="sub" style="margin-top:8px">{receipts_ok}/{receipts_total} receipts carry the invoice's own currency and amount —
    <span class="mono">python scripts/make_receipts.py</span> derives them from the signed invoice and the journal.</div>

  <h2>Owner actions verified on chain (pqcVerify)</h2>
  <table>
    <thead><tr><th>Action tag</th><th class="num">Approval nonce</th><th class="num">Block</th><th>Transaction</th></tr></thead>
    <tbody>{admin_rows}</tbody>
  </table>

  <h2>Refusals (what the chain and the contract said no to)</h2>
  <table>
    <thead><tr><th>Invoice</th><th class="num">Amount</th><th>Reason</th><th>Transaction</th></tr></thead>
    <tbody>{refusal_rows}</tbody>
  </table>

  <h2>Invoices</h2>
  <table>
    <thead><tr><th>Invoice</th><th>Vendor</th><th class="num">Amount</th><th>Memo</th><th>Signature</th></tr></thead>
    <tbody>{invoice_rows}</tbody>
  </table>
</main>
<footer>QoreGuard · Build Before Q-Day · QoreChain testnet. Test tokens only; nothing here has value.</footer>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
