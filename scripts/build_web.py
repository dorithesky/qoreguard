#!/usr/bin/env python3
"""Generate the two static pages a judge presses in a browser.

`web/lab.html` and `web/qorex.html` are single files with no server, no
package install and no key: everything they need is embedded at build time by
this script, from the same records `verify.py` checks.

    python scripts/build_web.py          # writes web/lab.html, web/qorex.html

The pages call the testnet's EVM lane directly. The EVM lane sends
`access-control-allow-origin: *`, so a static page may call it; the Cosmos REST
API does not, which is why the ML-DSA-87 key is read through the `pqcKeyStatus`
precompile (`0x…0a02`) on the EVM lane instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from eth_utils import keccak

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

FUNCTIONS = {
    "pay": ("pay(uint256,uint256,bytes32)", ["uint256", "uint256", "bytes32"], []),
    "payWithApproval": (
        "payWithApproval(uint256,uint256,bytes32,uint256,bytes,bytes)",
        ["uint256", "uint256", "bytes32", "uint256", "bytes", "bytes"],
        [],
    ),
    "setTreasuryCap": ("setTreasuryCap(uint96,uint256,bytes,bytes)", ["uint96", "uint256", "bytes", "bytes"], []),
    "setVendorActive": (
        "setVendorActive(uint256,bool,uint256,bytes,bytes)",
        ["uint256", "bool", "uint256", "bytes", "bytes"],
        [],
    ),
    "addVendor": (
        "addVendor(uint256,address,bytes32,uint96,uint96,uint256,bytes,bytes)",
        ["uint256", "address", "bytes32", "uint96", "uint96", "uint256", "bytes", "bytes"],
        [],
    ),
    "adminMessage": ("adminMessage(bytes32,bytes32,uint256)", ["bytes32", "bytes32", "uint256"], ["bytes"]),
    "exceptionMessage": (
        "exceptionMessage(uint256,uint256,bytes32,uint256)",
        ["uint256", "uint256", "bytes32", "uint256"],
        ["bytes"],
    ),
    "ownerAccount": ("ownerAccount()", [], ["address"]),
    "vendorCount": ("vendorCount()", [], ["uint256"]),
    "vendorIdAt": ("vendorIdAt(uint256)", ["uint256"], ["uint256"]),
    "vendor": ("vendor(uint256)", ["uint256"], ["address", "bytes32", "uint96", "uint96", "bool"]),
    "treasuryDailyCapUqor": ("treasuryDailyCapUqor()", [], ["uint96"]),
    "approvalNonce": ("approvalNonce()", [], ["uint256"]),
    "totalSpentUqor": ("totalSpentUqor()", [], ["uint256"]),
    "spentToday": ("spentToday(uint256)", ["uint256"], ["uint256"]),
    "treasurySpentToday": ("treasurySpentToday()", [], ["uint256"]),
    "paidInvoices": ("paidInvoices(bytes32)", ["bytes32"], ["bool"]),
    "settledInvoiceCount": ("settledInvoiceCount()", [], ["uint256"]),
    "pqcKeyStatus": (
        "pqcKeyStatus(address)",
        ["address"],
        ["bool", "uint8", "bytes", "bool"],
    ),
}

ACTIONS = {
    "ADD_VENDOR": "addVendor",
    "SET_VENDOR_ACTIVE": "setVendorActive",
    "SET_VENDOR_CAPS": "setVendorCaps",
    "SET_TREASURY_CAP": "setTreasuryCap",
    "PAY_WITH_APPROVAL": "payWithApproval",
}


def selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def error_table() -> dict[str, dict]:
    abi = json.loads(
        (ROOT / "contracts" / "out" / "QoreGuardTreasury.sol" / "QoreGuardTreasury.json").read_text()
    )["abi"]
    table = {}
    for entry in abi:
        if entry.get("type") != "error":
            continue
        types = [i["type"] for i in entry.get("inputs", [])]
        # every custom error in this contract takes static arguments only
        if any(t in {"bytes", "string"} or t.endswith("[]") for t in types):
            continue
        sig = f"{entry['name']}({','.join(types)})"
        table[selector(sig)] = {"name": entry["name"], "types": types, "signature": sig}
    return table


def build_data() -> dict:
    addrs = json.loads((ROOT / "demo" / "addresses.json").read_text())
    mandate = json.loads((ROOT / "demo" / "mandate.json").read_text())
    console = json.loads((ROOT / "demo" / "qorex-console.json").read_text())
    guard_path = ROOT / "demo" / "guard.json"
    guard = json.loads(guard_path.read_text()) if guard_path.exists() else {}

    #: One invoice the deployed contract has already settled. The lab's eighth
    #: experiment asks it to pay that invoice again: the chain must say no.
    settled = {}
    for p in (guard.get("pay", {}).get("payments") or []):
        if p.get("paid"):
            settled = {
                "invoice": p["invoice"],
                "vendor_id": 1,
                "amount_uqor": p["amount_uqor"],
                "key": p["invoice_key"],
                "key_hash": "0x" + keccak(text=p["invoice_key"]).hex(),
                "tx": p["tx"],
            }
            break

    data = {
        "chain": {
            "id": addrs["chain_id"],
            "rest": addrs["rest"],
            "evmRpc": addrs["evm_rpc"],
            "evmChainId": addrs["evm_chain_id"],
        },
        "contract": addrs["contract"],
        "owner": addrs["owner"],
        "relayer": addrs["relayer"],
        "explorer": "https://explore.qore.network",
        "pqcKeysPrecompile": "0x0000000000000000000000000000000000000a02",
        "pqcVerifyPrecompile": "0x0000000000000000000000000000000000000a01",
        "vendors": addrs["vendors"],
        "treasury_daily_cap_uqor": mandate["treasury_daily_cap_uqor"],
        "token": addrs["token"],
        "functions": {
            name: {
                "signature": sig,
                "selector": selector(sig),
                "types": types,
                "returns": returns,
            }
            for name, (sig, types, returns) in FUNCTIONS.items()
        },
        "actions": {name: selector(f"{name}()") for name in ACTIONS},
        "errors": error_table(),
        "settled_invoice": settled,
        "console": console,
        "approval": json.loads((ROOT / "demo" / "p1.json").read_text())["approval"],
        "pq_signature_len": 4627,
        "pq_key_len": 2592,
    }
    return data


def main() -> int:
    data = build_data()
    blob = json.dumps(data, separators=(",", ":"))
    if "</script" in blob:  # cannot happen with our data, but never inject markup
        raise SystemExit("embedded data would close the script tag")

    written = []
    for template, out in (("lab.template.html", "lab.html"), ("qorex.template.html", "qorex.html")):
        text = (WEB / template).read_text()
        if "__QOREGUARD_DATA__" not in text:
            raise SystemExit(f"{template} has no __QOREGUARD_DATA__ marker")
        (WEB / out).write_text(text.replace("__QOREGUARD_DATA__", blob))
        written.append(out)

    print(f"wrote {', '.join(written)} — {len(blob):,} bytes of embedded chain data")
    print(f"contract {data['contract']} · {len(data['functions'])} calls · {len(data['errors'])} error types")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
