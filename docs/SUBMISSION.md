# Submission copy — Build Before Q-Day

Paste-ready text for the project form on `build.qorechain.io`. Everything below
describes only what the repository and the testnet actually contain.

## Basic fields

| Field | Value |
| --- | --- |
| Project name | QoreGuard |
| Category | Post-Quantum AI Agents |
| Team | pickle-scout (solo) |
| Licence | Apache-2.0 |
| Repository | https://github.com/dorithesky/qoreguard (see docs/EVIDENCE.md until published) |
| Video | `demo/qoreguard-demo.mp4` (uploaded to the submission form) |
| Featured image | `docs/cover.png` |
| Network | QoreChain testnet `qorechain-diana` (EVM chain ID 9800) |
| Testnet addresses used | owner (eth-native) `qor1qj002t0yrrwud08geqww0r5f56tgu09rqjxqcd` / `0x049Ef52DE418DDC6Bce8c81ce78E89A6968e3Ca3`; relayer `qor1zcsp7xup7y08czmc9z563x0qk3q6sm5827ftf8`; vendors `0x305e5935929844bECA43aB6376226a7916A2d129`, `0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D` |
| Special award | Best AI-Native Build (`AI_USAGE.md`) |

## Short description (one line)

An AI treasury agent whose spending limits, payee allowlist and policy are
enforced on chain — and whose policy can only be changed with the owner's
ML-DSA-87 signature, verified by QoreChain's `pqcVerify` precompile.

## Long description

An AI agent that pays bills needs a wallet, and a wallet needs limits that
survive the agent: a stolen key, a prompt-injected invoice, a bug in the
planner. QoreGuard puts those limits where the agent cannot reach them.

**Gate 1 — the account's own rules.** The agent is a linked ed25519 key on the
owner's QoreChain account (`MsgRegisterAuthenticator`) with `send` and `evm`
permissions only, a spending rule of 0.6 QOR per call and 1.5 QOR per day in
`uqor`, and an `allowed_evm_calls` list naming exactly two functions on one
contract. Even with the agent's key, an attacker can pay registered vendors
within one day's cap and nothing else: no transfers, no other contracts, no
deployment. The owner's ML-DSA-87 key never leaves the owner's side; the agent's
relayer submits and pays the fee.

**Gate 2 — the business policy.** `QoreGuardTreasury.sol` holds the vendor
allowlist, per-vendor per-transaction and daily caps, and a treasury-wide daily
cap. Payments arrive as `msg.value` and are forwarded in the same transaction, so
the chain sees the real outflow that gate 1 bounds. Every payment and its
invoice commitment is an event.

**The post-quantum part.** Adding a vendor, changing a cap, or approving one
payment above a cap requires a fresh ML-DSA-87 (FIPS 204) signature from the
owner over a message that binds the domain, chain id, contract, action, payload
and a monotonic nonce. The contract checks it on chain: `pqcKeyStatus` for the
registered, proven key, then `pqcVerify` (`0x…0a01`) for the signature. An
attacker who has recovered the owner's classical ECDSA key — the Q-Day scenario
this fest is named for — cannot add a payee, raise a cap, or move money: those
paths need a key that quantum computers do not have.

**Invoices are authenticated, not trusted.** Each vendor signs its invoice with
an ed25519 invoice key registered for it in the mandate. The agent verifies the
signature before it builds anything, and the payment's on-chain memo is a
commitment to that exact signed invoice. The demo includes a tampered invoice
whose amount changed after signing; it is refused before a transaction exists.

**What the demo shows on testnet** (every hash in `docs/EVIDENCE.md`): the
owner's ML-DSA-87 key registered with a proof of possession in a classical
bootstrap transaction; a deployment EVM window of one transaction, then a
target-scoped admin window that admits only the treasury contract; two vendors
registered through PQ-signed calls that the contract verified with
`pqcVerify`; the agent linked with `send`+`evm`, 0.6 QOR/tx, 1.5 QOR/day and two
allowed selectors; four payments settled; five policy refusals — two vendor-cap
refusals, the chain's own per-call limit (`600000uqor exceeded (attempted
700000)`), a vendor daily-cap refusal, and a tampered invoice stopped before the
chain; one owner-approved exception that settled; one PQ-signed cap change; one
`MsgRevokeAuthenticator` after which the chain answers `linked key is unbound or
revoked`; and a Q-Day check where the owner's classical key submits a
`setTreasuryCap` with a forged PQ signature — mined, reverted, policy unchanged.

**What a reviewer can press.** `web/lab.html` runs seven refusals in the browser
as `eth_call`s — the page holds no key, so it can only ask and be refused — and
verifies the owner's recorded ML-DSA-87 signature against the key the chain
holds. `web/qorex.html` is the wallet path: `@qorechain/qorex-types` detects
QoreX and reads the account and its on-chain key status with `native.getKey`,
`verifyMessageWithChain` verifies a real hybrid signature in the page, and the
wallet's current refusal of `signArbitrary` is reported verbatim. The contract
carries 51 Foundry tests, 13 invariants over an 8-action handler, 100 % line /
statement / branch / function coverage and 19/19 killed mutants; a QoreScan CBOM
with DORA and NIS2 mappings sits in `docs/`; and a daily GitHub Actions run
re-executes `forge test` and `python verify.py` with no secret configured.

**Why this is AI-native.** The build itself used an AI workflow (documented in
`AI_USAGE.md`), and the design keeps AI out of the trust path: the agent may be
wrong, and the chain does not care.

## Tags

`post-quantum`, `ML-DSA-87`, `agents`, `account-abstraction`, `spending-limits`,
`Solidity`, `Python`, `qorechain-diana`
