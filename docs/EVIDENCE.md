# Evidence — what the testnet actually recorded

Generated 2026-11-27 12:18 UTC from `scripts/collect_evidence.py`. Network `qorechain-diana`, EVM chain id 9800. Every hash below can be opened at https://explore.qore.network/tx/<hash> with the explorer switched to **Testnet**.

## Accounts

| Role | bech32 | 0x form | test QOR now |
| --- | --- | --- | ---: |
| owner (eth-native) | `qor1qj002t0yrrwud08geqww0r5f56tgu09rqjxqcd` | `0x049Ef52DE418DDC6Bce8c81ce78E89A6968e3Ca3` | 50.458 |
| relayer (native) | `qor1zcsp7xup7y08czmc9z563x0qk3q6sm5827ftf8` | `0x16201f1b81f11e7c0b7828a9a899e0b441a86e87` | 1.065 |
| vendor 1 payee | `0x305e5935929844bECA43aB6376226a7916A2d129` | `0x305e5935929844bECA43aB6376226a7916A2d129` | 1.500 |
| vendor 2 payee | `0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D` | `0xdB4a4925451FbCa4183Ea435ef29E4EF24fCF07D` | 0.750 |

## Post-quantum keys

- owner ML-DSA-87 public key registered: **True**, 2592 bytes
- canonical derivation: `shake256("qorechain:pqc:v1|" + address + "|" + mnemonic)`
- public key sha256: `66ace6ecccfb85cadd3715feb99c2a8e4965b283ae81021712ef59b4971fcc2c`

## EVM window

- live: **False**
- opened at height 10791562, expires at 10791862 (0 blocks left)
- transactions: 1 used of 1; value: 108000 of 150000 uqor used
- allowed targets: ['0xa437ca5360214d4225ab3323da8f72db99250d92']

## The agent's on-chain limits

- rule `qoreguard-agent-v2`: per_tx 600000 uqor, daily 1500000 uqor, denoms ['uqor', 'erc20:0x97143B771D34ddb9130b807b135Ae675A5a963CA']
  - allowed EVM call: `0xa437ca5360214d4225ab3323da8f72db99250d92/0x62bc84af`
  - allowed EVM call: `0xa437ca5360214d4225ab3323da8f72db99250d92/0xd98d5dc8`

## Owner actions on chain

| action | by | tx | note |
| --- | --- | --- | --- |
| register-pqc | owner | [`629F6270248301C829…`](https://explore.qore.network/tx/629F6270248301C82932D03154B16AD607CE97A425B35D81B7C107A6755BAF47) | classical bootstrap tx (eth_secp256k1), ML-DSA-87 key + proof of possession |
| window-open | owner | [`63EB65A51265FB8647…`](https://explore.qore.network/tx/63EB65A51265FB8647613D8EE88B8EE5E1C17E0492D53911DC25A9FB0C36C888) | deployment window: 1 tx, 1.5 QOR value+fees, 400 blocks |
| deploy | owner | [`0x3fc7c4e5c1f3175a…`](https://explore.qore.network/tx/0x3fc7c4e5c1f3175a10d47d8b4bb997c06d4208de5e9e080ae3c8188ca3114078) | QoreGuardTreasury at 0xa437ca5360214d4225ab3323da8f72db99250d92 |
| window-open | owner | [`39A57D6DABD11AFE6A…`](https://explore.qore.network/tx/39A57D6DABD11AFE6AECB867932E8BCD6EB33D857193507C447028AAB15035FE) | admin window: 3 tx, target list = [treasury] only |
| add-vendor | owner | [`0xc0abd503f0f1021c…`](https://explore.qore.network/tx/0xc0abd503f0f1021c8942991727d39fd0f1143c3edea87eb32efb62736a218002) | vendor 1 cloud-hosting, ML-DSA-87 signature verified by pqcVerify |
| add-vendor | owner | [`0xdc71c2c388525c7c…`](https://explore.qore.network/tx/0xdc71c2c388525c7c84732f9b333e8fd05a01ce94f0df2db7a3cd132b2a4f8ce3) | vendor 2 api-credits, ML-DSA-87 signature verified by pqcVerify |
| set-treasury-cap | owner | [`0x4dfc37f3dc55a9bc…`](https://explore.qore.network/tx/0x4dfc37f3dc55a9bc0d4516cc098b4897be642385368c9fef7bab8f6b69ad180c) | 1.5 QOR per day, PQ-signed |
| link-agent | owner | [`A7769C4FAA7351B011…`](https://explore.qore.network/tx/A7769C4FAA7351B0116679E177B8D06263189C152A0B4F203F5614773D837049) | ed25519 agent key, permissions send+evm, 0.6/tx, 1.5/day, allowed calls = pay + payWithApproval |
| set-vendor-caps | owner | [`0xac48abfb3669f137…`](https://explore.qore.network/tx/0xac48abfb3669f13760f3f4a6c585dcac1b5e8c4344e56ce1a1ecb2bed732d752) | vendor 2 daily cap lowered to 0.4 QOR, PQ-signed |
| revoke-agent | owner | [`6A2AD433CE15338697…`](https://explore.qore.network/tx/6A2AD433CE1533869796159C4DE8816339670D3F2BDFEFC444534A28C7158BFB) | agent key unbound |
| qday-check | owner-classical-key | [`0xbae77f139b1d67f5…`](https://explore.qore.network/tx/0xbae77f139b1d67f52767a41212e9584d2058d0dce7985b01e9aba7be25bbba59) | setTreasuryCap(999999999) with a forged PQ signature: mined, reverted, policy unchanged |

## Treasury contract

- address: `0xa437ca5360214d4225ab3323da8f72db99250d92`
- deploy tx: `0x3fc7c4e5c1f3175a10d47d8b4bb997c06d4208de5e9e080ae3c8188ca3114078` (block 10791349)
- treasury daily cap (uqor): 1500000
- spent today (uqor): 250000
- total paid (uqor): 1250000
- PQ approval nonce: 6
- vendors: 2

## Vendors

| id | payee | per-tx cap | daily cap | active |
| ---: | --- | ---: | ---: | :---: |
| 1 | `0x305e5935929844beca43ab6376226a7916a2d129` | 200000 | 1000000 | yes |
| 2 | `0xdb4a4925451fbca4183ea435ef29e4ef24fcf07d` | 300000 | 400000 | yes |

## Transactions

| invoice | vendor | amount (QOR) | result | tx |
| --- | --- | ---: | --- | --- |
| INV-1001 | cloud-hosting | 0.150 | PAID | [`B662008D78AC5001…`](https://explore.qore.network/tx/B662008D78AC5001DE9B486A878155C00D9D36B9AA974BEF7C88407BAAD93F68) |
| INV-1002 | cloud-hosting | 0.500 | REFUSED — PerVendorTxCapExceeded(500000, 200000) | [`9F1478CBF66DD6AF…`](https://explore.qore.network/tx/9F1478CBF66DD6AF35EA3C15F0251632F0216670E77BB98363145BDBC00C109A) |
| INV-1003 | api-credits | 0.700 | REFUSED — per-tx limit 600000uqor exceeded (attempted 700000): spending limit exceeded | [`6773B4BA5F141CC7…`](https://explore.qore.network/tx/6773B4BA5F141CC7D550A88BFAA919AB8B1DC2E77A50B67A7B00479EBAA78E15) |
| INV-1004 | api-credits | 0.250 | PAID | [`BA1A3BC0A8A19B5A…`](https://explore.qore.network/tx/BA1A3BC0A8A19B5A8504EED42E8F2FD3A814B89D3E0EFE1DBB48FDC329E13A8E) |
| INV-1006 | cloud-hosting | 0.100 | PAID | [`7AB753CFC7955408…`](https://explore.qore.network/tx/7AB753CFC7955408310774EB101F662CFF5CE8E6E0E4624692D0F899E72C9EB4) |
| INV-1007 | api-credits | 0.400 | REFUSED — PerVendorTxCapExceeded(400000, 300000) | [`D75DAA14F807906B…`](https://explore.qore.network/tx/D75DAA14F807906B572497DAD11C67084CDC0491E385C81A74C579187E9301F1) |
| INV-1002 | cloud-hosting | 0.500 | REFUSED — insufficient gas for floor data gas cost: have 300000, want 312790 | [`991780CBF3E2DBAF…`](https://explore.qore.network/tx/991780CBF3E2DBAFFD77EBDD6F1AF368A35EF31B1AF3A28AA7CFB1B182A03368) |
| INV-1002 | cloud-hosting | 0.500 | REFUSED — tx rejected: failed to execute message; message index: 0: the transaction provisions 354824 gas for the call, less than  | [`E2395F002F734FA4…`](https://explore.qore.network/tx/E2395F002F734FA4FCCEB478F7231968033D68DAEAB5E02F2A3BA57347BB85AA) |
| INV-1002 | cloud-hosting | 0.500 | PAID | [`8C097EC88F2DF0A4…`](https://explore.qore.network/tx/8C097EC88F2DF0A4F14E7DAC0DA4574D340ED52AFDF4025FDB193CD153286F2D) |
| INV-1009 | api-credits | 0.200 | REFUSED — PerVendorDayCapExceeded(450000, 400000) | [`41080E7596C8FDBC…`](https://explore.qore.network/tx/41080E7596C8FDBC24D7858CD2D167AFE8C15A658AE1A619DC77E4FF7E4FF346) |
| INV-1010 | cloud-hosting | 0.100 | REFUSED — tx rejected: failed to execute message; message index: 0: linked key is unbound or revoked: authenticator not permitted  | [`3ACCA201DCD97BFF…`](https://explore.qore.network/tx/3ACCA201DCD97BFF15D334E5B8153F998493D4D5EA9381075DF0067AF702411C) |
| INV-1012 | cloud-hosting | 0.250 | PAID | [`0C261DC914A9CE20…`](https://explore.qore.network/tx/0C261DC914A9CE20D2E9DE877BCB3552FB195921AA1ACDDF274C705622163E6B) |
| INV-1013 | cloud-hosting | 0.000 | PAID | [`3DAD3DF948B61ED8…`](https://explore.qore.network/tx/3DAD3DF948B61ED87642BC476C6F79DD66A0E1F182405510EF9D1696FC028D08) |

## The second run: sealed archive, owner approval, token settlement

| Rail | What the chain holds | Value | tx |
| --- | --- | --- | --- |
| QoreSeal key | ML-KEM-1024 key registered for the owner (`msg.pqc.register_kem_key`), read back identical | 1568 B, algorithm mlkem1024 (id 2), height 10848121 | [`A32934FED596CE21…`](https://explore.qore.network/tx/A32934FED596CE21DB3B6E4C6CFCEA81F1B2F57E76B46117A19EFCE64602E86C) |
| AMM swap | native pool 2 (fUSD/QOR), 100000 uqor in, minOut 1 | 881 fUSD out | [`F41B2712A4E0FD0E…`](https://explore.qore.network/tx/F41B2712A4E0FD0EE2EEB64D3E4B74276F63EDAB3BD1BA8FC5FA4CDDA86FFC56) |
| Sealed archive | 10 signed invoices encrypted to the owner's ML-KEM-1024 key; only the archive digest is public | `qg:seal-archive:v1:19527ac1f4b4741a3af5816c3584318be8830d29` (memo read back: True) | [`33B776A02C703158…`](https://explore.qore.network/tx/33B776A02C703158B1F1462E68F301C9BA774E26B4BE08B24D51CA7629538E22) |
| Agent re-linked | permissions ['send', 'evm'], 600000 uqor/tx, 1500000 uqor/day, denoms ['uqor', 'erc20:0x97143B771D34ddb9130b807b135Ae675A5a963CA'] | nonce 9 | [`1D11682380E79C06…`](https://explore.qore.network/tx/1D11682380E79C065DDC7B886538BCFDA945E3C47E7D6BE927C92576DC96D867) |
| QoreSign approval | SIWQ message verified against the on-chain ML-DSA-87 key, then `payWithApproval` settled 250000 uqor above the 200000 uqor cap (approval nonce 5) | negatives: {'amount changed after approval': 'refused: QoreSignError', 'replayed under another nonce': 'refused: SIWQ_NONCE_MISMATCH'} | [`0C261DC914A9CE20…`](https://explore.qore.network/tx/0C261DC914A9CE20D2E9DE877BCB3552FB195921AA1ACDDF274C705622163E6B) |
| fUSD settlement | the agent paid a 500 fUSD invoice on the native lane (`MsgExecuteCosmos`), vendor `qor1vd3kshzcvqkr238arazyelvjurm87mnjlr02d0` | vendor fUSD after: 500 | [`3DAD3DF948B61ED8…`](https://explore.qore.network/tx/3DAD3DF948B61ED87642BC476C6F79DD66A0E1F182405510EF9D1696FC028D08) |

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

Signed hybrid (secp256k1 + ML-DSA-87) and verified with `qoresign.verify_siwq(..., require_pq=True)` against the ML-DSA-87 key registered for the owner account on chain. A classical-only signature is refused with `PQ_LAYER_MISSING`.

## Verifying a payment yourself

```bash
    curl -s https://api-testnet.qore.host/cosmos/tx/v1beta1/txs/<hash> | jq .tx_response.code
```

A relayed payment is a `MsgExecuteEVM`; the transaction is signed hybrid by the
relayer, and the payment inside is authorised by the agent's ed25519 signature
over `evmAuthSignBytesV2`. A refused payment keeps transaction `code: 0` and
carries `abstractaccount_execution_failed` with the contract's reason — that is
the refusal, not a failed transaction.

Everything in this file is re-checked by `python verify.py` (read-only, no keys,
23 checks): bytecode against the artifact, the policy, the spend ledger, every
payment and refusal, the approval, the archive digest and the fUSD balance.
