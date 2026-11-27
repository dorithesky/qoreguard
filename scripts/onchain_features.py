"""Two extra on-chain primitives QoreGuard uses beyond payments.

    python scripts/onchain_features.py kem     # register the owner's ML-KEM-1024 key (QoreSeal)
    python scripts/onchain_features.py swap    # one native-AMM swap, owner-signed
    python scripts/onchain_features.py show    # read both back from the chain

Both are hybrid (classical + ML-DSA-87) native transactions from the treasury
owner account on `qorechain-diana`. They are the two Ship Weeks challenges that
were still open: "first QoreSeal key" and "first swap in a pool".

Nothing here touches mainnet and nothing costs anything: test QOR only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import qoreseal  # noqa: E402
import httpx  # noqa: E402
from qorsdk import fetch_kem_key  # noqa: E402
from qorsdk.rest import RestClient  # noqa: E402
from qorsdk.messages import msg as compose  # noqa: E402
from qorsdk.unified import derive_unified_account  # noqa: E402

from agent.qoreguard.chain import Chain  # noqa: E402
from agent.qoreguard.config import settings  # noqa: E402

OUT = ROOT / "demo" / "onchain_features.json"
#: Pool 2 on qorechain-diana: erc20:0x9714… / uqor, constant product.
POOL_ID = 2
TOKEN_OUT = "erc20:0x97143B771D34ddb9130b807b135Ae675A5a963CA"
AMOUNT_IN_UQOR = 100_000  # 0.1 QOR


def load() -> dict:
    return json.loads(OUT.read_text()) if OUT.exists() else {}


def save(data: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def seal_keys() -> "qoreseal.SealKeyPair":
    """The owner's QoreSeal keys, derived from the same mnemonic as the account.

    Deterministic derivation means the demo can be replayed from the mnemonic
    alone; the mnemonic itself never leaves `.keys/keys.env`.
    """
    cfg = settings()
    seed = qoreseal.seal_seed_from_mnemonic(cfg.owner_mnemonic)
    return qoreseal.seal_keys_from_seed(seed)


def cmd_kem() -> dict:
    cfg = settings()
    chain = Chain(cfg)
    owner = derive_unified_account(cfg.owner_mnemonic)
    keys = seal_keys()

    already = fetch_kem_key(RestClient(cfg.rest), owner.cosmos)
    if already.found and already.key is not None:
        # Already on chain: re-read it instead of registering twice (the chain
        # would replace the key, which is fine, but one transaction is enough).
        record = {
            "address": owner.cosmos,
            "tx": os.environ.get("QORE_KEM_TX", ""),
            "already": True,
            "key_len": len(already.key.public_key),
            "chain_matches": already.key.public_key == keys.mlkem_public_key,
            "chain_algorithm_id": already.key.algorithm_id,
            "chain_algorithm_name": already.key.algorithm_name,
            "registered_height": already.key.registered_height,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        data = load()
        data["kem"] = record
        save(data)
        print(json.dumps(record, indent=2))
        return record

    message = compose.pqc.register_kem_key(sender=owner.cosmos, public_key=keys.mlkem_public_key)
    outcome = chain.send_owner(owner, [message], memo="qoreguard:register-kem-key")
    print("register_kem_key tx", outcome.tx_hash, "code", outcome.code, outcome.raw_log)
    if outcome.code != 0:
        raise SystemExit("registration refused")

    # Read it back: the chain's own copy, not ours.
    found = None
    for _ in range(12):
        time.sleep(2)
        got = fetch_kem_key(RestClient(cfg.rest), owner.cosmos)
        if got.found and got.key is not None:
            found = got.key
            break
    record = {
        "address": owner.cosmos,
        "tx": outcome.tx_hash,
        "already": False,
        "key_len": len(keys.mlkem_public_key),
        "chain_matches": bool(found and found.public_key == keys.mlkem_public_key),
        "chain_algorithm_id": found.algorithm_id if found else None,
        "chain_algorithm_name": found.algorithm_name if found else None,
        "registered_height": found.registered_height if found else None,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data = load()
    data["kem"] = record
    save(data)
    print(json.dumps(record, indent=2))
    return record


def cmd_swap() -> dict:
    cfg = settings()
    chain = Chain(cfg)
    owner = derive_unified_account(cfg.owner_mnemonic)
    before = chain.balances(owner.cosmos)

    from cosmpy.protos.cosmos.base.v1beta1.coin_pb2 import Coin

    message = compose.amm.swap_exact_in(
        sender=owner.cosmos,
        pool_id=POOL_ID,
        token_in=Coin(denom="uqor", amount=str(AMOUNT_IN_UQOR)),
        denom_out=TOKEN_OUT,
        min_out="1",
    )
    outcome = chain.send_owner(owner, [message], memo="qoreguard:first-swap")
    print("swap tx", outcome.tx_hash, "code", outcome.code, outcome.raw_log)
    time.sleep(3)
    after = chain.balances(owner.cosmos)
    record = {
        "tx": outcome.tx_hash,
        "code": outcome.code,
        "pool_id": POOL_ID,
        "amount_in_uqor": AMOUNT_IN_UQOR,
        "denom_out": TOKEN_OUT,
        "token_out_received": after.get(TOKEN_OUT, 0) - before.get(TOKEN_OUT, 0),
        "uqor_before": before.get("uqor", 0),
        "uqor_after": after.get("uqor", 0),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if outcome.code != 0:
        raise SystemExit("swap refused: " + outcome.raw_log)
    data = load()
    data["swap"] = record
    save(data)
    print(json.dumps(record, indent=2))
    return record


def cmd_show() -> None:
    cfg = settings()
    owner = derive_unified_account(cfg.owner_mnemonic)
    kem = fetch_kem_key(RestClient(cfg.rest), owner.cosmos)
    bal = Chain(cfg).balances(owner.cosmos)
    print(json.dumps({
        "owner": owner.cosmos,
        "kem_found": getattr(kem, "found", False),
        "kem_public_key_len": len(getattr(kem, "public_key", b"") or b""),
        "balances": bal,
        "recorded": load(),
    }, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    {"kem": cmd_kem, "swap": cmd_swap, "show": cmd_show}[cmd]()
