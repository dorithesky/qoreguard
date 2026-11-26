# QoreGuard — a treasury agent whose limits are enforced by a post-quantum chain

**Build Before Q-Day · Post-Quantum AI Agents track · QoreChain testnet `qorechain-diana`**

An AI agent pays an owner's recurring bills. It holds no funds and cannot raise
its own limits: the chain enforces what it may spend and which contracts it may
touch, and a Solidity policy contract enforces who may be paid and how much. Any
change to that policy needs a **fresh ML-DSA-87 signature from the owner**,
verified on chain by the `pqcVerify` precompile (`0x…0a01`). An attacker who
holds the owner's classical (ECDSA) key — the key a quantum computer would
recover — still cannot add a payee or raise a cap.

That is the Q-Day story told in the one category where it matters most: an agent
with a wallet.

```
                       ┌──────────────────────────────────────────────┐
  signed invoice  ───► │ agent (ed25519 hot key, no funds)            │
  (vendor key)         │  · authenticates the invoice                 │
                       │  · writes a signed intent (audit)            │
                       └───────────────┬──────────────────────────────┘
                                       │ MsgExecuteEVM (agent signature)
                                       ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ QoreChain testnet — gate 1: the account's own rules               │
   │  · permission: send, evm (no deploy, no admin)                    │
   │  · spending rule: ≤0.6 QOR/tx, ≤1.5 QOR/day, uqor only            │
   │  · allowed EVM calls: pay() and payWithApproval() on the          │
   │    treasury contract — nothing else, not even a transfer          │
   │  · relayer pays the fee; the owner's ML-DSA key never leaves home │
   └───────────────────────────────┬───────────────────────────────────┘
                                   │ only these two functions
                                   ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ QoreGuardTreasury.sol — gate 2: the business policy               │
   │  · vendor allowlist + per-vendor per-tx and daily caps            │
   │  · treasury-wide daily cap                                        │
   │  · audit events for every payment and every refusal               │
   │  · policy changes require pqcVerify(owner ML-DSA-87 signature)   │
   └───────────────────────────────────────────────────────────────────┘
```

## What is on the testnet right now

| | |
| --- | --- |
| Contract | [`0xa437ca5360214d4225ab3323da8f72db99250d92`](https://explore.qore.network/address/0xa437ca5360214d4225ab3323da8f72db99250d92) on `qorechain-diana` |
| Owner (eth-native) | `qor1qj002t0yrrwud08geqww0r5f56tgu09rqjxqcd` / `0x049Ef52DE418DDC6Bce8c81ce78E89A6968e3Ca3` |
| Relayer (native) | `qor1zcsp7xup7y08czmc9z563x0qk3q6sm5827ftf8` |
| Vendors | `0x305e5935929844bECA43aB6376226a7916A2d129`, `0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D` |
| Payments settled | 4 (0.15 + 0.25 + 0.10 + 0.50 QOR), 1.0 QOR of a 1.5 QOR daily cap |
| Policy refusals on chain | 5 (vendor per-tx ×2, chain per-tx, vendor daily, tampered invoice stopped off-chain) |
| Evidence | [`docs/EVIDENCE.md`](docs/EVIDENCE.md) — every transaction hash, generated from the chain |

## Why two gates

| Failure | What stops it |
| --- | --- |
| The agent's key is stolen | Gate 1: it can only call two functions, within a daily cap. Gate 2: caps per vendor. One transaction revokes it — after that the chain answers `linked key is unbound or revoked`. |
| A bug in the agent pays the wrong vendor | Gate 2: the vendor registry is PQ-signed by the owner; `msg.sender` must be the owner account. |
| A prompt-injected invoice | The invoice must be signed by the vendor's registered key, and the payment memo commits to that exact signed invoice. |
| The owner's classical key is leaked (Q-Day) | It cannot open an EVM window (that needs a hybrid signature) and cannot change the policy (that needs an ML-DSA-87 signature); gate 1's rule stays in force. |
| The relayer is malicious | It can only submit; every payment carries the agent's own signature over `evmAuthSignBytesV2`, bound to chain, account, target, value, calldata, nonce and gas limit. |
| A signature is replayed | The authenticator nonce is the chain's per-key counter, and every admin signature binds a monotonic `approvalNonce`. |

## The four chain features this build uses

1. **Hybrid ML-DSA-87 transactions** (`qorsdk` hybrid sign-bytes v2) for every
   owner and relayer transaction.
2. **Authenticator (linked key) lane** — `MsgRegisterAuthenticator` +
   `MsgUpdateSpendingRules` + `MsgExecuteEVM`, i.e. an agent key with
   least-privilege permissions, spending rules and an EVM-call allowlist.
3. **Scoped EVM window** — `MsgOpenEVMWindow` with a tx count, a value budget and
   an `allowed_targets` list, used for the deployment and the policy setup only.
4. **Post-quantum precompiles** — `pqcKeyStatus` (`0x…0a02`) and `pqcVerify`
   (`0x…0a01`) called from Solidity, with the empty-return-data trap handled.

## Repository layout

```
contracts/            Foundry project: QoreGuardTreasury.sol, mocks, 23 tests
agent/qoreguard/      Python package: chain, evm, treasury ABI, planner, agent, CLI
demo/                 mandate.json, signed invoices, journal (evidence)
scripts/              key bootstrap, invoice signing, demo runner
docs/                 architecture, threat model, submission copy
AI_USAGE.md           how this was built with AI (Best AI-Native Build entry)
```

## Quickstart (testnet only, no funds of value)

```bash
# 1. throwaway key material (owner, relayer, two vendors)
python scripts/bootstrap_keys.py            # writes .keys/keys.env, mode 600
python scripts/sign_invoices.py             # vendors sign demo/invoices.unsigned.json

# 2. fund the three addresses from the faucet (dashboard.qorechain.io/faucet)
python -m qoreguard.cli status              # shows the addresses and balances

# 3. register ML-DSA-87 keys (the first transaction of every account)
python -m qoreguard.cli register

# 4. deploy inside a tightly scoped EVM window
python -m qoreguard.cli window open --txs 1 --value 1000000 --blocks 200
python -m qoreguard.cli deploy

# 5. the owner sets the policy (each call carries an ML-DSA-87 signature)
python -m qoreguard.cli window open --txs 4 --value 200000 --blocks 200 \
        --target <contract>
python -m qoreguard.cli setup --set-cap

# 6. link the agent with the chain-enforced limits
python -m qoreguard.cli link

# 7. let it pay the bills
python -m qoreguard.cli run
python -m qoreguard.cli approve INV-1002    # one owner-approved exception
python -m qoreguard.cli revoke              # and one transaction to switch it off

# 8. audit
python -m qoreguard.cli verify
```

`python -m qoreguard.cli status` reads everything back from the chain: account
numbers, sequences, the registered PQC key, the live EVM window, the agent's
authenticator nonce, and the contract's vendors, caps and spend ledger.

## Tests

```bash
cd contracts
forge install foundry-rs/forge-std  # once
forge test                          # 23 tests: policy, PQ gate, encoding parity
```

```bash
bash scripts/local_integration.sh   # 12 checks against anvil, precompiles mocked
```

The Foundry tests etch stand-ins for the two precompiles at their real
addresses, so the policy and the PQ gate are exercised without a live node. The
mock `pqcVerify` also binds the signature to the message, so a test can prove
that an approval for 0.5 QOR cannot pay 0.9 QOR.

## Two things a builder should know (learned the hard way)

1. **The owner is an eth-native (unified) account.** QoreChain's EVM lane
   attributes a transaction to the keccak-derived address of the signing key, so
   a plain Cosmos-native account (whose 20 bytes are `ripemd160(sha256(pub))`)
   can *read* its `0x` balance but cannot *send* from it on the EVM lane. The
   owner, vendors and any account that sends on the EVM lane are therefore
   `eth_secp256k1` accounts; the relayer, which only sends native transactions,
   stays native. `derive_unified_account` gives one key for both lanes.
2. **A refunded EVM execution still consumes the authenticator nonce.** A
   payment that the contract refuses lands as a transaction with `code: 0` and an
   `abstractaccount_execution_failed` event; the nonce has moved, so the next
   attempt signs a new one. The agent reads the nonce from the chain for every
   payment instead of tracking it locally.

## Honest limitations

- The `x/abstractaccount` module and the PQC precompiles are **testnet only**
  today; on mainnet the module is switched off and the precompile list is empty.
  This project is built for `qorechain-diana` and says so everywhere.
- The treasury holds no balance: each payment arrives as `msg.value` and is
  forwarded in the same transaction. That is deliberate — the chain then sees
  the real outflow on every call, which is what gate 1 bounds.
- The demo pays 0.1–0.7 QOR test amounts. Nothing here touches real funds.
- The agent's planner is deterministic; the optional LLM annotation never
  changes an action (see `AI_USAGE.md`).
- The owner signs admin messages from a script in this repo. In production the
  same bytes would be signed inside QoreX, which holds the ML-DSA-87 key in the
  Secure Enclave.

## Credits

QoreChain SDK v0.15.0 (`qorechain-sdk`), the QoreChain docs and the
`build-before-qday` workshop-2 examples (structure of the vault/agent flow),
Foundry, eth-abi/eth-account, and the QoreChain testnet faucet.

Apache-2.0.
