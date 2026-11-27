# JUDGES.md — five minutes with QoreGuard

Everything below is on the QoreChain **testnet** (`qorechain-diana`). No mainnet,
no real money, no accounts to create. Open explorer links with **Testnet**
selected.

---

## 0:00 — the one-sentence version

An AI agent pays an owner's bills; the chain and a Solidity contract hold every
limit, and the only way to change a limit is a fresh **ML-DSA-87** signature from
the owner that the contract verifies on chain with `pqcVerify`. A stolen
classical key — the Q-Day scenario — changes nothing.

**Track:** Post-Quantum AI Agents · **Repo:** this one · **Video:** `demo/qoreguard-demo.mp4` (2:16)

---

## 0:30 — run the read-only check (this is the fastest way to judge "does it work")

```bash
python verify.py
```

40 checks, no keys, nothing signed, ~60 seconds. It compares the deployed
bytecode to the audited artifact, reads the policy and the spend ledger from the
contract, re-fetches every payment and refusal from the chain, re-verifies the
owner's SIWQ approval against the on-chain key, opens the sealed archive
digests, and **reproduces the refusals now** with `eth_call`:

```
PASS  an over-cap payment reverts (same call, now)     PerVendorTxCapExceeded(200001, 200000) …
PASS  an unregistered vendor is refused (same call, now)  UnknownVendor(99)
PASS  a forged ML-DSA-87 signature cannot raise the cap   BadPqcSignature()
PASS  the owner's Sign-In-With-QoreChain approval verifies
PASS  a tampered or replayed approval is refused
PASS  the fUSD invoice settled to the vendor's account   the vendor holds 500 fUSD
```

`python verify.py --json` gives the same result machine-readably. It also checks
the browser surfaces (the page embeds the policy the chain holds, the precompile
answers, the recorded signature verifies and a changed byte does not), the CBOM
and the DORA/NIS2 reports, and that the recorded Foundry results belong to this
exact contract source.

---

## 1:00 — press something instead of reading (no wallet, no key, no install)

| Page | Press | You should see |
| --- | --- | --- |
| [web/lab.html](https://dorithesky.github.io/qoreguard/web/lab.html) | **Run all seven** | seven `eth_call`s, each refused by the deployed contract with its own error: `PerVendorTxCapExceeded`, `UnknownVendor`, `BadPqcSignature`, `ValueMismatch`, `StaleApprovalNonce`, `NotOwnerAccount`, `BadPqcSignature` |
| web/lab.html | **Verify the recorded signature** | the owner's 215-byte message verifies in your browser against the ML-DSA-87 key the chain holds; **Verify a copy with one byte changed** is refused |
| [web/qorex.html](https://dorithesky.github.io/qoreguard/web/qorex.html) | **Verify the recorded owner signature** | `verifyMessageWithChain` from `@qorechain/qoresign/network`, over the key read from the `pqcKeyStatus` precompile |
| web/qorex.html | **Connect QoreX** | the production signing path: `window.qorex.native.signArbitrary`. Enabled only if a QoreX wallet is installed in your browser |

The pages hold no key and send no transaction, which is why they can only ask.

---

## 1:15 — what is actually on chain (all of it in `docs/EVIDENCE.md`)

| # | Fact | Transaction |
| --- | --- | --- |
| 1 | Treasury deployed inside a one-transaction EVM window | `0x3fc7c4e5c1f3175a10d47d8b4bb997c06d4208de5e9e080ae3c8188ca3114078` |
| 2 | Owner's ML-DSA-87 key registered with proof of possession | `629F6270…5BAF47` |
| 3 | Vendors added through calls the contract verified with `pqcVerify` | `0xc0abd503…`, `0xdc71c2c3…` |
| 4 | Agent linked: `send`+`evm`, 0.6 QOR/tx, 1.5 QOR/day, two allowed functions | `A7769C4F…837049` |
| 5 | 5 QOR payments settled (0.15 + 0.25 + 0.10 + 0.50 + 0.25) | `B662008D…`, `BA1A3BC0…`, `7AB753CF…`, `8C097EC8…`, `0C261DC9…` |
| 6 | Refusals that are transactions, not claims | `9F1478CB…` (vendor per-tx), `6773B4BA…` (the chain's own per-tx limit), `41080E75…` (vendor daily cap) |
| 7 | Owner's classical key alone tries to change the policy → reverted | `0xbae77f13…` |
| 8 | **QoreSeal**: ML-KEM-1024 key registered, 1568 B, read back identical | `A32934FE…602E86C` |
| 9 | **QoreSeal**: 10 signed invoices sealed; archive digest on chain | `33B776A0…538E22` |
| 10 | **QoreSign**: SIWQ approval verified against the on-chain key, then paid above the vendor cap | `0C261DC9…163E6B` |
| 11 | **fUSD**: 500 fUSD invoice paid by the agent itself (`MsgExecuteCosmos`) | `3DAD3DF9…028D08` |
| 12 | **AMM**: the treasury's first swap in pool 2 (fUSD/QOR) | `F41B2712…86FFC56` |

Full hashes and the explorer pattern are in `docs/EVIDENCE.md`.

---

## 2:15 — try to break it

| What you try | What answers | Where |
| --- | --- | --- |
| Pay a vendor more than its per-tx cap | `PerVendorTxCapExceeded(200001, 200000)` | `verify.py`, or `treasury.call` yourself |
| Pay an address that is not a vendor | `UnknownVendor(99)` | same |
| Raise the cap with the owner's ECDSA key and a junk signature | `BadPqcSignature()` | same |
| Approve a payment, then pay a different amount | the SIWQ statement check fails (`SIWQ_STATEMENT_MISMATCH`) and the contract's `approvalNonce` moved on | `agent/qoreguard/approvals.py` |
| Replay an old approval | `SIWQ_NONCE_MISMATCH` / `StaleApprovalNonce` | same |
| Read a sealed invoice | you get an authentication failure without the owner's ML-KEM secret key (`agent/qoreguard/sealing.py`) | `demo/sealed/*.sealed` |
| Use the agent's key after revocation | `linked key is unbound or revoked` (chain) | `docs/EVIDENCE.md` |

The one thing you *cannot* break from here is the chain's own rule: it is
enforced by `x/abstractaccount`, outside every contract this project wrote.

---

## 3:15 — where each scoring criterion lives

| Criterion (weight) | Look at |
| --- | --- |
| **Works as described (25%)** | `python verify.py` (40 checks); the **attack lab** and **QoreX console** above; the dashboard `web/index.html`; `demo/journal.jsonl` (every decision, including refusals); `docs/EVIDENCE.md` |
| **QoreChain usage (25%)** | 4/4 Ship Weeks badges; features: hybrid ML-DSA-87 txs, authenticator lane + spending rules, scoped EVM windows, `pqcVerify`/`pqcKeyStatus` in Solidity, ML-KEM registry, native AMM swap, test-token settlement; kits: SDK, QoreSeal, QoreSign, QoreChain MCP, **QoreScan** (CBOM + DORA/NIS2), QoreX (wallet API + `verifyMessageWithChain`); **51 Foundry tests, 13 invariants, 100 % coverage, 19/19 mutants**; a daily GitHub Actions re-run with no secret |
| **Post-quantum correctness (20%)** | `docs/THREAT-MODEL.md`; the `pqcVerify` gate in `contracts/src/QoreGuardTreasury.sol` (`_verifyOwnerSignature`); `SIWQ require_pq=True`; the Q-Day check transaction |
| **Originality (15%)** | two independent gates (chain rule *and* contract policy) with the owner's ML-DSA key as the only way through; the agent signs intents, the relayer pays fees, the owner's key never leaves home |
| **Clarity & UX (15%)** | this file, `README.md`, `docs/RUBRIC.md`, the single-file chain-derived dashboard, the two static pages, the 2:16 video |

---

## 4:15 — the design in six lines

```
vendor signs invoice ──► agent verifies signature, seals the invoice to the owner (QoreSeal)
                     ──► writes signed intent  ──► MsgExecuteEVM (agent key, relayer pays fee)
chain gate 1: the owner's account rules  (send+evm, 0.6/tx, 1.5/day, 2 functions only)
contract gate 2: vendor allowlist, per-vendor caps, treasury cap, audit events
over a cap? ──► owner signs a SIWQ approval (QoreSign, hybrid) ──► payWithApproval
change the policy? ──► fresh ML-DSA-87 signature ──► pqcVerify(0x…0a01) on chain
```

---

## 5:00 — honest limitations

- Testnet only. The `x/abstractaccount` module and the PQC precompiles are not on
  mainnet today; everything here is on `qorechain-diana`.
- Amounts are test amounts (0.1–0.7 QOR, 500 fUSD). Nothing has value.
- The owner's keys live in `.keys/` (gitignored, mode 600) for the demo. The
  production path is `web/qorex.html` (`window.qorex.native.signArbitrary` +
  `verifyMessageWithChain`); no QoreX wallet was installed in the browser this
  was built in, so the page demonstrates the verification half live and leaves
  the signing buttons disabled, with the recorded signature in
  `demo/qorex-console.json` as the reference.
- The demo also publishes the plaintext of the sealed invoices so you can check
  that opening an envelope returns the same bytes. In production only the
  envelopes exist.
- The planner is deterministic; the optional LLM annotation cannot change an
  action (`AI_USAGE.md`).
