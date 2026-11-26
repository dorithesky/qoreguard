"""The planning half of the agent: what to pay, and why — before any signing.

The planner is deterministic by default so a demo can be replayed exactly. An
optional LLM pass (`QORE_AGENT_LLM_URL`) can annotate decisions in natural
language, but it never widens a limit: the planner only ever proposes, and the
chain and the contract decide. That split is the point of the project — see
AI_USAGE.md.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Vendor:
    id: int
    label: str
    payee: str  # 0x address
    per_tx_cap_uqor: int
    daily_cap_uqor: int
    invoice_pubkey: str = ""  # ed25519 key the vendor signs invoices with
    note: str = ""


@dataclass
class Invoice:
    id: str
    vendor: str
    amount_uqor: int
    memo: str
    due: str = ""
    signer_pubkey: str = ""
    signature: str = ""
    tamper_note: str = ""


@dataclass
class Mandate:
    """The owner's written mandate, as a file the agent reads at start-up."""

    treasury_daily_cap_uqor: int
    agent_per_tx_cap_uqor: int
    agent_daily_cap_uqor: int
    agent_link_hours: int
    vendors: list[Vendor] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def load(cls, path: str) -> "Mandate":
        raw = json.loads(open(path).read())
        invoices = raw.get("invoices")
        if not invoices and raw.get("invoices_file"):
            from pathlib import Path as _Path

            base = _Path(path).resolve().parent.parent
            invoices = json.loads((base / raw["invoices_file"]).read_text())["invoices"]
        return cls(
            treasury_daily_cap_uqor=raw["treasury_daily_cap_uqor"],
            agent_per_tx_cap_uqor=raw["agent_per_tx_cap_uqor"],
            agent_daily_cap_uqor=raw["agent_daily_cap_uqor"],
            agent_link_hours=raw.get("agent_link_hours", 24),
            vendors=[Vendor(**v) for v in raw.get("vendors", [])],
            invoices=[Invoice(**i) for i in (invoices or [])],
            notes=raw.get("notes", ""),
        )

    def vendor(self, label: str) -> Vendor | None:
        for v in self.vendors:
            if v.label == label:
                return v
        return None


@dataclass
class PlanItem:
    invoice: Invoice
    vendor: Vendor | None
    action: str  # "pay" | "skip"
    reason: str


def invoice_message(inv: Invoice) -> bytes:
    """The exact bytes a vendor signs. Any change to the invoice changes it."""
    return "|".join(
        ["QoreGuard.invoice.v1", inv.id, inv.vendor, str(inv.amount_uqor), inv.memo, inv.due or ""]
    ).encode()


def verify_invoice(inv: Invoice, vendor: Vendor) -> bool:
    """True only when the vendor's registered invoice key signed this invoice."""
    if not vendor.invoice_pubkey or not inv.signature:
        return False
    if inv.signer_pubkey and inv.signer_pubkey != vendor.invoice_pubkey:
        return False
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(vendor.invoice_pubkey)).verify(
            bytes.fromhex(inv.signature), invoice_message(inv)
        )
        return True
    except (InvalidSignature, ValueError):
        return False


def plan(mandate: Mandate, already_paid: set[str] | None = None) -> list[PlanItem]:
    """Decide, invoice by invoice, what the agent should attempt.

    These are *proposals*: the hard limits live on chain. A local pre-check only
    keeps obviously doomed calls out of the log.
    """
    already_paid = already_paid or set()
    items: list[PlanItem] = []
    for inv in mandate.invoices:
        if inv.id in already_paid:
            items.append(PlanItem(inv, mandate.vendor(inv.vendor), "skip", "already paid"))
            continue
        vendor = mandate.vendor(inv.vendor)
        if vendor is None:
            items.append(PlanItem(inv, None, "skip", f"vendor {inv.vendor!r} is not in the mandate"))
            continue
        if inv.amount_uqor <= 0:
            items.append(PlanItem(inv, vendor, "skip", "non-positive amount"))
            continue
        if not verify_invoice(inv, vendor):
            items.append(
                PlanItem(
                    inv,
                    vendor,
                    "skip",
                    "invoice does not authenticate against the vendor's registered key "
                    "(tampered or unsigned) — nothing is sent to the chain",
                )
            )
            continue
        if inv.amount_uqor > mandate.agent_per_tx_cap_uqor:
            items.append(
                PlanItem(
                    inv,
                    vendor,
                    "pay",
                    f"{inv.amount_uqor} uqor is above the agent's {mandate.agent_per_tx_cap_uqor} "
                    "uqor per-call cap: attempt it so the refusal is on record",
                )
            )
            continue
        items.append(PlanItem(inv, vendor, "pay", "within the mandate"))
    return items


def annotate_with_llm(items: list[PlanItem], mandate: Mandate) -> list[PlanItem]:
    """Optional natural-language annotation, purely additive.

    Enabled only when QORE_AGENT_LLM_URL is set (an OpenAI-compatible chat
    endpoint). It never changes an action; it appends a sentence to the reason.
    """
    url = os.environ.get("QORE_AGENT_LLM_URL")
    if not url:
        return items
    import urllib.request

    payload = {
        "model": os.environ.get("QORE_AGENT_LLM_MODEL", "gpt-4o-mini"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the treasury agent of QoreGuard. For each invoice, write one short "
                    "sentence explaining the payment in business terms. Do not change any action."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mandate_notes": mandate.notes,
                        "invoices": [
                            {
                                "id": i.invoice.id,
                                "vendor": i.invoice.vendor,
                                "amount_uqor": i.invoice.amount_uqor,
                                "action": i.action,
                                "reason": i.reason,
                            }
                            for i in items
                        ],
                    }
                ),
            },
        ],
        "temperature": 0,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {os.environ.get('QORE_AGENT_LLM_KEY', '')}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body: Any = json.loads(resp.read())
        text = body["choices"][0]["message"]["content"]
        for item in items:
            item.reason = f"{item.reason} · {text[:200]}"
    except Exception as exc:  # the demo must not fail because a model is unreachable
        for item in items:
            item.reason = f"{item.reason} · (LLM unavailable: {type(exc).__name__})"
    return items
