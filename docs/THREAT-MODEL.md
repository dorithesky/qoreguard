# Threat model

Scope: one owner account on QoreChain testnet `qorechain-diana`, one agent key,
one relayer, one policy contract, N vendors. Amounts are test QOR with no value.
The model is written so it would still apply to real amounts, because that is
where the design has to hold.

## Assets

1. The owner's balance (the treasury).
2. The policy: who may be paid, how much, per day.
3. The audit trail: which signed invoice each payment settles.
4. The owner's ML-DSA-87 key (never leaves the owner's device in production).

## Adversaries

| # | Adversary | Capability |
| --- | --- | --- |
| A1 | Stolen agent key | Holds the agent's ed25519 key, can sign anything the agent signs |
| A2 | Compromised relayer | Submits transactions, pays fees, sees pending requests |
| A3 | Malicious payee | Can send invoices, can receive money |
| A4 | Invoice forger | Can write any invoice text, cannot sign as a vendor |
| A5 | Classical key recovery (Q-Day) | Holds the owner's secp256k1 key, including the ECDSA half |
| A6 | Buggy or jailbroken agent | Any logic error or prompt injection in the agent process |

## Controls, per adversary

**A1 — stolen agent key.**
The chain, not the agent, decides. The key is linked with `permissions:
["send","evm"]` and a spending rule (`≤ per_tx`, `≤ daily`, `uqor` only). From
chain v3.2.8 a linked key with a value permission also requires a rule that
bounds every denom, which `linkAuthenticatorMsgs` enforces locally before
signing. `allowed_evm_calls` lists exactly two selectors on the treasury
contract, so the key cannot call anything else — not another contract, not a
plain transfer, not `deploy`. Worst case: one day's cap, paid to registered
vendors only. `revoke` is one owner transaction; afterwards the chain answers
`linked key is unbound or revoked`.

**A2 — compromised relayer.**
The relayer's only power is to submit. The authorization is the agent's
signature over `evmAuthSignBytesV2(chain, account, pubkey, to, value, data,
nonce, gasLimit)`, so a relayer cannot change the recipient, the amount, the
calldata or the gas limit, and cannot replay: the nonce is the chain's
per-key counter (`fetchAuthNonce`), bound into the digest. A relayer that drops
the transaction simply achieves nothing.

**A3 — malicious payee.**
Caps are per vendor and per day, and the treasury-wide daily cap bounds the sum.
A payee cannot become a different payee: the registry is owner-signed and binds
id → address → label hash.

**A4 — invoice forger.**
An invoice is only payable if it verifies against the vendor's registered
ed25519 invoice key, and the on-chain memo is a commitment
(`qg:v1:<sha256 prefix>`) to the exact invoice that was signed. `INV-1008` in the
demo is a tampered invoice: it is refused by the agent before any transaction is
built.

**A5 — Q-Day (classical key recovery).**
This is the reason the project exists. With the owner's secp256k1 key an attacker
can:

- sign native transactions? No — the native lane requires a hybrid signature, and
  the attacker has no ML-DSA-87 key.
- open an EVM window? No — `MsgOpenEVMWindow` is a native-lane message, so it
  carries the hybrid signature.
- change the policy? No — `_requireOwnerSignature` checks the registered
  ML-DSA-87 public key (`pqcKeyStatus`, including `possessionProven`) and then
  `pqcVerify` over a message that binds domain, chain id, contract, action,
  payload and a monotonic nonce. A classical key produces no such signature.
- pay through the agent path? The agent's own key is separate; revoking it is a
  hybrid-signed owner transaction.

The residual risk is the bridge between the two worlds: a signature the owner
*would* have made is still valid, which is why every admin signature is bound to
a nonce that the chain consumes.

**A6 — buggy agent.**
The agent cannot exceed the caps even if it tries: the demo deliberately walks it
into three refusals (vendor cap, chain per-tx cap, treasury daily cap) and shows
the chain's own words for each. The planner only proposes; it never enforces.

**A7 — the same bill paid twice.** A crash and retry, two agent processes racing,
a second machine with its own journal, or an app restart can all ask for the same
invoice again. The contract answers: `paidInvoices[invoiceKey]` is written with
the transfer and checked by `pay` and `payWithApproval` before any cap or
signature, so the second attempt reverts `InvoiceAlreadyPaid` and no value leaves
the owner's account. The agent's journal skip is a convenience on top of that, not
the guarantee. The key is stable (`qg-inv:v1:<vendorId>:<invoiceId>`) and is not
the payment's signed-intent memo, which changes every attempt by design.

## What is deliberately *not* protected

- **The owner's device.** In production the ML-DSA-87 key sits in QoreX's Secure
  Enclave behind biometrics. In this demo it is derived from a throwaway mnemonic
  in `.keys/keys.env`; a real deployment would sign the same bytes inside QoreX.
- **Vendor key compromise.** A vendor whose invoice key leaks can issue invoices
  up to its caps. Caps and the daily treasury cap bound that.
- **Privacy.** Amounts and payees are public on chain, like any on-chain
  treasury. The memo carries only a hash, not the invoice text.
- **Mainnet.** The `x/abstractaccount` module is switched off on mainnet and no
  PQC precompile is active there; this design is explicitly testnet-only today.

## Invariants a reviewer can check in the tests

1. No caller other than the owner account can reach `pay` / `payWithApproval`.
2. Every cap is checked before the value moves, and the state changes before the
   external call (no reentrancy window).
3. `msg.value` must equal `amount * 1e12`; a mismatch is refused before any
   bookkeeping.
4. Every administrative call needs: a registered key, a proven key, the *same*
   key bytes, a valid ML-DSA-87 signature over the exact message, and the current
   nonce.
5. An approval for one (vendor, amount, memo, nonce) cannot pay another.
6. An inactive precompile (empty return data) is refused, never read as
   "registered".
7. No invoice key ever settles twice, on either lane, however often it is
   replayed (`invariant_invoiceIsSettledAtMostOnce`).
