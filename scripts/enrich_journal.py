#!/usr/bin/env python3
"""Re-read refused payments from the chain and replace the generic node message
with the contract's own error (e.g. `PerVendorTxCapExceeded(500000, 200000)`).

The journal keeps the node's message under `chain_reason_raw` for comparison.

    python scripts/enrich_journal.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from qoreguard import treasury as T  # noqa: E402
from qoreguard.chain import Chain  # noqa: E402
from qoreguard.config import settings  # noqa: E402
from qorsdk import decode_execution_results  # noqa: E402

JOURNAL = ROOT / "demo" / "journal.jsonl"


def main() -> int:
    cfg = settings()
    chain = Chain(cfg)
    entries = [json.loads(l) for l in JOURNAL.read_text().splitlines() if l.strip()]
    changed = 0
    for e in entries:
        if e.get("chain_success") is not False or not e.get("tx_hash"):
            continue
        try:
            body = chain.tx(e["tx_hash"])
            results = decode_execution_results(body)
        except Exception as exc:
            print(f"{e.get('invoice')}: could not read {e['tx_hash'][:12]}… ({type(exc).__name__})")
            continue
        for r in results:
            if r.success:
                continue
            decoded = T.decode_revert(getattr(r, "ret", b"") or b"")
            if decoded:
                e.setdefault("chain_reason_raw", e.get("chain_reason"))
                e["chain_reason"] = decoded
                changed += 1
                print(f"{e.get('invoice')}: {decoded}")
    JOURNAL.write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")
    print(f"updated {changed} entries in {JOURNAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
