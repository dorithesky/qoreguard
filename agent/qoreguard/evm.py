"""Minimal EVM-lane client for the QoreChain testnet.

The EVM lane accepts ordinary Ethereum transactions from an account that has a
registered post-quantum key *and* an open, unexhausted EVM window. This module
signs those transactions locally with `eth-account` and talks JSON-RPC to the
node; it deliberately does not use Foundry's broadcaster, because QoreChain's
unified account address is derived from the compressed public key
(ripemd160(sha256(pub))) rather than from keccak, so a tool that guesses the
sender locally would pick the wrong nonce. Nonces here always come from the
chain.

Testnet only.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from eth_account import Account
from eth_utils import to_checksum_address

from .treasury import WEI_PER_UQOR


class EvmRpcError(RuntimeError):
    def __init__(self, method: str, error: Any) -> None:
        super().__init__(f"{method} failed: {json.dumps(error)[:600]}")
        self.error = error


@dataclass
class EvmReceipt:
    tx_hash: str
    status: int
    gas_used: int
    block_number: int
    logs: list[dict]
    contract_address: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 1


class Evm:
    def __init__(self, rpc_url: str, chain_id: int) -> None:
        self.url = rpc_url
        self.chain_id = chain_id
        self._http = httpx.Client(timeout=40.0)
        self._id = 0

    # ------------------------------------------------------------------ #
    def call(self, method: str, params: list[Any] | None = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        r = self._http.post(self.url, json=payload)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise EvmRpcError(method, body["error"])
        return body.get("result")

    # ------------------------------------------------------------------ #
    def chain_id_on_chain(self) -> int:
        return int(self.call("eth_chainId"), 16)

    def gas_price(self) -> int:
        return int(self.call("eth_gasPrice"), 16)

    def nonce(self, address: str) -> int:
        return int(self.call("eth_getTransactionCount", [address, "pending"]), 16)

    def balance_wei(self, address: str) -> int:
        return int(self.call("eth_getBalance", [address, "latest"]), 16)

    def code_at(self, address: str) -> str:
        return self.call("eth_getCode", [address, "latest"])

    def eth_call(self, to: str, data: bytes) -> str:
        return self.call(
            "eth_call",
            [{"to": to, "data": "0x" + data.hex(), "value": "0x0"}, "latest"],
        )

    # ------------------------------------------------------------------ #
    def sign(
        self,
        private_key: bytes,
        *,
        nonce: int,
        to: str | None,
        data: bytes = b"",
        value_wei: int = 0,
        gas: int = 300_000,
        gas_price: int | None = None,
    ) -> dict:
        tx = {
            "nonce": nonce,
            "gasPrice": gas_price if gas_price is not None else self.gas_price(),
            "gas": gas,
            "value": value_wei,
            "data": data,
            "chainId": self.chain_id,
        }
        if to is not None:
            tx["to"] = to_checksum_address(to)
        signed = Account.sign_transaction(tx, private_key)
        raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
        return {"raw": raw, "tx": tx}

    def send_raw(self, raw: bytes) -> str:
        return self.call("eth_sendRawTransaction", ["0x" + raw.hex()])

    def send(
        self,
        private_key: bytes,
        *,
        to: str | None,
        data: bytes = b"",
        value_wei: int = 0,
        gas: int = 300_000,
        gas_price: int | None = None,
        nonce: int | None = None,
    ) -> str:
        sender = Account.from_key(private_key).address
        nonce = self.nonce(sender) if nonce is None else nonce
        built = self.sign(
            private_key, nonce=nonce, to=to, data=data, value_wei=value_wei, gas=gas, gas_price=gas_price
        )
        return self.send_raw(built["raw"])

    def wait(self, tx_hash: str, timeout: float = 60.0) -> EvmReceipt:
        deadline = time.time() + timeout
        while time.time() < deadline:
            rec = self.call("eth_getTransactionReceipt", [tx_hash])
            if rec:
                return EvmReceipt(
                    tx_hash=tx_hash,
                    status=int(rec["status"], 16),
                    gas_used=int(rec["gasUsed"], 16),
                    block_number=int(rec["blockNumber"], 16),
                    logs=rec.get("logs", []),
                    contract_address=rec.get("contractAddress"),
                )
            time.sleep(1.0)
        raise TimeoutError(f"no receipt for {tx_hash} after {timeout}s")

    # ------------------------------------------------------------------ #
    def deploy(
        self,
        private_key: bytes,
        artifact_path: str,
        constructor_types: list[str],
        constructor_args: list[Any],
        *,
        gas: int = 1_500_000,
        gas_price: int | None = None,
        nonce: int | None = None,
        value_wei: int = 0,
    ) -> str:
        from eth_abi import encode as abi_encode

        artifact = json.loads(open(artifact_path).read())
        bytecode = artifact["bytecode"]["object"]
        data = bytes.fromhex(bytecode[2:]) if bytecode.startswith("0x") else bytes.fromhex(bytecode)
        if constructor_types:
            data += abi_encode(constructor_types, constructor_args)
        return self.send(
            private_key,
            to=None,
            data=data,
            value_wei=value_wei,
            gas=gas,
            gas_price=gas_price,
            nonce=nonce,
        )


def uqor_to_wei(amount_uqor: int) -> int:
    return amount_uqor * WEI_PER_UQOR
