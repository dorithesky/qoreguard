# Evidence — what the testnet actually recorded

Assembled 2026-11-28 from `scripts/run_guard.py`, `scripts/run_p1.py`,
`scripts/make_receipts.py` and `scripts/render_dashboard.py`, and re-checked by
`python verify.py` (45 read-only checks). Network `qorechain-diana`, EVM chain id
9800. Every hash below opens at https://explore.qore.network/tx/<hash> with the
explorer switched to **Testnet**.

There are two deployments, and the difference between them is the point:

| | Contract | Deployed | What it settled |
| --- | --- | --- | --- |
| **Current** | [`0x3e9f072ffb0dc2cc07e4d5829a9f152556020a4a`](https://explore.qore.network/address/0x3e9f072ffb0dc2cc07e4d5829a9f152556020a4a) | tx [`0x0f9bdd45…`](https://explore.qore.network/tx/0x0f9bdd45c235773f929d088c5fa12b690870258967a70cc0901dab571af8d65c), block 10876112 | INV-1014, INV-1015 (one payment each), the duplicate matrix below, INV-1016 in fUSD |
| First run (superseded) | [`0xa437ca5360214d4225ab3323da8f72db99250d92`](https://explore.qore.network/address/0xa437ca5360214d4225ab3323da8f72db99250d92) | tx [`0x3fc7c4e5…`](https://explore.qore.network/tx/0x3fc7c4e5c1f3175a10d47d8b4bb997c06d4208de5e9e080ae3c8188ca3114078), block 10791349 | INV-1001…INV-1012 and the 500 fUSD INV-1013 |

The current contract adds one thing the first one did not have: **a settlement
record for every invoice** (`mapping(bytes32 => bool) paidInvoices`), consulted by
`pay` and `payWithApproval` alike. The first deployment stays on chain as the
history of the first run; its payments are labelled `earlier` in the dashboard.

## Current deployment — the guarded contract

- address: `0x3e9f072ffb0dc2cc07e4d5829a9f152556020a4a`
- deploy tx: `0x0f9bdd45c235773f929d088c5fa12b690870258967a70cc0901dab571af8d65c` (block 10876112, gas 3000000)
- treasury daily cap (uqor): 1500000
- spent today (uqor): 650000
- total paid (uqor): 650000
- settled invoices: **2** (`settledInvoiceCount`)
- PQ approval nonce: 4
- vendors: 2

| id | payee | per-tx cap | daily cap | active |
| ---: | --- | ---: | ---: | :---: |
| 1 | `0x305e5935929844beca43ab6376226a7916a2d129` | 200000 | 1000000 | yes |
| 2 | `0xdb4a4925451fbca4183ea435ef29e4ef24fcf07d` | 300000 | 400000 | yes |

### Owner actions on the guarded contract

| action | tx | note |
| --- | --- | --- |
| deploy | [`0x0f9bdd45…`](https://explore.qore.network/tx/0x0f9bdd45c235773f929d088c5fa12b690870258967a70cc0901dab571af8d65c) | inside a 1-tx EVM window (2 000 000 uqor budget) |
| add-vendor 1 | [`0x58fbee5a…`](https://explore.qore.network/tx/0x58fbee5ab6cbbe136729a5fdf89be4549b1111a03d78ecf744b9dfdcba474914) | ML-DSA-87 signature checked by `pqcVerify`, nonce 0 |
| add-vendor 2 | [`0x09112478…`](https://explore.qore.network/tx/0x09112478881b27fb7f92b58c986a2c61bf8cba14489a98a0cd11dca911a61b11) | nonce 1 |
| set-vendor-caps 2 | [`0xd507ce1e…`](https://explore.qore.network/tx/0xd507ce1eda191de3d4f080a718a7ea0519e1951897ab87c071f099ea7fbf1abf) | daily cap 0.4 QOR, the same owner decision the first deployment carries |
| link-agent-v3 | [`394B27C1…`](https://explore.qore.network/tx/394B27C17B624DE3F9318C763A25446B02AB5C92F6F964C597E91937E0052488) | ed25519 agent key, send+evm, 600000 uqor/tx, 1500000 uqor/day, allowed calls = `pay` + `payWithApproval` on this contract only, denoms uqor + fUSD |

### One invoice, one payment — six attempts, two deposits

Two invoices the agent had never paid were each requested twice on each lane.
The first payment of INV-1014 came from a separate process running the ordinary
agent loop (`python -m qoreguard.cli run --invoice INV-1014`); a second process
of the same command skipped it from the journal ("already paid"); the repeated
attempts below came from a process with no journal of its own, so only the
contract could stop them.

| # | attempt | invoice | amount | result | tx |
| ---: | --- | --- | ---: | --- | --- |
| 1 | agent lane (`pay`) | INV-1014 | 0.15 QOR | **PAID** | [`6CC8C34E…`](https://explore.qore.network/tx/6CC8C34E652BE8ED1331E929B9C81FCC8D2D44950EDC0DF568C893080B8F3234) |
| 2 | the same invoice again, no journal | INV-1014 | 0.15 QOR | REFUSED `InvoiceAlreadyPaid(0xd87b33ed…)` | [`874E13F4…`](https://explore.qore.network/tx/874E13F40A0534E60D6D824B46E5B211813D83AB0CB9BD2F132C9D8D7F8F62FD) |
| 3 | approval lane (`payWithApproval`, owner ML-DSA-87) | INV-1015 | 0.50 QOR | **PAID** (above the 0.2 QOR vendor cap) | [`C878A5AC…`](https://explore.qore.network/tx/C878A5ACD52521B31652AFF980288C090F309D325D2BBE4013868432B515E9E3) |
| 4 | agent lane after the approval | INV-1015 | 0.50 QOR | REFUSED `InvoiceAlreadyPaid(0x07b40b91…)` | [`7E7D9E03…`](https://explore.qore.network/tx/7E7D9E037DE441BBDF291235D8C423DD2F2A94A71CC48C94283F99D85A37EF3A) |
| 5 | a second, freshly signed owner approval | INV-1015 | 0.50 QOR | REFUSED `InvoiceAlreadyPaid(0x07b40b91…)` | [`FC02AADC…`](https://explore.qore.network/tx/FC02AADC7FFAABBF9F7BEEF446F9C7E24BD43F7BAAB9437BD94ADA3C181A6B66) |
| 6 | approval lane for the invoice the agent lane paid | INV-1014 | 0.15 QOR | REFUSED `InvoiceAlreadyPaid(0xd87b33ed…)` | [`17394DAB…`](https://explore.qore.network/tx/17394DAB7D0B80D825F6299143357C24B0BB5F21027A5A9CBEF4F4C69A88A458) |

The value that reached the vendor's account, read back from the bank events:

| invoice | settlements on this contract | moved to `0x305e…d129` |
| --- | ---: | ---: |
| INV-1014 | 1 | 150000 uqor |
| INV-1015 | 1 | 500000 uqor |

The invoice key is `keccak256("qg-inv:v1:<vendorId>:<invoiceId>")` — stable across
processes and lanes, and different from the payment's own signed-intent memo
(which changes every attempt). The guard is checked after the vendor and active
checks and **before** the caps and the owner signature, so the refusal is always
`InvoiceAlreadyPaid`, never a cap or a signature error.

### The token invoice, through the ordinary run

INV-1016 is a 5 fUSD invoice. `python -m qoreguard.cli run --invoice INV-1016`
plans it on the **native token lane** (the QOR contract cannot forward an ERC-20
denom), pays it with `MsgExecuteCosmos`, and prints a receipt in fUSD:

| invoice | amount | lane | tx | vendor fUSD before → after |
| --- | ---: | --- | --- | --- |
| INV-1016 | 5 fUSD | native token (`MsgExecuteCosmos`) | [`90A4F700…`](https://explore.qore.network/tx/90A4F7003F4E6CC653E94006EAC6454D5F5376959ED9B481FAAAF125F234DBAC) | 500 → 505 |

### Receipts — the invoice, the payment and the document agree

`scripts/make_receipts.py` derives one receipt per settled invoice from the
signed invoice and the journal; `demo/receipts.json` and `demo/receipts/<ID>.txt`
hold them, and the dashboard shows the same table.

| invoice | invoice says | chain paid | lane | receipt |
| --- | ---: | ---: | --- | :---: |
| INV-1001 | 0.150 QOR | 0.150 QOR | treasury contract | agrees |
| INV-1004 | 0.250 QOR | 0.250 QOR | treasury contract | agrees |
| INV-1006 | 0.100 QOR | 0.100 QOR | treasury contract | agrees |
| INV-1002 | 0.500 QOR | 0.500 QOR | treasury contract (owner approval) | agrees |
| INV-1012 | 0.250 QOR | 0.250 QOR | treasury contract (owner approval) | agrees |
| **INV-1013** | **500 fUSD** | **500 fUSD** | native token | agrees |
| INV-1014 | 0.150 QOR | 0.150 QOR | treasury contract | agrees |
| INV-1015 | 0.500 QOR | 0.500 QOR | treasury contract (owner approval) | agrees |
| INV-1016 | 5 fUSD | 5 fUSD | native token | agrees |

The INV-1013 row is the currency defect corrected: the signed invoice says
`amount_uqor: 500` with `denom: erc20:0x97143B77…`, the chain paid 500 of that
denom, and every surface now prints **500 fUSD**. Before the fix the dashboard's
invoice list formatted every invoice as `amount/1e6` QOR and showed
`0.001 QOR` for this one — a number that never existed on chain.

## First deployment — the first run (superseded)

- address: `0xa437ca5360214d4225ab3323da8f72db99250d92`
- deploy tx: `0x3fc7c4e5c1f3175a10d47d8b4bb997c06d4208de5e9e080ae3c8188ca3114078` (block 10791349)
- treasury daily cap (uqor): 1500000 · total paid: 1250000 · PQ approval nonce: 6
- the owner's ML-DSA-87 key was registered, the EVM window used, and the agent key linked in this run (see below)

| invoice | vendor | amount | result | tx |
| --- | --- | ---: | --- | --- |
| INV-1001 | cloud-hosting | 0.150 QOR | PAID | [`B662008D78AC5001…`](https://explore.qore.network/tx/B662008D78AC5001DE9B486A878155C00D9D36B9AA974BEF7C88407BAAD93F68) |
| INV-1002 | cloud-hosting | 0.500 QOR | REFUSED — `PerVendorTxCapExceeded(500000, 200000)` | [`9F1478CBF66DD6AF…`](https://explore.qore.network/tx/9F1478CBF66DD6AF35EA3C15F0251632F0216670E77BB98363145BDBC00C109A) |
| INV-1003 | api-credits | 0.700 QOR | REFUSED — chain per-tx limit 600000 uqor | [`6773B4BA5F141CC7…`](https://explore.qore.network/tx/6773B4BA5F141CC7D550A88BFAA919AB8B1DC2E77A50B67A7B00479EBAA78E15) |
| INV-1004 | api-credits | 0.250 QOR | PAID | [`BA1A3BC0A8A19B5A…`](https://explore.qore.network/tx/BA1A3BC0A8A19B5A8504EED42E8F2FD3A814B89D3E0EFE1DBB48FDC329E13A8E) |
| INV-1006 | cloud-hosting | 0.100 QOR | PAID | [`7AB753CFC7955408…`](https://explore.qore.network/tx/7AB753CFC7955408310774EB101F662CFF5CE8E6E0E4624692D0F899E72C9EB4) |
| INV-1007 | api-credits | 0.400 QOR | REFUSED — `PerVendorTxCapExceeded(400000, 300000)` | [`D75DAA14F807906B…`](https://explore.qore.network/tx/D75DAA14F807906B572497DAD11C67084CDC0491E385C81A74C579187E9301F1) |
| INV-1002 | cloud-hosting | 0.500 QOR | REFUSED — gas floor (operator retry) | [`991780CBF3E2DBAF…`](https://explore.qore.network/tx/991780CBF3E2DBAFFD77EBDD6F1AF368A35EF31B1AF3A28AA7CFB1B182A03368) |
| INV-1002 | cloud-hosting | 0.500 QOR | REFUSED — gas provisioning (operator retry) | [`E2395F002F734FA4…`](https://explore.qore.network/tx/E2395F002F734FA4FCCEB478F7231968033D68DAEAB5E02F2A3BA57347BB85AA) |
| INV-1002 | cloud-hosting | 0.500 QOR | PAID — owner approval | [`8C097EC88F2DF0A4…`](https://explore.qore.network/tx/8C097EC88F2DF0A4F14E7DAC0DA4574D340ED52AFDF4025FDB193CD153286F2D) |
| INV-1009 | api-credits | 0.200 QOR | REFUSED — `PerVendorDayCapExceeded(450000, 400000)` | [`41080E7596C8FDBC…`](https://explore.qore.network/tx/41080E7596C8FDBC24D7858CD2D167AFE8C15A658AE1A619DC77E4FF7E4FF346) |
| INV-1010 | cloud-hosting | 0.100 QOR | REFUSED — linked key revoked | [`3ACCA201DCD97BFF…`](https://explore.qore.network/tx/3ACCA201DCD97BFF15D334E5B8153F998493D4D5EA9381075DF0067AF702411C) |
| INV-1012 | cloud-hosting | 0.250 QOR | PAID — owner approval | [`0C261DC914A9CE20…`](https://explore.qore.network/tx/0C261DC914A9CE20D2E9DE877BCB3552FB195921AA1ACDDF274C705622163E6B) |
| INV-1013 | cloud-hosting | **500 fUSD** | PAID — native token lane | [`3DAD3DF948B61ED8…`](https://explore.qore.network/tx/3DAD3DF948B61ED87642BC476C6F79DD66A0E1F182405510EF9D1696FC028D08) |

## Accounts

| Role | bech32 | 0x form | test QOR now |
| --- | --- | --- | ---: |
| owner (eth-native) | `qor1qj002t0yrrwud08geqww0r5f56tgu09rqjxqcd` | `0x049Ef52DE418DDC6Bce8c81ce78E89A6968e3Ca3` | 50.458 |
| relayer (native) | `qor1zcsp7xup7y08czmc9z563x0qk3q6sm5827ftf8` | `0x16201f1b81f11e7c0b7828a9a899e0b441a86e87` | 1.065 |
| vendor 1 payee | `0x305e5935929844bECA43aB6376226a7916A2d129` | `0x305e5935929844bECA43aB6376226a7916A2d129` | 1.500 |
| vendor 1 token account | `qor1vd3kshzcvqkr238arazyelvjurm87mnjlr02d0` | — | 505 fUSD |
| vendor 2 payee | `0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D` | `0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D` | 0.750 |

## Post-quantum keys

- owner ML-DSA-87 public key registered: **True**, 2592 bytes, possession proven
- owner ML-KEM-1024 key registered: **True**, 1568 bytes (`mlkem1024`, id 2), height 10848121, tx [`A32934FE…`](https://explore.qore.network/tx/A32934FED596CE21DB3B6E4C6CFCEA81F1B2F57E76B46117A19EFCE64602E86C)
- canonical derivation: `shake256("qorechain:pqc:v1|" + address + "|" + mnemonic)`

## The agent's on-chain limits (current)

- rule `qoreguard-agent-v3`: per_tx 600000 uqor, daily 1500000 uqor, denoms `['uqor', 'erc20:0x97143B771D34ddb9130b807b135Ae675A5a963CA']`
  - allowed EVM call: `0x3e9f072ffb0dc2cc07e4d5829a9f152556020a4a/0x62bc84af` (`pay`)
  - allowed EVM call: `0x3e9f072ffb0dc2cc07e4d5829a9f152556020a4a/0xd98d5dc8` (`payWithApproval`)
- linked key nonce 11, label `qoreguard-agent`, permissions `['send', 'evm']`

## The other rails (first run)

| Rail | What the chain holds | Value | tx |
| --- | --- | --- | --- |
| Sealed archive | 10 signed invoices encrypted to the owner's ML-KEM-1024 key; only the archive digest is public | `qg:seal-archive:v1:19527ac1f4b4741a3af5816c3584318be8830d29` (memo read back: True) | [`33B776A02C703158…`](https://explore.qore.network/tx/33B776A02C703158B1F1462E68F301C9BA774E26B4BE08B24D51CA7629538E22) |
| AMM swap | native pool 2 (fUSD/QOR), 100000 uqor in, minOut 1 | 881 fUSD out | [`F41B2712A4E0FD0E…`](https://explore.qore.network/tx/F41B2712A4E0FD0EE2EEB64D3E4B74276F63EDAB3BD1BA8FC5FA4CDDA86FFC56) |
| QoreSign approval | SIWQ message verified against the on-chain ML-DSA-87 key, then `payWithApproval` settled 250000 uqor above the 200000 uqor cap (approval nonce 5) | negatives: amount changed / replayed nonce refused | [`0C261DC914A9CE20…`](https://explore.qore.network/tx/0C261DC914A9CE20D2E9DE877BCB3552FB195921AA1ACDDF274C705622163E6B) |
| fUSD settlement | INV-1013 paid on the native lane (`MsgExecuteCosmos`) to `qor1vd3…` | vendor fUSD after: 500 | [`3DAD3DF948B61ED8…`](https://explore.qore.network/tx/3DAD3DF948B61ED87642BC476C6F79DD66A0E1F182405510EF9D1696FC028D08) |

### The owner's approval, as the agent verified it

```
qoreguard.approval wants you to sign in with your QoreChain account:
qor1qj002t0yrrwud08geqww0r5f56tgu09rqjxqcd

Approve INV-1012: pay vendor 1 (cloud-hosting) 250000 uqor, treasury approval nonce 5

URI: qorechain:qoreguard.approval
Version: 1
Chain ID: qorechain-diana
Nonce: apprdcdc8509e248
Issued At: 2026-11-27T12:13:18Z
Expiration Time: 2026-11-27T12:28:18Z
```

Signed hybrid (secp256k1 + ML-DSA-87) and verified with
`qoresign.verify_siwq(..., require_pq=True)` against the ML-DSA-87 key registered
for the owner account on chain. A classical-only signature is refused with
`PQ_LAYER_MISSING`.

## Verifying a payment yourself

```bash
    curl -s https://api-testnet.qore.host/cosmos/tx/v1beta1/txs/<hash> | jq .tx_response.code
```

A relayed payment is a `MsgExecuteEVM`; the transaction is signed hybrid by the
relayer, and the payment inside is authorised by the agent's ed25519 signature
over `evmAuthSignBytesV2`. A refused payment keeps transaction `code: 0` and
carries `abstractaccount_execution_failed` with the contract's reason — that is
the refusal, not a failed transaction. A duplicate is one of those: the
transaction is mined, the EVM execution reverts with `InvoiceAlreadyPaid`, and
no value leaves the owner's account.

Everything in this file is re-checked by `python verify.py` (read-only, no keys,
45 checks): bytecode against the artifact, the policy, the spend ledger of both
deployments, every payment and refusal, the invoice settlement record and a live
`eth_call` that the settled invoice is refused on both lanes, every receipt, the
token balances, the approval, the archive digest, and that the dashboard prints
the token invoice in fUSD.
