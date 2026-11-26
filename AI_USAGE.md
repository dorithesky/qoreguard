# AI usage — how QoreGuard was built

This file documents the AI workflow behind the project, as the **Best AI-Native
Build** award requires: what the AI produced, what it did not, and where a human
stayed in the loop.

Short version: the code, the tests, the documentation and the testnet operations
in `docs/EVIDENCE.md` were produced by an AI coding assistant working from the
QoreChain documentation, with a human in the owner role deciding the policy and
holding the keys that matter.

---

## 1. Setup

| | |
| --- | --- |
| Assistant | A general-purpose coding agent with shell, file, web and browser tools |
| Human | `pickle-scout`, in the treasury-owner role: approved the scope, chose the spending numbers, holds the owner key |
| Model access | The AI ran the toolchain itself: Python, Foundry, the QoreChain SDK and the live testnet |
| Repo starting point | Empty directory. No starter kit was copied; the `build-before-qday` workshop was read for its *shape* (vault + agent + MCP) and credited in the README |

## 2. Workflow

1. **Read the rules, not the marketing.** The AI read `build.qorechain.io`
   (home, prizes, rules, FAQ, builder FAQ) and pulled out the exact conditions:
   one category per project, the five-criterion rubric, "Q-Day Survivor closed on
   17 November", "Top-Quality only if you win nothing else". That changed the
   plan: target *one* category well (Post-Quantum AI Agents) plus *Best
   AI-Native Build*, instead of spreading across tracks.
2. **Read the protocol before writing code.** Precompiles (`pqcVerify` at
   `0x…0a01`, `pqcKeyStatus` at `0x…0a02`), the authenticator lane, spending-rule
   fields (`allowed_evm_calls` with `0x<contract>/0x<selector>`), the v2 EVM
   auth digest, the EVM window traps (opening advances the account sequence;
   `max_value` covers fees too). The first design draft paid vendors from a
   contract balance — reading the rule semantics ("the account's native balance
   decrease") killed that design, because the chain would have seen zero outflow.
   The final `pay{value: ...}` design exists because of that reading.
3. **Write the contract, then attack it.** The AI wrote
   `QoreGuardTreasury.sol`, then wrote the tests that try to break it: wrong
   sender, per-tx cap, per-vendor day cap, treasury cap, inactive vendor, value
   mismatch, unregistered/unproven/mismatched key, bad signature, nonce replay,
   an approval for one amount used on another, and an inactive precompile that
   answers with empty data.
4. **Close the encode/decode gap.** Off-chain encoders are where these projects
   silently break, so the AI generated reference hashes from the Python encoder
   and asserted them in Solidity (`contracts/test/Encoding.t.sol`). If either
   side drifts, `forge test` fails before a transaction is ever signed.
5. **Run it on the testnet and write down what the chain said.** Every command
   prints the chain's answer, and the journal records refusals as first-class
   results. `docs/EVIDENCE.md` is a transcript of real transactions, not a plan.
6. **Write the submission from the artifact.** README, threat model, this file
   and the demo script were generated from the code and the run, then checked
   against the rubric.

## 3. What the AI produced

- `contracts/src/QoreGuardTreasury.sol` (~380 lines) and its mocks.
- `contracts/test/*.sol`: 23 tests, all passing.
- `agent/qoreguard/`: configuration, chain wrapper, EVM client, ABI encoder,
  planner, agent, CLI (~1,200 lines).
- `scripts/`: key bootstrap, invoice signing, demo runner.
- All documentation, the threat model and the submission text.

## 4. Where the AI was wrong, and how it was caught

Concrete failures from this build, kept because they are the honest part:

- **Public key ↔ address mismatch.** It first assumed a chain account's `0x`
  form equals the keccak-derived Ethereum address. It does not: QoreChain derives
  the 20-byte identity from `ripemd160(sha256(compressed_pubkey))`. The AI found
  this by comparing `cast wallet address` with the bech32 hex, and switched the
  EVM path to read nonces from the chain instead of guessing locally.
- **Checksummed precompile literals.** Solidity refuses `0x…0A01` with a mixed-case
  checksum error; the docs warn about it, the AI hit it anyway, and now writes
  every precompile address lowercase.
- **A test that passed for the wrong reason.** `vm.prank` was consumed by an
  external *view* call in the same expression, so the test called the contract as
  the test contract and "passed" a different assertion path. Fixed by
  pre-computing every getter before `vm.prank`.
- **A mock that did not bind the message.** The first `MockPQCVerify` accepted any
  signature with a magic byte, which made "an approval for 0.5 QOR cannot pay
  0.9 QOR" untestable. Rewritten to `0x01 || message`, so the mock also binds.

## 5. Human contribution

- Approved the entry, the category and the "no real money, testnet only" rule.
- Chose the business numbers the demo runs on (per-tx caps, daily caps, the
  vendor list, the invoice set) — these are policy, not code.
- Holds the owner mnemonic. The AI can sign with it on testnet because the keys
  are throwaway testnet keys created for this entry; in production the same
  bytes are signed inside QoreX, which the AI cannot open.
- Reviewed the README and the threat model, and decided what to submit.

## 6. Why this is "AI-native" rather than "AI-assisted"

The interesting part is not that an AI wrote code. It is that the design keeps
the AI *out* of the trust path: the agent in the product is an AI whose failure
mode is assumed (stolen key, injected invoice, buggy planner), and the limits
that survive that assumption live on chain, signed by a key the AI never holds.
The build workflow mirrors the product: an AI did the work, a human held the
only keys that can change the rules.
