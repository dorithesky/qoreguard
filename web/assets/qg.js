// QoreGuard lab helpers — read-only access to the testnet from a static page.
//
// Nothing here signs, nothing sends a transaction: every function is an
// `eth_call` (a simulation the node answers and throws away) or a read of the
// chain state. There is no key on this page and no account to connect.
//
// The two QoreChain endpoints have different browser policies:
//   * the EVM lane (evm-testnet.qore.host) sends `access-control-allow-origin: *`,
//     so a static page can call it directly;
//   * the Cosmos REST API does not, so the page reads the owner's ML-DSA-87 key
//     from the same precompile the contract itself calls (0x…0a02) through the
//     EVM lane, and hands that key to the QoreSign verifier.
import { verifyMessageWithChain, verifyMessage } from "../vendor/qoresign-verify.js";

export const DATA = JSON.parse(document.getElementById("qg-data").textContent);

const RPC = DATA.chain.evmRpc;
let nextId = 1;

/** JSON-RPC against the testnet EVM lane. */
export async function rpc(method, params) {
  const res = await fetch(RPC, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: nextId++, method, params }),
  });
  const body = await res.json();
  if (body.error) {
    const err = new Error(body.error.message || "rpc error");
    err.data = body.error.data;
    throw err;
  }
  return body.result;
}

/** A simulated call as the owner — the node runs it and returns the result or the revert. */
export async function ethCall(data, value = 0n, from = DATA.owner.evm, to = DATA.contract) {
  return rpc("eth_call", [
    { from, to, data, value: "0x" + BigInt(value).toString(16) },
    "latest",
  ]);
}

// ---------------------------------------------------------------- encoding --
const strip0x = (h) => (h || "").replace(/^0x/, "");
const word = (hexBody) => hexBody.padStart(64, "0").slice(-64);

function encodeStatic(type, value) {
  if (type === "address") return strip0x(String(value).toLowerCase()).padStart(64, "0");
  if (type === "bool") return (value ? "1" : "0").padStart(64, "0");
  if (type === "bytes32") return word(strip0x(value));
  return BigInt(value).toString(16).padStart(64, "0"); // uintN / intN (non-negative)
}

function encodeDynamicBytes(bytesHex) {
  const body = strip0x(bytesHex);
  const len = body.length / 2;
  const padded = body.padEnd(Math.ceil(body.length / 64) * 64, "0");
  return BigInt(len).toString(16).padStart(64, "0") + padded;
}

/** ABI-encode a call for a function in DATA.functions: `encodeCall("pay", [1, 250000, memo])`. */
export function encodeCall(name, values) {
  const fn = DATA.functions[name];
  if (!fn) throw new Error(`unknown function ${name}`);
  const heads = [];
  const tail = [];
  let tailBytes = 0;
  fn.types.forEach((type, i) => {
    const value = values[i];
    if (type === "bytes" || type === "string") {
      heads.push(BigInt(fn.types.length * 32 + tailBytes).toString(16).padStart(64, "0"));
      const encoded = encodeDynamicBytes(value);
      tailBytes += encoded.length / 2;
      tail.push(encoded);
    } else {
      heads.push(encodeStatic(type, value));
    }
  });
  return fn.selector + heads.join("") + tail.join("");
}

function decodeStatic(type, wordHex) {
  if (type === "address") return "0x" + wordHex.slice(24).toLowerCase();
  if (type === "bool") return BigInt("0x" + wordHex) !== 0n;
  if (type === "bytes32") return "0x" + wordHex.toLowerCase();
  return BigInt("0x" + wordHex);
}

/** Decode a tuple of static return values. */
export function decodeTuple(types, hex) {
  const body = strip0x(hex);
  return types.map((type, i) => decodeStatic(type, body.slice(i * 64, (i + 1) * 64)));
}

/** Decode revert data into the contract's own error name and argument values. */
export function decodeRevert(hex) {
  const body = strip0x(hex);
  if (body.length < 8) return { name: "Error", types: [], args: [], raw: hex || "0x" };
  const selector = "0x" + body.slice(0, 8);
  const known = DATA.errors[selector];
  if (!known) return { name: "unknown", selector, types: [], args: [], raw: hex };
  const argsHex = body.slice(8);
  const args = known.types.map((type, i) => decodeStatic(type, argsHex.slice(i * 64, (i + 1) * 64)));
  return { name: known.name, selector, types: known.types, args, raw: hex };
}

/** The revert payload an eth_call error carries, wherever the node put it. */
export function revertDataFrom(err) {
  const d = err && err.data;
  if (typeof d === "string") return d;
  if (d && typeof d === "object") {
    if (typeof d.data === "string") return d.data;
    if (typeof d.result === "string") return d.result;
  }
  const m = /(?:0x[0-9a-fA-F]{8,})/.exec(String(err && err.message));
  return m ? m[0] : "0x";
}

/** `qor1…` → `0x…`: QoreChain's bech32 payload is the 20-byte account address. */
export function bech32ToHex(address) {
  const text = String(address).toLowerCase();
  if (text.startsWith("0x")) return text;
  const charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
  const body = text.slice(text.lastIndexOf("1") + 1, -6); // drop the checksum
  const bytes = [];
  let acc = 0;
  let bits = 0;
  for (const ch of body) {
    const value = charset.indexOf(ch);
    if (value < 0) throw new Error(`not a bech32 address: ${address}`);
    acc = (acc << 5) | value;
    bits += 5;
    while (bits >= 8) {
      bits -= 8;
      bytes.push((acc >> bits) & 0xff);
    }
  }
  return "0x" + bytes.map((b) => b.toString(16).padStart(2, "0")).join("");
}

// --------------------------------------------------------------- chain reads --
export async function view(name, args = []) {
  const fn = DATA.functions[name];
  const data = fn.types.length ? encodeCall(name, args) : fn.selector;
  const raw = await ethCall(data);
  return decodeTuple(fn.returns, raw);
}

export async function chainId() {
  return BigInt(await rpc("eth_chainId", []));
}

export async function blockNumber() {
  return BigInt(await rpc("eth_blockNumber", []));
}

/** The owner's ML-DSA-87 key, read from the chain's own precompile 0x…0a02. */
export async function pqcKeyStatus(account = DATA.owner.evm) {
  const raw = strip0x(
    await ethCall(encodeCall("pqcKeyStatus", [bech32ToHex(account)]), 0n, DATA.owner.evm, DATA.pqcKeysPrecompile),
  );
  // returns (bool registered, uint8 algorithmId, bytes pubkey, bool proven)
  const registered = BigInt("0x" + raw.slice(0, 64)) !== 0n;
  const algorithmId = Number(BigInt("0x" + raw.slice(64, 128)));
  const offset = Number(BigInt("0x" + raw.slice(128, 192))) * 2;
  const proven = BigInt("0x" + raw.slice(192, 256)) !== 0n;
  const len = Number(BigInt("0x" + raw.slice(offset, offset + 64)));
  const pubkey = "0x" + raw.slice(offset + 64, offset + 64 + len * 2);
  return { registered, algorithmId, proven, pubkey };
}

/**
 * The QoreSign call a dApp makes after a wallet signature, with the key read
 * from the EVM precompile instead of REST (the REST API has no CORS header, the
 * EVM lane does). `fetchPqKeyStatus` inside the library asks for
 * `/qorechain/pqc/v1/account/<addr>`; this shim answers that from the chain.
 */
function pqFetchFromChain(url) {
  const address = decodeURIComponent(String(url).split("/").pop());
  return pqcKeyStatus(address).then((status) => {
    const body = status.registered
      ? {
          found: true,
          account: {
            address,
            public_key: b64(status.pubkey),
            algorithm_id: status.algorithmId,
            key_type: "hybrid",
            possession_proven: status.proven,
          },
        }
      : { found: false };
    return {
      status: status.registered ? 200 : 404,
      ok: status.registered,
      json: async () => body,
    };
  });
}

function b64(hex) {
  const body = strip0x(hex);
  const bytes = new Uint8Array(body.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(body.slice(i * 2, i * 2 + 2), 16);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/** verifyMessageWithChain — the library call, over the key the chain holds now. */
export async function verifyWithChain(signer, data, signature) {
  return verifyMessageWithChain({
    rest: DATA.chain.rest,
    fetch: pqFetchFromChain,
    chainId: DATA.chain.id,
    signer,
    data,
    signature,
  });
}

/** The pure verifier, given a public key explicitly (used for a negative test). */
export function verifyWithKey(chainIdValue, signer, data, signature, pqPublicKeyHex) {
  return verifyMessage({ chainId: chainIdValue, signer, data, signature, pqPublicKey: pqPublicKeyHex });
}

// ------------------------------------------------------------------- display --
export const fmtUqor = (v) => {
  const n = BigInt(v);
  const whole = n / 1000000n;
  const frac = (n % 1000000n).toString().padStart(6, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : `${whole}`;
};

export const short = (s, head = 10, tail = 8) =>
  s && s.length > head + tail ? `${s.slice(0, head)}…${s.slice(-tail)}` : s || "";
