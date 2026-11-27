#!/usr/bin/env python3
"""Mutation-test the treasury policy.

Each mutant is a small, semantically meaningful change to
`contracts/src/QoreGuardTreasury.sol` — a removed cap check, a loosened nonce,
a dropped ledger write. The suite must kill every one: if `forge test` still
passes, a test is missing (a *survived* mutant), and this script fails.

    python scripts/mutate.py              # every mutant, summary at the end
    python scripts/mutate.py --list       # names only
    python scripts/mutate.py --only M07   # one mutant

The contract is restored before exit, whatever happens (also on Ctrl-C).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "contracts" / "src" / "QoreGuardTreasury.sol"
BACKUP = SRC.with_suffix(".sol.mutation-orig")

# (id, what a survivor would mean, original text, mutated text)
MUTANTS: list[tuple[str, str, str, str]] = [
    (
        "M01",
        "the per-vendor per-tx cap is not enforced",
        "if (amountUqor > v.perTxCapUqor) revert PerVendorTxCapExceeded(amountUqor, v.perTxCapUqor);",
        "if (false) revert PerVendorTxCapExceeded(amountUqor, v.perTxCapUqor);",
    ),
    (
        "M02",
        "the per-vendor daily cap is not enforced",
        "if (vendorDayAfter > v.dailyCapUqor) revert PerVendorDayCapExceeded(vendorDayAfter, v.dailyCapUqor);",
        "if (false) revert PerVendorDayCapExceeded(vendorDayAfter, v.dailyCapUqor);",
    ),
    (
        "M03",
        "the treasury daily cap is not enforced on the agent lane",
        "if (treasuryDayAfter > treasuryDailyCapUqor) revert TreasuryDayCapExceeded(treasuryDayAfter, treasuryDailyCapUqor);\n\n        paidInvoices[invoiceKey] = true;",
        "if (false) revert TreasuryDayCapExceeded(treasuryDayAfter, treasuryDailyCapUqor);\n\n        paidInvoices[invoiceKey] = true;",
    ),
    (
        "M04",
        "paying exactly the vendor daily cap is refused (off-by-one)",
        "if (vendorDayAfter > v.dailyCapUqor)",
        "if (vendorDayAfter >= v.dailyCapUqor)",
    ),
    (
        "M05",
        "the treasury day ledger is never written",
        "spentOnDayUqor[day] = uint96(treasuryDayAfter);\n        totalSpentUqor += amountUqor;\n\n        emit VendorPaid(",
        "totalSpentUqor += amountUqor;\n\n        emit VendorPaid(",
    ),
    (
        "M06",
        "the per-vendor day ledger is never written",
        "spentOnDayUqor[_vendorDayKey(vendorId, day)] = uint96(vendorDayAfter);\n        spentOnDayUqor[day] = uint96(treasuryDayAfter);",
        "spentOnDayUqor[day] = uint96(treasuryDayAfter);",
    ),
    (
        "M07",
        "the all-time total does not grow on the agent lane",
        "spentOnDayUqor[day] = uint96(treasuryDayAfter);\n        totalSpentUqor += amountUqor;\n\n        emit VendorPaid(",
        "spentOnDayUqor[day] = uint96(treasuryDayAfter);\n        totalSpentUqor += 0;\n\n        emit VendorPaid(",
    ),
    (
        "M08",
        "an owner approval can be replayed (the nonce does not advance)",
        "approvalNonce = approvalNonce_ + 1;",
        "approvalNonce = approvalNonce_;",
    ),
    (
        "M09",
        "an old approval nonce is accepted",
        "if (approvalNonce_ != approvalNonce) revert StaleApprovalNonce(approvalNonce_, approvalNonce);",
        "if (approvalNonce_ > approvalNonce) revert StaleApprovalNonce(approvalNonce_, approvalNonce);",
    ),
    (
        "M10",
        "any PQ algorithm is accepted, not only ML-DSA-87",
        "if (algorithmId != 1) revert PqcKeyMismatch(); // 1 = ML-DSA-87",
        "if (algorithmId == 0) revert PqcKeyMismatch(); // 1 = ML-DSA-87",
    ),
    (
        "M11",
        "a registered key without proof of possession is accepted",
        "if (!proven) revert PqcKeyNotProven();",
        "if (false) revert PqcKeyNotProven();",
    ),
    (
        "M12",
        "a signature under a different registered key is accepted",
        "if (keccak256(registeredKey) != keccak256(pqcPubkey)) revert PqcKeyMismatch();",
        "if (false) revert PqcKeyMismatch();",
    ),
    (
        "M13",
        "an inactive vendor can still be paid",
        "if (!v.active) revert VendorInactive(vendorId);\n        if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
        "if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
    ),
    (
        "M14",
        "an unregistered vendor id can be paid",
        "if (v.payee == address(0)) revert UnknownVendor(vendorId);\n        if (!v.active) revert VendorInactive(vendorId);\n        if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
        "if (!v.active) revert VendorInactive(vendorId);\n        if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
    ),
    (
        "M15",
        "a zero-amount payment is accepted",
        "if (amountUqor == 0) revert ZeroAmount();\n        if (msg.value != amountUqor * WEI_PER_UQOR) revert ValueMismatch(msg.value, amountUqor * WEI_PER_UQOR);\n\n        Vendor storage v = _vendors[vendorId];\n        if (v.payee == address(0)) revert UnknownVendor(vendorId);\n        if (!v.active) revert VendorInactive(vendorId);\n        if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
        "if (msg.value != amountUqor * WEI_PER_UQOR) revert ValueMismatch(msg.value, amountUqor * WEI_PER_UQOR);\n\n        Vendor storage v = _vendors[vendorId];\n        if (v.payee == address(0)) revert UnknownVendor(vendorId);\n        if (!v.active) revert VendorInactive(vendorId);\n        if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
    ),
    (
        "M16",
        "msg.value may differ from the amount the memo names",
        "if (msg.value != amountUqor * WEI_PER_UQOR) revert ValueMismatch(msg.value, amountUqor * WEI_PER_UQOR);\n\n        Vendor storage v = _vendors[vendorId];\n        if (v.payee == address(0)) revert UnknownVendor(vendorId);\n        if (!v.active) revert VendorInactive(vendorId);\n        if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
        "if (false) revert ValueMismatch(msg.value, amountUqor * WEI_PER_UQOR);\n\n        Vendor storage v = _vendors[vendorId];\n        if (v.payee == address(0)) revert UnknownVendor(vendorId);\n        if (!v.active) revert VendorInactive(vendorId);\n        if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
    ),
    (
        "M17",
        "the treasury silently accepts plain transfers",
        "    receive() external payable {\n        revert ValueMismatch(msg.value, 0);\n    }",
        "    receive() external payable {}",
    ),
    (
        "M18",
        "the treasury cap does not bound an owner-approved exception",
        "// The treasury-wide cap still holds: an exception is a one-off payment,\n        // not a way to lift the budget the owner set for the whole day.\n        uint256 treasuryDayAfter = uint256(spentOnDayUqor[day]) + amountUqor;\n        if (treasuryDayAfter > treasuryDailyCapUqor) revert TreasuryDayCapExceeded(treasuryDayAfter, treasuryDailyCapUqor);",
        "// The treasury-wide cap still holds: an exception is a one-off payment,\n        // not a way to lift the budget the owner set for the whole day.\n        uint256 treasuryDayAfter = uint256(spentOnDayUqor[day]) + amountUqor;\n        if (false) revert TreasuryDayCapExceeded(treasuryDayAfter, treasuryDailyCapUqor);",
    ),
    (
        "M19",
        "an over-cap payment needs no owner signature at all",
        "        bytes32 payloadHash = keccak256(abi.encode(vendorId, amountUqor, invoiceKey));\n        _requireOwnerSignature(PAY_WITH_APPROVAL, payloadHash, approvalNonce_, pqcPubkey, pqcSignature);",
        "        bytes32 payloadHash = keccak256(abi.encode(vendorId, amountUqor, invoiceKey));\n        payloadHash;",
    ),
    (
        "M20",
        "the agent lane pays an invoice that is already settled",
        "if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
        "if (false) revert InvoiceAlreadyPaid(invoiceKey);\n        if (amountUqor > v.perTxCapUqor)",
    ),
    (
        "M21",
        "the approval lane pays an invoice that is already settled",
        "if (paidInvoices[invoiceKey]) revert InvoiceAlreadyPaid(invoiceKey);\n\n        bytes32 payloadHash = keccak256(abi.encode(vendorId, amountUqor, invoiceKey));",
        "if (false) revert InvoiceAlreadyPaid(invoiceKey);\n\n        bytes32 payloadHash = keccak256(abi.encode(vendorId, amountUqor, invoiceKey));",
    ),
    (
        "M22",
        "a settled invoice is recorded under another key, so it can be paid again",
        "paidInvoices[invoiceKey] = true;\n        settledInvoiceCount += 1;\n        spentOnDayUqor[_vendorDayKey(vendorId, day)]",
        "paidInvoices[keccak256(abi.encode(invoiceKey))] = true;\n        settledInvoiceCount += 1;\n        spentOnDayUqor[_vendorDayKey(vendorId, day)]",
    ),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT / "contracts", capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true", help="print one JSON line for scripts/record_tests.py")
    args = ap.parse_args()

    if args.list:
        for mid, meaning, _, _ in MUTANTS:
            print(f"{mid}  {meaning}")
        return 0

    forge = shutil.which("forge")
    if not forge:
        print("forge not found on PATH", file=sys.stderr)
        return 2

    original = SRC.read_text()
    BACKUP.write_text(original)
    selected = [m for m in MUTANTS if not args.only or m[0] == args.only]
    if not selected:
        print(f"no mutant {args.only}", file=sys.stderr)
        return 2

    results: list[tuple[str, str, str]] = []
    try:
        for mid, meaning, old, new in selected:
            count = original.count(old)
            if count != 1:
                results.append((mid, "BAD-PATTERN", f"matched {count} times: {meaning}"))
                continue
            SRC.write_text(original.replace(old, new, 1))
            proc = run([forge, "test", "--no-match-path", "test/Invariants.t.sol"])
            if proc.returncode == 0:
                status = "SURVIVED"
            elif "Compiler run failed" in proc.stdout + proc.stderr:
                status = "COMPILE-ERROR"
            else:
                import re

                failing = [
                    re.search(r"\]\s*([A-Za-z0-9_]+)\(", line).group(1)
                    for line in (proc.stdout + proc.stderr).splitlines()
                    if line.strip().startswith("[FAIL") and re.search(r"\]\s*([A-Za-z0-9_]+)\(", line)
                ]
                status = "killed"
                meaning = f"{meaning} — killed by {failing[0] if failing else 'a test'}"
            results.append((mid, status, meaning))
            if not args.json:
                print(f"{mid}  {status.upper():<13} {meaning}")
    finally:
        SRC.write_text(original)
        BACKUP.unlink(missing_ok=True)
        # Recompile the restored source: the last mutant left its own artifact
        # in contracts/out, and verify.py compares the deployed bytecode to it.
        run([forge, "build"])

    killed = sum(1 for _, s, _ in results if s == "killed")
    if args.json:
        print(json.dumps([{"id": m, "status": s, "meaning": d} for m, s, d in results]))
        return 0 if killed == len(results) else 1
    print()
    print(f"{killed}/{len(results)} mutants killed by the Foundry suite.")
    print("Survived mutants are gaps in the tests; the contract itself is unchanged.")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
