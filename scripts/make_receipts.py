#!/usr/bin/env python3
"""Write one receipt per settled invoice, in the invoice's own currency.

A receipt is the document a vendor could hold: what the invoice said, what the
chain actually paid, and whether the two agree. It is derived from
`demo/invoices.json` (the vendor-signed invoice) and `demo/journal.jsonl` (the
payment the agent made), so it can never say something the chain did not.

    python scripts/make_receipts.py

Output:
  * `demo/receipts.json`      — every receipt, for the dashboard and verify.py
  * `demo/receipts/<ID>.txt`  — the same receipt as a line of text
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from qoreguard.treasury import invoice_key  # noqa: E402

INVOICES = ROOT / "demo" / "invoices.json"
JOURNAL = ROOT / "demo" / "journal.jsonl"
OUT = ROOT / "demo" / "receipts.json"
OUT_DIR = ROOT / "demo" / "receipts"

#: The token denom the demo settles in. The symbol is what a human reads; the
#: denom is what the bank keeps.
TOKEN_SYMBOL = "fUSD"


def currency(denom: str | None) -> str:
    return TOKEN_SYMBOL if denom else "QOR"


def amount_label(amount: int, denom: str | None) -> str:
    """The same amount the invoice and the dashboard show, in the same unit.

    A native invoice is written in uqor but read in QOR (150000 uqor = 0.150
    QOR); a token invoice is written and read in whole fUSD. Converting a token
    invoice into QOR is the bug this receipt must never repeat.
    """
    if denom:
        return f"{amount} {TOKEN_SYMBOL}"
    return f"{amount / 1_000_000:.3f} QOR"


def main() -> int:
    invoices = {i["id"]: i for i in json.loads(INVOICES.read_text())["invoices"]}
    journal = [json.loads(l) for l in JOURNAL.read_text().splitlines() if l.strip()]

    receipts: list[dict] = []
    for entry in journal:
        if not entry.get("chain_success") or entry.get("kind") not in {"payment", "token_payment"}:
            continue
        invoice = invoices.get(entry.get("invoice"))
        if invoice is None:
            continue
        token = entry.get("kind") == "token_payment"
        invoice_denom = invoice.get("denom") or ""
        paid_denom = entry.get("denom") or ""
        invoice_amount = int(invoice["amount_uqor"])
        paid_amount = int(entry.get("amount", entry.get("amount_uqor", 0)))
        same_currency = (currency(invoice_denom) == currency(paid_denom)) and (
            (invoice_denom or "") == (paid_denom or "")
        )
        vendor_id = int(entry.get("vendor_id", 0))
        receipt = {
            "receipt": f"QoreGuard receipt {invoice['id']}",
            "invoice": invoice["id"],
            "vendor": invoice["vendor"],
            "vendor_id": vendor_id,
            "invoice_amount": invoice_amount,
            "invoice_denom": invoice_denom or "uqor",
            "invoice_currency": currency(invoice_denom),
            "paid_amount": paid_amount,
            "paid_denom": paid_denom or "uqor",
            "paid_currency": currency(paid_denom),
            "currency": currency(invoice_denom),
            "amount_label": amount_label(invoice_amount, invoice_denom),
            "lane": "native token" if token else "treasury contract",
            "tx": entry.get("tx_hash"),
            "settled_at": entry.get("logged_at"),
            "contract": entry.get("contract"),
            "invoice_key": invoice_key(invoice["id"], vendor_id),
            "payment_intent": entry.get("onchain_memo"),
            "matches_invoice": bool(invoice_amount == paid_amount and same_currency),
        }
        receipts.append(receipt)

    receipts.sort(key=lambda r: (r["settled_at"] or "", r["invoice"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "receipts": receipts}, indent=2) + "\n")

    for r in receipts:
        (OUT_DIR / f"{r['invoice']}.txt").write_text(
            f"{r['receipt']}\n"
            f"vendor       {r['vendor']}\n"
            f"invoice      {r['amount_label']} "
            f"({r['invoice_amount']} {r['invoice_denom']})\n"
            f"paid         {r['amount_label']} "
            f"({r['paid_amount']} {r['paid_denom']})\n"

            f"lane         {r['lane']}\n"
            f"tx           {r['tx']}\n"
            f"agrees       {r['matches_invoice']}\n"
        )

    mismatched = [r["invoice"] for r in receipts if not r["matches_invoice"]]
    for r in receipts:
        mark = "OK  " if r["matches_invoice"] else "BAD "
        print(f"{mark} {r['invoice']:<10} invoice {r['amount_label']:<11} paid {r['amount_label']:<11} "
              f"{r['lane']:<17} {str(r['tx'])[:14]}…")
    print(f"\n{len(receipts)} receipts in {OUT.relative_to(ROOT)}")
    if mismatched:
        print(f"receipts that do not match their invoice: {mismatched}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
