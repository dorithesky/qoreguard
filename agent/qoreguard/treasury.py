"""The treasury contract's interface, exactly as the Solidity sees it.

Everything an off-chain caller must reproduce byte-for-byte lives here:
function selectors, calldata, the admin payload hashes, and the exact message
the owner signs with ML-DSA-87. `contracts/src/QoreGuardTreasury.sol` is the
other half of this file; if either changes, both change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

DOMAIN = "QoreGuard.treasury.v1"
WEI_PER_UQOR = 10**12

# Action tags — identical to the bytes32 constants in the contract.
ADD_VENDOR = keccak(text="addVendor")
SET_VENDOR_ACTIVE = keccak(text="setVendorActive")
SET_VENDOR_CAPS = keccak(text="setVendorCaps")
SET_TREASURY_CAP = keccak(text="setTreasuryCap")
PAY_WITH_APPROVAL = keccak(text="payWithApproval")

# Function selectors.
SEL_PAY = keccak(text="pay(uint256,uint256,bytes32)")[:4]
SEL_PAY_WITH_APPROVAL = keccak(text="payWithApproval(uint256,uint256,bytes32,uint256,bytes,bytes)")[:4]
SEL_ADD_VENDOR = keccak(text="addVendor(uint256,address,bytes32,uint96,uint96,uint256,bytes,bytes)")[:4]
SEL_SET_VENDOR_ACTIVE = keccak(text="setVendorActive(uint256,bool,uint256,bytes,bytes)")[:4]
SEL_SET_VENDOR_CAPS = keccak(text="setVendorCaps(uint256,uint96,uint96,uint256,bytes,bytes)")[:4]
SEL_SET_TREASURY_CAP = keccak(text="setTreasuryCap(uint96,uint256,bytes,bytes)")[:4]


# View selectors (read-only eth_call).
SEL_APPROVAL_NONCE = keccak(text="approvalNonce()")[:4]
SEL_VENDOR = keccak(text="vendor(uint256)")[:4]
SEL_VENDOR_COUNT = keccak(text="vendorCount()")[:4]
SEL_VENDOR_ID_AT = keccak(text="vendorIdAt(uint256)")[:4]
SEL_SPENT_TODAY = keccak(text="spentToday(uint256)")[:4]
SEL_TREASURY_SPENT_TODAY = keccak(text="treasurySpentToday()")[:4]
SEL_TREASURY_DAILY_CAP = keccak(text="treasuryDailyCapUqor()")[:4]
SEL_TOTAL_SPENT = keccak(text="totalSpentUqor()")[:4]
SEL_OWNER_ACCOUNT = keccak(text="ownerAccount()")[:4]
SEL_ADMIN_MESSAGE = keccak(text="adminMessage(bytes32,bytes32,uint256)")[:4]


def memo_hash(memo: str) -> bytes:
    return keccak(text=memo)


def label_hash(label: str) -> bytes:
    return keccak(text=label)


def admin_message(chain_id: int, contract: str, action: bytes, payload_hash: bytes, nonce: int) -> bytes:
    """`abi.encodePacked(DOMAIN, block.chainid, address(this), action, payload, nonce)`."""
    return (
        DOMAIN.encode()
        + chain_id.to_bytes(32, "big")
        + bytes.fromhex(contract[2:])
        + action
        + payload_hash
        + nonce.to_bytes(32, "big")
    )


# ---------------------------------------------------------------------- #
# Payload hashes: one per administrative action
# ---------------------------------------------------------------------- #
def payload_add_vendor(vendor_id: int, payee: str, label: str, per_tx: int, daily: int) -> bytes:
    return keccak(
        abi_encode(
            ["uint256", "address", "bytes32", "uint96", "uint96"],
            [vendor_id, to_checksum_address(payee), label_hash(label), per_tx, daily],
        )
    )


def payload_set_vendor_active(vendor_id: int, active: bool) -> bytes:
    return keccak(abi_encode(["uint256", "bool"], [vendor_id, active]))


def payload_set_vendor_caps(vendor_id: int, per_tx: int, daily: int) -> bytes:
    return keccak(abi_encode(["uint256", "uint96", "uint96"], [vendor_id, per_tx, daily]))


def payload_set_treasury_cap(cap: int) -> bytes:
    return keccak(abi_encode(["uint96"], [cap]))


def payload_exception(vendor_id: int, amount: int, memo: str) -> bytes:
    return keccak(abi_encode(["uint256", "uint256", "bytes32"], [vendor_id, amount, memo_hash(memo)]))


# ---------------------------------------------------------------------- #
# Calldata
# ---------------------------------------------------------------------- #
def calldata_pay(vendor_id: int, amount_uqor: int, memo: str) -> bytes:
    return SEL_PAY + abi_encode(["uint256", "uint256", "bytes32"], [vendor_id, amount_uqor, memo_hash(memo)])


def calldata_pay_with_approval(
    vendor_id: int, amount_uqor: int, memo: str, nonce: int, pubkey: bytes, signature: bytes
) -> bytes:
    return SEL_PAY_WITH_APPROVAL + abi_encode(
        ["uint256", "uint256", "bytes32", "uint256", "bytes", "bytes"],
        [vendor_id, amount_uqor, memo_hash(memo), nonce, pubkey, signature],
    )


def calldata_add_vendor(
    vendor_id: int,
    payee: str,
    label: str,
    per_tx: int,
    daily: int,
    nonce: int,
    pubkey: bytes,
    signature: bytes,
) -> bytes:
    return SEL_ADD_VENDOR + abi_encode(
        ["uint256", "address", "bytes32", "uint96", "uint96", "uint256", "bytes", "bytes"],
        [vendor_id, to_checksum_address(payee), label_hash(label), per_tx, daily, nonce, pubkey, signature],
    )


def calldata_set_vendor_active(
    vendor_id: int, active: bool, nonce: int, pubkey: bytes, signature: bytes
) -> bytes:
    return SEL_SET_VENDOR_ACTIVE + abi_encode(
        ["uint256", "bool", "uint256", "bytes", "bytes"],
        [vendor_id, active, nonce, pubkey, signature],
    )


def calldata_set_vendor_caps(
    vendor_id: int, per_tx: int, daily: int, nonce: int, pubkey: bytes, signature: bytes
) -> bytes:
    return SEL_SET_VENDOR_CAPS + abi_encode(
        ["uint256", "uint96", "uint96", "uint256", "bytes", "bytes"],
        [vendor_id, per_tx, daily, nonce, pubkey, signature],
    )


def calldata_set_treasury_cap(cap: int, nonce: int, pubkey: bytes, signature: bytes) -> bytes:
    return SEL_SET_TREASURY_CAP + abi_encode(
        ["uint96", "uint256", "bytes", "bytes"], [cap, nonce, pubkey, signature]
    )


# ---------------------------------------------------------------------- #
# Owner-side signing
# ---------------------------------------------------------------------- #
@dataclass
class OwnerSignature:
    """A fresh ML-DSA-87 signature from the owner over one exact message."""

    nonce: int
    pubkey: bytes
    signature: bytes
    message: bytes

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"OwnerSignature(nonce={self.nonce}, msg={len(self.message)}B, sig={len(self.signature)}B)"


def owner_sign(
    pqc_keypair: Any,
    chain_id: int,
    contract: str,
    action: bytes,
    payload_hash: bytes,
    nonce: int,
    signer: Any,
) -> OwnerSignature:
    """Build the message for one action and sign it with the owner's ML-DSA-87 key.

    `signer` is `qorsdk.pqc_sign`-compatible: `signer(secret_key, message)`.
    """
    message = admin_message(chain_id, contract, action, payload_hash, nonce)
    signature = signer(pqc_keypair.secret_key, message)
    return OwnerSignature(nonce=nonce, pubkey=pqc_keypair.public_key, signature=signature, message=message)


def decode_uint(raw_hex: str) -> int:
    if not raw_hex or raw_hex == "0x":
        return 0
    return int(raw_hex, 16)


def decode_address(raw_hex: str) -> str:
    return "0x" + raw_hex[-40:]


def decode_vendor(result: Sequence[Any]) -> dict:
    payee, label, per_tx, daily, active = result
    return {
        "payee": payee,
        "label_hash": "0x" + bytes(label).hex(),
        "per_tx_cap_uqor": int(per_tx),
        "daily_cap_uqor": int(daily),
        "active": bool(active),
    }
