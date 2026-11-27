// Exercise the lab page's real module in Node: the same ABI encoding, the same
// eth_calls, the same QoreSign verification the browser runs (web/assets/qg.js).
//
//   node scripts/test_web.mjs
//
// No key, no wallet, no writes: every call is an eth_call on the testnet.
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = fs.readFileSync(path.join(ROOT, "web/lab.html"), "utf8");
const blob = /<script id="qg-data" type="application\/json">([\s\S]*?)<\/script>/.exec(html)[1];
const DATA = JSON.parse(blob);
globalThis.document = { getElementById: () => ({ textContent: blob }) };

const qg = await import(path.join(ROOT, "web/assets/qg.js"));

const b32 = (t) => "0x" + Buffer.from(t).toString("hex").padEnd(64, "0").slice(0, 64);

let failures = 0;
const ok = (cond, label, extra = "") => {
  console.log(`${cond ? "PASS" : "FAIL"}  ${label}${extra ? "  — " + extra : ""}`);
  if (!cond) failures++;
};

const owner = await qg.view("ownerAccount");
ok(owner[0].toLowerCase() === DATA.owner.evm.toLowerCase(), "ownerAccount() read from chain", owner[0]);
const nonce = await qg.view("approvalNonce");
ok(typeof nonce[0] === "bigint", "approvalNonce() read", nonce[0].toString());
const count = await qg.view("vendorCount");
ok(count[0] === BigInt(DATA.vendors.length), "vendorCount matches the record", count[0].toString());

const v1 = await qg.view("vendor", [1n]);
ok(v1[0].toLowerCase() === DATA.vendors[0].payee.toLowerCase(), "vendor(1) payee", v1[0]);
ok(v1[2] === BigInt(DATA.vendors[0].per_tx_cap_uqor), "vendor(1) per-tx cap", v1[2].toString());

const key = await qg.pqcKeyStatus();
ok(key.registered && key.pubkey.length === 2 + 2592 * 2, "pqcKeyStatus precompile returns the 2592-byte key",
  `${(key.pubkey.length - 2) / 2} bytes`);

const ZERO_SIG = "0x" + "00".repeat(4627);
const cases = [
  ["over-cap", () => ({
    data: qg.encodeCall("pay", [1n, v1[2] + 50000n, b32("lab:over-cap")]),
    value: (v1[2] + 50000n) * 10n ** 12n,
  }), "PerVendorTxCapExceeded"],
  ["unknown vendor", () => ({
    data: qg.encodeCall("pay", [99n, 1000n, b32("lab:unknown")]), value: 1000n * 10n ** 12n,
  }), "UnknownVendor"],
  ["forged PQ signature", () => ({
    data: qg.encodeCall("setTreasuryCap", [999999999n, nonce[0], key.pubkey, ZERO_SIG]), value: 0n,
  }), "BadPqcSignature"],
  ["value mismatch", () => ({
    data: qg.encodeCall("pay", [1n, 100000n, b32("lab:value")]), value: 50000n * 10n ** 12n,
  }), "ValueMismatch"],
  ["stale nonce", () => ({
    data: qg.encodeCall("payWithApproval", [1n, 1000n, b32("lab:replay"), nonce[0] - 1n, key.pubkey, ZERO_SIG]),
    value: 1000n * 10n ** 12n,
  }), "StaleApprovalNonce"],
  ["stranger sender", () => ({
    data: qg.encodeCall("pay", [1n, 1000n, b32("lab:stranger")]), value: 1000n * 10n ** 12n,
    from: "0x000000000000000000000000000000000000dEaD",
  }), "NotOwnerAccount"],
  ["one-byte signature", () => ({
    data: qg.encodeCall("setVendorActive", [1n, false, nonce[0], key.pubkey, "0x01"]), value: 0n,
  }), "BadPqcSignature"],
  ["already-settled invoice", () => ({
    data: qg.encodeCall("pay", [BigInt(DATA.settled_invoice.vendor_id),
      BigInt(DATA.settled_invoice.amount_uqor), DATA.settled_invoice.key_hash]),
    value: BigInt(DATA.settled_invoice.amount_uqor) * 10n ** 12n,
  }), "InvoiceAlreadyPaid"],
];

for (const [label, build, expected] of cases) {
  const call = build();
  try {
    const out = await qg.ethCall(call.data, call.value, call.from || DATA.owner.evm);
    ok(false, label, `not refused: ${out}`);
  } catch (err) {
    const decoded = qg.decodeRevert(qg.revertDataFrom(err));
    ok(decoded.name === expected, label,
      `${decoded.name}${decoded.args.length ? "(" + decoded.args.map(String).join(", ") + ")" : ""}`);
  }
}

const payload = "0x" + (999999999n).toString(16).padStart(64, "0");
const messageHex = await qg.ethCall(qg.encodeCall("adminMessage", [DATA.actions.SET_TREASURY_CAP, payload, nonce[0]]));
const body = messageHex.replace(/^0x/, "");
const offset = Number(BigInt("0x" + body.slice(0, 64))) * 2;
const len = Number(BigInt("0x" + body.slice(offset, offset + 64)));
ok(len === 169, "adminMessage length", `${len} bytes`);

const rec = DATA.console;
const res = await qg.verifyWithChain(rec.signer, rec.data_utf8, rec.signature);
ok(res.possessionProven === true, "verifyMessageWithChain: recorded signature", JSON.stringify(res));

try {
  await qg.verifyWithChain(rec.signer, rec.data_utf8 + "!", rec.signature);
  ok(false, "tampered copy refused", "accepted");
} catch (err) {
  ok(true, "tampered copy refused", err.code || err.name);
}

const addrs = JSON.parse(fs.readFileSync(path.join(ROOT, "demo/addresses.json"), "utf8"));
ok(DATA.contract.toLowerCase() === addrs.contract.toLowerCase(), "lab embeds the deployed contract", DATA.contract);
const settledOnChain = await qg.view("paidInvoices", [DATA.settled_invoice.key_hash]);
ok(settledOnChain[0] === true, "paidInvoices() says the lab's invoice is settled", DATA.settled_invoice.invoice);

console.log(`\n${failures === 0 ? "all checks passed" : failures + " failed"}`);
process.exit(failures === 0 ? 0 : 1);
