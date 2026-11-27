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
with a wallet. Around that core sit three more rails, all on the same testnet
account: invoices archived **encrypted to the owner's ML-KEM-1024 key**
(QoreSeal), over-cap payments approved by the owner as a **Sign-In-With-QoreChain
signature** verified against the on-chain ML-DSA-87 key (QoreSign), and a
**stablecoin invoice settled in fUSD** by the same agent under the same spending
rule (test tokens). `python verify.py` re-checks all of it read-only: **40 checks,
no keys, no writes**. Two static pages let a judge press the refusals in a
browser with no wallet: [`web/lab.html`](https://dorithesky.github.io/qoreguard/web/lab.html)
and the [`QoreX signing console`](https://dorithesky.github.io/qoreguard/web/qorex.html).

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
   │  · spending rule: ≤0.6 QOR/tx, ≤1.5 QOR/day, uqor + fUSD          │
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
| Payments settled | 5 QOR payments (1.25 QOR all-time) **+ 1 fUSD invoice (500 fUSD)** |
| Policy refusals on chain | 5 (vendor per-tx ×2, chain per-tx, vendor daily, tampered invoice stopped off-chain) |
| Sealed invoices | 10 envelopes, archive digest committed on chain (`qg:seal-archive:v1:19527ac1…`) |
| Owner approval | SIWQ message verified against the on-chain ML-DSA-87 key, then paid on chain |
| Ship Weeks | 4/4 badges (first hybrid tx, first swap in a pool, first QoreSeal key, first PQC-checking contract) |
| Tests | **51 Foundry tests**, **13 invariants** over an 8-action handler, **100 %** of lines, statements, branches and functions, **19/19 mutants killed** (`docs/verification.json`) |
| Inventory | [`docs/cbom.json`](docs/cbom.json) — QoreScan CycloneDX 1.6 CBOM, with [`docs/DORA-REPORT.md`](docs/DORA-REPORT.md) and [`docs/NIS2-REPORT.md`](docs/NIS2-REPORT.md) |
| CI | `.github/workflows/daily-verify.yml` — every day, `forge test` + `python verify.py` on a machine we do not control, with no secret configured |
| Evidence | [`docs/EVIDENCE.md`](docs/EVIDENCE.md) — generated from the chain · [`docs/JUDGES.md`](docs/JUDGES.md) — a 5-minute tour |

## Why two gates

| Failure | What stops it |
| --- | --- |
| The agent's key is stolen | Gate 1: it can only call two functions, within a daily cap. Gate 2: caps per vendor. One transaction revokes it — after that the chain answers `linked key is unbound or revoked`. |
| A bug in the agent pays the wrong vendor | Gate 2: the vendor registry is PQ-signed by the owner; `msg.sender` must be the owner account. |
| A prompt-injected invoice | The invoice must be signed by the vendor's registered key, and the payment memo commits to that exact signed invoice. |
| The owner's classical key is leaked (Q-Day) | It cannot open an EVM window (that needs a hybrid signature) and cannot change the policy (that needs an ML-DSA-87 signature); gate 1's rule stays in force. |
| The relayer is malicious | It can only submit; every payment carries the agent's own signature over `evmAuthSignBytesV2`, bound to chain, account, target, value, calldata, nonce and gas limit. |
| A signature is replayed | The authenticator nonce is the chain's per-key counter, and every admin signature binds a monotonic `approvalNonce`. |

## Beyond payments: the three other rails

| Rail | What the chain records | Kit |
| --- | --- | --- |
| **QoreSeal archive** | Each signed invoice is encrypted to the owner's ML-KEM-1024 key (hybrid X25519 + ML-KEM envelope, bound to chain id, purpose and invoice id). Only a 32-byte digest goes on chain: `qg:seal-archive:v1:19527ac1…` in tx [`33B776A0…`](https://explore.qore.network/tx/33B776A02C703158B1F1462E68F301C9BA774E26B4BE08B24D51CA7629538E22). The owner's key is registered on chain in tx [`A32934FE…`](https://explore.qore.network/tx/A32934FED596CE21DB3B6E4C6CFCEA81F1B2F57E76B46117A19EFCE64602E86C) (ML-KEM-1024, 1568 B, read back identical). | `qorechain-qoreseal` |
| **QoreSign approval** | An over-cap invoice (250 000 uqor against a 200 000 uqor per-tx cap) is approved by the owner as a SIWQ message — `qoreguard.approval wants you to sign in with your QoreChain account…`. The agent verifies it with `verify_siwq(require_pq=True)` against the **ML-DSA-87 key registered on chain**, then pays through `payWithApproval` ([`0C261DC9…`](https://explore.qore.network/tx/0C261DC914A9CE20D2E9DE877BCB3552FB195921AA1ACDDF274C705622163E6B)). A doubled amount, a replayed nonce or another invoice id is refused. | `qorechain-qoresign` |
| **fUSD settlement** | A 500 fUSD invoice is paid by the agent itself on the native lane: `MsgExecuteCosmos` carries a bank send of the `erc20:0x9714…` denom, signed over the chain's `cosmos_auth_sign_bytes` digest and bounded by the same spending rule (which now lists the token denom) — tx [`3DAD3DF9…`](https://explore.qore.network/tx/3DAD3DF948B61ED87642BC476C6F79DD66A0E1F182405510EF9D1696FC028D08). The vendor's account holds the 500 fUSD afterwards. | SDK + QoreChain test tokens |

The read side of the agent goes through the **`qorechain-mcp`** server
(`agent/qoreguard/mcpread.py`): treasury views, account state and transaction
lookups are MCP tool calls, so the same answers are available to any MCP client
without credentials — and none of those tools can write.

## What a judge can press, without a wallet

| Surface | What it does | Why it is not a mock |
| --- | --- | --- |
| [`web/lab.html`](https://dorithesky.github.io/qoreguard/web/lab.html) | seven refusal experiments, each an `eth_call` from the page: over-cap, unknown vendor, forged ML-DSA-87 signature, memo ≠ value, stale approval nonce, a non-owner sender, a one-byte signature | the page holds **no key**; the revert comes back from the deployed contract, decoded by selector |
| [`web/qorex.html`](https://dorithesky.github.io/qoreguard/web/qorex.html) | `window.qorex.native.signArbitrary(chainId, address, data)` for a QoreX wallet, then `verifyMessageWithChain` — the browser verifies an ML-DSA-87 signature against the key the chain holds | the key is read from the `pqcKeyStatus` precompile (`0x…0a02`) that the contract itself calls, and one changed byte is refused in the page |

Neither page can sign, so neither can move money. They exist to make the
refusals and the post-quantum check something a reviewer can watch rather than
read.

## Inventory and regulation

`qorescan` reads the chain and this repository's Solidity and writes:

```
docs/cbom.json          CycloneDX 1.6: ML-DSA-87 (FIPS 204, post-quantum),
                        ECDSA secp256k1 / Ed25519 (quantum-vulnerable), Keccak-256
docs/DORA-REPORT.md     DORA RTS 2024/1774 Article 6 mapping, per algorithm
docs/NIS2-REPORT.md     EU NIS Cooperation Group PQC roadmap mapping
```

The citations are checkable: `verify.py` reads every `QoreGuardTreasury.sol:N`
line the CBOM points at and confirms the primitive is there, and confirms both
reports cite the same CBOM serial and list the same algorithms.

## The seven chain features this build uses

1. **Hybrid ML-DSA-87 transactions** (`qorsdk` hybrid sign-bytes v2) for every
   owner and relayer transaction.
2. **Authenticator (linked key) lane** — `MsgRegisterAuthenticator` +
   `MsgUpdateSpendingRules` + `MsgExecuteEVM`, i.e. an agent key with
   least-privilege permissions, spending rules and an EVM-call allowlist.
3. **Scoped EVM window** — `MsgOpenEVMWindow` with a tx count, a value budget and
   an `allowed_targets` list, used for the deployment and the policy setup only.
4. **Post-quantum precompiles** — `pqcKeyStatus` (`0x…0a02`) and `pqcVerify`
   (`0x…0a01`) called from Solidity, with the empty-return-data trap handled.
5. **ML-KEM-1024 key registry** — `msg.pqc.register_kem_key`, the key QoreSeal
   encrypts the invoice archive to.
6. **Native AMM** — `msg.amm.swap_exact_in` on pool 2 (fUSD/QOR), which is how
   the demo treasury obtained its fUSD, and the pool's first swap badge.
7. **Test tokens (fUSD)** — a real invoice settled in a test stablecoin through
   the authenticator lane (`MsgExecuteCosmos`).

## Repository layout

```
verify.py             read-only re-check of everything below (40 checks, no keys)
contracts/            Foundry project: treasury, mocks, 51 tests (13 invariants)
agent/qoreguard/      chain, evm, treasury ABI, planner, agent, CLI,
                      sealing (QoreSeal), approvals (QoreSign/SIWQ), mcpread (MCP)
demo/                 mandate, signed invoices, journal, p1.json, sealed/ envelopes,
                      qorex-console.json (the signature the console verifies)
scripts/              key bootstrap, invoice signing, demo runners, dashboard,
                      mutate.py (mutation testing), record_tests.py (Foundry evidence),
                      build_web.py (the two pages), test_web.mjs (their module in Node)
web/                  dashboard, attack lab, QoreX console (static, no server, no key)
docs/                 JUDGES.md, evidence, threat model, rubric map, submission copy,
                      cbom.json, DORA-REPORT.md, NIS2-REPORT.md, verification.json
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

# 8. the other rails
python scripts/onchain_features.py kem      # register the owner's ML-KEM-1024 key
python scripts/onchain_features.py swap     # one AMM swap (pool 2) for the fUSD rail
python scripts/run_p1.py all                # seal the archive, re-link, approve, pay fUSD

# 9. audit — this is the one to run first
python verify.py
```

`python -m qoreguard.cli status` reads everything back from the chain: account
numbers, sequences, the registered PQC key, the live EVM window, the agent's
authenticator nonce, and the contract's vendors, caps and spend ledger.

## Tests

```bash
cd contracts
forge install foundry-rs/forge-std  # once
forge test                          # 51 tests: policy, PQ gate, encoding parity,
                                    # fuzz, and 13 invariants over an 8-action handler
forge coverage --report summary --no-match-coverage "test|mocks|lib"   # 100/100/100/100
python scripts/mutate.py            # 19 mutants, all must be killed
python scripts/record_tests.py      # writes docs/verification.json (source digest + results)
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
- The owner signs admin messages from a script in this repo. `web/qorex.html`
  implements the production path — `window.qorex.native.signArbitrary` plus
  `verifyMessageWithChain` — but no QoreX wallet was installed in the browser we
  built it in, so the page cannot demonstrate the *signing* half there; it
  verifies a real recorded signature, and refuses a changed byte, in the page.
  The recorded signature was made by `scripts/qorex_console.py` with the owner's
  hybrid key over exactly the bytes the page displays
  (`demo/qorex-console.json`), in the same wire format the wallet produces.
- The sealed invoices are published twice on purpose: the envelopes (which need
  the owner's ML-KEM secret key) and, for the demo only, their plaintext, so a
  reviewer can check that opening an envelope returns the same bytes.

## Credits

QoreChain SDK v0.15.0 (`qorechain-sdk`), the QoreChain docs and the
`build-before-qday` workshop-2 examples (structure of the vault/agent flow),
Foundry, eth-abi/eth-account, and the QoreChain testnet faucet.

Apache-2.0.
