# How QoreGuard maps to the jury rubric

The fest scores every project on five criteria (25 / 25 / 20 / 15 / 15). This
page points each one at something a reviewer can check in under a minute.

## 1. Works as described — 25%

| Claim | Where it happens |
| --- | --- |
| The agent pays registered vendors inside the caps | 4 payments in `docs/EVIDENCE.md`, vendor balances on chain (1.25 / 0.75 QOR) |
| Caps are enforced by the chain and the contract | 5 refusals with the exact revert/chain reason |
| The policy can only change with the owner's ML-DSA-87 signature | 8 admin actions, each an `addVendor`/`setVendorCaps`/`setTreasuryCap` call carrying a 4,627-byte signature |
| An over-cap payment is possible only with a fresh owner signature | `payWithApproval` tx `8C097EC8…` |
| Revocation works | `linked key is unbound or revoked` after `MsgRevokeAuthenticator` |
| The owner approves an over-cap payment from their own device | SIWQ message verified against the on-chain ML-DSA-87 key, then `payWithApproval` tx `0C261DC9…` |
| The agent pays a stablecoin invoice itself | `MsgExecuteCosmos` of 500 fUSD, tx `3DAD3DF9…`; the vendor's account holds it |
| Invoices are archived encrypted | 10 envelopes sealed to the owner's ML-KEM-1024 key, archive digest on chain (`33B776A0…`) |

Reproduce: **`python verify.py`** (23 read-only checks against the live chain, no
keys needed), `scripts/local_integration.sh` (12 checks, no network) and
`python -m qoreguard.cli run`.

## 2. QoreChain technology used, verified in repo and on chain — 25%

| Feature | Evidence |
| --- | --- |
| Hybrid ML-DSA-87 transactions | `pqc_hybrid_verify` event on every owner/relayer tx; `sign_hybrid_eth` / `build_hybrid_tx` in `agent/qoreguard/chain.py` |
| Authenticator lane | `MsgRegisterAuthenticator` + `MsgUpdateSpendingRules` (`link` tx `A7769C4F…`), `MsgRevokeAuthenticator` (`6A2AD433…`) |
| Chain-enforced spending rule | `qorechain/abstractaccount/v1/accounts/<owner>/spending_rules` shows 0.6/tx, 1.5/day, `uqor`, two `allowed_evm_calls` |
| Scoped EVM window | three `MsgOpenEVMWindow` txs, one of them with `allowed_targets = [treasury]` only |
| Post-quantum precompiles | `QoreGuardTreasury.sol` calls `pqcKeyStatus` (`0x…0a02`) and `pqcVerify` (`0x…0a01`), with the empty-return-data trap handled |
| Multi-message transaction | `link` sends the rule and the registration in one owner-signed transaction |
| ML-KEM-1024 key registry | `msg.pqc.register_kem_key` (`A32934FE…`), key read back byte-identical; the archive is sealed to it |
| Native AMM | `msg.amm.swap_exact_in` on pool 2 (`F41B2712…`) — the treasury's fUSD came from the pool |
| Test tokens (fUSD) | a token invoice settled through the authenticator lane; the rule's `allowed_denoms` lists the denom |
| QoreChain MCP | `agent/qoreguard/mcpread.py` reads treasury views and accounts through `qorechain-mcp` over stdio |
| Sign In With QoreChain | `agent/qoreguard/approvals.py`, verified with `verify_siwq(require_pq=True)` against the on-chain key |

Kits: `qorechain-sdk`, `qorechain-qoreseal`, `qorechain-qoresign`,
`qorechain-mcp`, plus Foundry for the contract. Ship Weeks: 4/4 badges.

## 3. Post-quantum correctness and security — 20%

- The owner's key is the **canonical derivation** (`shake256("qorechain:pqc:v1|" + address + "|" + mnemonic)`), registered with a **proof of possession**.
- Every admin signature binds `domain ‖ chain id ‖ contract ‖ action ‖ payloadHash ‖ nonce`; the nonce is monotonic on chain, so a signature is single-use here and on any other deployment.
- The contract refuses: an unregistered key, an unproven key, a different key, a wrong signature, a stale nonce, a signature reused for another amount, and an inactive precompile.
- The agent's authorization is `evmAuthSignBytesV2`, which binds chain, account, key, target, value, calldata, nonce **and gas limit**.
- `docs/THREAT-MODEL.md` walks six adversaries, including the Q-Day case, and names what is *not* protected.
- The Q-Day check on chain: the owner's classical key submits `setTreasuryCap(999999999)` with a forged signature; the transaction is mined and reverts; the cap is still 1.5 QOR.

## 4. Originality — 15%

The examples suggest a DCA agent or a bill payer. QoreGuard's difference is the
**split of trust**: the hot AI agent is assumed to be compromisable, so nothing it
can reach is a policy; and the human's lever is a post-quantum signature the
chain verifies inside the contract, not an API key. Two smaller ideas follow from
it: invoices are **authenticated messages** whose exact signed bytes are
committed to in the payment memo, and the agent's key is restricted to **two
function selectors** on one contract, so even a stolen key cannot transfer funds
or touch another protocol.

## 5. Clarity, UX, documentation — 15%

- `README.md`: architecture, the two gates, what is on chain, quickstart, two
  builder traps found the hard way, honest limitations.
- `web/index.html`: a dashboard generated from the chain (vendors, caps, spend
  bars, ledger, refusals, PQ-verified admin events). `python scripts/render_dashboard.py`.
- `demo/qoreguard-demo.mp4`: 2:08 captioned walkthrough, every number copied from
  the deployment record and the journal.
- `docs/EVIDENCE.md`: generated from the chain, one row per transaction.
- `docs/JUDGES.md`: the five-minute tour — run `verify.py`, read what is on
  chain, then try to break it.
- `docs/SUBMISSION.md`: paste-ready submission text.
