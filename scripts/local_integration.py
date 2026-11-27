#!/usr/bin/env python3
"""Local end-to-end check of the Python ↔ Solidity contract integration.

Runs against anvil (chain id 9800, same as the testnet EVM lane) with the two
QoreChain precompiles replaced by their mocks, so the pieces that must agree
byte-for-byte — selectors, ABI layout, the ML-DSA-87 admin message, the approval
nonce, the caps — are exercised before a single testnet transaction.

    ./scripts/local_integration.sh

This is not a substitute for the testnet run; it is the cheap check that comes
first. The owner's "signature" here is the mock's `0x01 || message`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from qoreguard import treasury as T  # noqa: E402
from qoreguard.evm import Evm, uqor_to_wei  # noqa: E402

OWNER_KEY = bytes.fromhex("ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
VENDOR_KEY = bytes.fromhex("59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
VENDOR2_KEY = bytes.fromhex("5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a")
PQC_VERIFY = "0x0000000000000000000000000000000000000a01"
PQC_KEYS = "0x0000000000000000000000000000000000000a02"


def mock_sign(message: bytes) -> bytes:
    """What MockPQCVerify accepts: 0x01 || message."""
    return b"\x01" + message


class MockPQC:
    def __init__(self, evm: Evm) -> None:
        self.evm = evm

    def set_key(self, call_data: bytes) -> str:
        return self.evm.send(OWNER_KEY, to=PQC_KEYS, data=call_data, gas=200_000)

    def encode_set_key(self, account: str, algorithm_id: int, pubkey: bytes, proven: bool) -> bytes:
        from eth_utils import keccak

        selector = keccak(text="setKey(address,uint8,bytes,bool)")[:4]
        return selector + abi_encode(["address", "uint8", "bytes", "bool"], [account, algorithm_id, pubkey, proven])


def main() -> int:
    evm = Evm("http://127.0.0.1:8545", 9800)
    if evm.chain_id_on_chain() != 9800:
        raise SystemExit("anvil is not running with --chain-id 9800")

    from eth_account import Account

    owner = Account.from_key(OWNER_KEY).address
    vendor = Account.from_key(VENDOR_KEY).address
    vendor2 = Account.from_key(VENDOR2_KEY).address

    # 1. deploy the mocks and etch them at the real precompile addresses
    verify_tx = evm.deploy(OWNER_KEY, str(ROOT / "contracts/out/MockPQC.sol/MockPQCVerify.json"), [], [], gas=500_000)
    verify_addr = evm.wait(verify_tx).contract_address
    keys_tx = evm.deploy(OWNER_KEY, str(ROOT / "contracts/out/MockPQC.sol/MockPQCKeys.json"), [], [], gas=500_000)
    keys_addr = evm.wait(keys_tx).contract_address
    evm.call("anvil_setCode", [PQC_VERIFY, evm.code_at(verify_addr)])
    evm.call("anvil_setCode", [PQC_KEYS, evm.code_at(keys_addr)])
    print(f"mocks etched: verify={PQC_VERIFY} keys={PQC_KEYS}")

    # 2. register the owner's "PQC key" with the mock
    mock = MockPQC(evm)
    pk = b"mock-owner-pubkey"
    mock.set_key(mock.encode_set_key(owner, 1, pk, True))
    print(f"owner {owner}: mock PQC key registered")

    # 3. deploy the treasury
    tx = evm.deploy(
        OWNER_KEY,
        str(ROOT / "contracts/out/QoreGuardTreasury.sol/QoreGuardTreasury.json"),
        ["address", "uint96"],
        [owner, 1_200_000],
        gas=6_000_000,
    )
    receipt = evm.wait(tx)
    contract = receipt.contract_address
    print(f"treasury deployed at {contract} (gas {receipt.gas_used})")

    def view(selector: bytes, types=None, args=None) -> str:
        return evm.eth_call(contract, selector + (abi_encode(types, args) if types else b""))

    def owner_call(data: bytes, gas: int = 600_000) -> dict:
        tx = evm.send(OWNER_KEY, to=contract, data=data, gas=gas)
        return evm.wait(tx)

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"   {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    # 4. admin: add two vendors, each with a real ML-DSA-87-format message
    nonce = int(view(T.SEL_APPROVAL_NONCE), 16)
    for vid, payee, label, per_tx, daily in (
        (1, vendor, "cloud-hosting", 200_000, 1_000_000),
        (2, vendor2, "api-credits", 300_000, 600_000),
    ):
        payload = T.payload_add_vendor(vid, payee, label, per_tx, daily)
        message = T.admin_message(9800, contract, T.ADD_VENDOR, payload, nonce)
        sig = mock_sign(message)
        data = T.calldata_add_vendor(vid, payee, label, per_tx, daily, nonce, pk, sig)
        rec = owner_call(data)
        check(f"addVendor {label}", rec.ok)
        nonce += 1

    check("vendorCount == 2", int(view(T.SEL_VENDOR_COUNT), 16) == 2)
    check("approvalNonce advanced to 2", int(view(T.SEL_APPROVAL_NONCE), 16) == 2)

    # an admin call with a signature for another nonce must fail
    payload = T.payload_set_treasury_cap(999)
    stale = mock_sign(T.admin_message(9800, contract, T.SET_TREASURY_CAP, payload, 0))
    try:
        evm.send(OWNER_KEY, to=contract, data=T.calldata_set_treasury_cap(999, 0, pk, stale), gas=600_000)
        rec = evm.wait(evm.send(OWNER_KEY, to=contract, data=T.calldata_set_treasury_cap(999, 0, pk, stale), gas=600_000))
        check("stale admin nonce refused", not rec.ok)
    except Exception as exc:
        check("stale admin nonce refused", True, type(exc).__name__)

    # 5. the agent lane: pay within caps, once per invoice, then over a cap
    def pay(vid: int, amount: int, invoice_key: str) -> dict:
        tx = evm.send(OWNER_KEY, to=contract, data=T.calldata_pay(vid, amount, invoice_key), value_wei=uqor_to_wei(amount), gas=300_000)
        return evm.wait(tx)

    key1001 = T.invoice_key("INV-1001", 1)
    rec = pay(1, 150_000, key1001)
    check("pay 0.15 QOR to cloud-hosting", rec.ok)
    check("vendor spend recorded", int(view(T.SEL_SPENT_TODAY, ["uint256"], [1]), 16) == 150_000)
    check("invoice recorded as settled", int(view(T.SEL_SETTLED_INVOICE_COUNT), 16) == 1)

    rec = pay(1, 150_000, key1001)
    check("the same invoice cannot be paid twice", not rec.ok)

    rec = pay(1, 250_000, T.invoice_key("INV-1002-pay", 1))
    check("pay above the 0.2 QOR vendor cap refused", not rec.ok)

    rec = pay(2, 700_000, T.invoice_key("INV-1003", 2))
    check("pay above the treasury day cap refused", not rec.ok)

    # 6. the exception path: an owner signature for exactly this payment
    amount, key1002 = 500_000, T.invoice_key("INV-1002", 1)
    nonce = int(view(T.SEL_APPROVAL_NONCE), 16)
    payload = T.payload_exception(1, amount, key1002)
    sig = mock_sign(T.admin_message(9800, contract, T.PAY_WITH_APPROVAL, payload, nonce))
    rec = evm.wait(
        evm.send(
            OWNER_KEY,
            to=contract,
            data=T.calldata_pay_with_approval(1, amount, key1002, nonce, pk, sig),
            value_wei=uqor_to_wei(amount),
            gas=300_000,
        )
    )
    check("owner-approved over-cap payment settles", rec.ok)
    check("treasury day spend is 0.65 QOR", int(view(T.SEL_TREASURY_SPENT_TODAY), 16) == 650_000)
    check("two distinct invoices settled", int(view(T.SEL_SETTLED_INVOICE_COUNT), 16) == 2)

    # neither lane can revisit an invoice that is already settled
    rec = pay(1, amount, key1002)
    check("the agent lane cannot revisit an approved invoice", not rec.ok)
    nonce = int(view(T.SEL_APPROVAL_NONCE), 16)
    sig2 = mock_sign(T.admin_message(9800, contract, T.PAY_WITH_APPROVAL, payload, nonce))
    rec = evm.wait(
        evm.send(
            OWNER_KEY,
            to=contract,
            data=T.calldata_pay_with_approval(1, amount, key1002, nonce, pk, sig2),
            value_wei=uqor_to_wei(amount),
            gas=300_000,
        )
    )
    check("a second approval for the same invoice is refused", not rec.ok)
    check("treasury day spend is still 0.65 QOR", int(view(T.SEL_TREASURY_SPENT_TODAY), 16) == 650_000)

    # the same approval cannot pay a different invoice amount
    other_key = T.invoice_key("INV-9999", 1)
    nonce = int(view(T.SEL_APPROVAL_NONCE), 16)
    try:
        tx = evm.send(
            OWNER_KEY,
            to=contract,
            data=T.calldata_pay_with_approval(1, 900_000, other_key, nonce, pk, sig),
            value_wei=uqor_to_wei(900_000),
            gas=300_000,
        )
        rec = evm.wait(tx)
        check("reused approval refused", not rec.ok)
    except Exception as exc:
        check("reused approval refused", True, type(exc).__name__)

    # 7. leave a record the dashboard can render without a testnet
    (ROOT / ".keys").mkdir(exist_ok=True)
    (ROOT / ".keys" / "deployment.local.json").write_text(
        json.dumps({"contract": contract, "tx": "local-anvil", "block": 0}, indent=2)
    )
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        return 1
    print("local integration: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
