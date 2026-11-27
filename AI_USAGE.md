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
- `contracts/test/*.sol`: 51 tests, 13 invariants `scripts/record_tests.py` records, 100 % coverage, 19/19 mutants killed (the AI wrote the mutants; the suite had to kill them).
- `agent/qoreguard/`: configuration, chain wrapper, EVM client, ABI encoder,
  planner, agent, CLI, plus the three rails — `sealing.py` (QoreSeal),
  `approvals.py` (QoreSign/SIWQ) and `mcpread.py` (QoreChain MCP) (~1,700 lines).
- `verify.py`: 40 read-only checks a judge can run without a key, including the two browser surfaces and the CBOM.
- The QoreX console was first written against `window.qorex.native.signArbitrary`
  on the assumption that a wallet can sign arbitrary bytes. The vendor's own
  `@qorechain/qorex-types` README says otherwise for the current build
  ("`signArbitrary` is refused for now", typed `Promise<never>`), so the page was
  rewritten to detect the wallet, read the account, and report the wallet's
  refusal verbatim instead of claiming a signing path that does not exist. The
  kit's types are what caught it; that is the AI workflow's main check on itself.
- Mutation testing found no gap, but it found a stale build artifact: the last
  mutant's bytecode stayed in `contracts/out` and `verify.py` flagged the
  deployed code as different from the artifact. `scripts/mutate.py` now
  recompiles the restored source before it exits.
- `scripts/`: key bootstrap, invoice signing, demo runners, dashboard, evidence.
- All documentation, the threat model and the submission text.

## 4. Where the AI was wrong, and how it was caught

Concrete failures from this build, kept because they are the honest part:

- **Public key ↔ address mismatch (the expensive one).** It built the whole
  treasury around a Cosmos-native owner account, then discovered on testnet that
  the EVM lane attributes a transaction to the *keccak* address of the signing
  key: the native account's `0x` form read a balance of 95 QOR while its own
  signed EVM transaction was refused with `sender balance < tx cost (0 < …)`.
  The fix was structural, not cosmetic — the owner has to be an `eth_secp256k1`
  (unified) account, and native transactions from it need `sign_hybrid_eth`
  instead of `build_hybrid_tx`. The AI rebuilt the account layer, migrated the
  funded balance, and documented the trap in the README so the next builder does
  not pay for it.
- **Broadcast mode.** `BROADCAST_MODE_BLOCK` is not served by the public REST
  endpoint (`unsupported return type`), so relayed payments are broadcast in
  `sync` mode and the execution result is read back from the delivered
  transaction. Without that, a refused payment inside a `code: 0` transaction
  would look like a success.
- **Checksummed precompile literals.** Solidity refuses `0x…0A01` with a mixed-case
  checksum error; the docs warn about it, the AI hit it anyway, and now writes
  every precompile address lowercase.
- **A test that passed for the wrong reason.** `vm.prank` was consumed by an
  external *view* call in the same expression, so the test called the contract as
  the test contract and "passed" a different assertion path. Fixed by
  pre-computing every getter before `vm.prank`.
- **A gas parameter that hid a real refusal.** The first post-quantum approval
  attempt failed with an out-of-gas message from the EVM lane: the signed
  `gas_limit` has to fit inside the gas the enclosing transaction provisions, and
  a 4.6 KB signature leaves less room than expected. The two retries stay in
  `demo/journal.jsonl` and are labelled as operational retries, not refusals.
- **Two libraries, two key types, one name.** `qorsdk` returns a `PqcKeypair`
  (bytes only) and `qoresign` wants its own `PqKeyPair`; passing the first to
  `sign_siwq` raised `'PqcKeypair' object has no attribute 'algorithm_id'`. The
  fix was to derive the signing key with `qoresign.derive_canonical_pq_key` and
  *assert on chain* that it equals the registered key — which is now also a
  check in `verify.py`.
- **Bytes that differ are not always a mismatch.** The first bytecode check
  failed on a correct deployment: Solidity 0.8.24 substitutes the immutable
  `ownerAccount` into four sites in the runtime code. The check now requires that
  every differing region is exactly 20 bytes and exactly the owner's address —
  stricter than "same length", and truer.
- **A read helper that took a URL.** `fetch_kem_key` wants a REST client, not an
  `httpx.Client`; the first call died with `Request URL is missing an 'http://'`.
  Small, but it is the kind of thing a judge would hit, so the fix went into the
  script rather than into a local workaround.
- **The MCP server speaks stdio, not HTTP.** `qorechain-mcp` is a stdio MCP
  server; the read layer had to open a session, initialize it and call tools
  through it. That took an afternoon of mismatched transports before
  `agent/qoreguard/mcpread.py` worked — and the payoff is that every number in
  the dashboard can be re-read by any MCP client.
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
