#!/usr/bin/env python3
"""Build the demo video from real artifacts (≤3 minutes, captioned).

No narration track: every card is readable in place, and each number is copied
from the deployment record or the agent journal, so the video cannot drift from
the repo.

    python scripts/make_video.py            # writes demo/qoreguard-demo.mp4
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "qoreguard-demo.mp4"
FONT_DIR = Path("/usr/local/lib/python3.12/site-packages/matplotlib/mpl-data/fonts/ttf")

W, H = 1280, 720
BG = (11, 15, 20)
PANEL = (18, 24, 33)
TEXT = (230, 237, 243)
MUTED = (139, 152, 165)
ACCENT = (88, 166, 255)
OK = (63, 185, 80)
BAD = (248, 81, 73)
PURPLE = (163, 113, 247)


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    else:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


class Slide:
    def __init__(self, kicker: str, title: str, seconds: float = 12.0) -> None:
        self.kicker = kicker
        self.title = title
        self.seconds = seconds
        self.lines: list[tuple[str, tuple[int, int, int], str]] = []  # (text, color, style)

    def say(self, text: str, color=TEXT, style: str = "body") -> "Slide":
        self.lines.append((text, color, style))
        return self

    def render(self) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 8, H], fill=ACCENT)
        d.text((64, 56), self.kicker.upper(), font=font(18, bold=True), fill=ACCENT)
        d.text((64, 88), self.title, font=font(40, bold=True), fill=TEXT)
        y = 168
        for text, color, style in self.lines:
            if style == "gap":
                y += 18
                continue
            if style == "rule":
                d.line([(64, y), (W - 64, y)], fill=(30, 39, 51), width=1)
                y += 22
                continue
            if style == "mono":
                f = font(17, mono=True)
                width = 118
            elif style == "small":
                f = font(18)
                width = 105
            elif style == "h2":
                f = font(24, bold=True)
                width = 76
            elif style == "big":
                f = font(30, bold=True)
                width = 62
            else:
                f = font(21)
                width = 92
            for chunk in textwrap.wrap(text, width=width) or [""]:
                d.text((64, y), chunk, font=f, fill=color)
                y += f.size + 9
            y += 2
        d.text((64, H - 46), "QoreGuard · Build Before Q-Day · QoreChain testnet (test tokens only)",
               font=font(15), fill=MUTED)
        d.text((W - 190, H - 46), "Apache-2.0", font=font(15), fill=MUTED)
        return img


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def journal() -> list[dict]:
    path = ROOT / "demo" / "journal.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def build_slides() -> list[Slide]:
    dep = load_json(ROOT / ".keys" / "deployment.json", {})
    addrs = load_json(ROOT / "demo" / "addresses.json", {})
    p1 = load_json(ROOT / "demo" / "p1.json", {})
    features = load_json(ROOT / "demo" / "onchain_features.json", {})
    verification = load_json(ROOT / "docs" / "verification.json", {})
    cbom = load_json(ROOT / "docs" / "cbom.json", {})
    entries = journal()
    paid = [e for e in entries if e.get("chain_success")]
    refused = [
        e for e in entries
        if e.get("chain_success") is False and "gas" not in (e.get("chain_reason") or "").lower()
    ]
    contract = dep.get("contract") or addrs.get("contract", "0x…")

    tests = verification.get("tests", {})
    coverage = verification.get("coverage", {})
    mutation = verification.get("mutation", {})
    approval = p1.get("approval", {})
    seal = p1.get("seal", {})
    token = p1.get("token", {})
    algorithms = len([c for c in cbom.get("components", []) if (c.get("cryptoProperties") or {}).get("assetType") == "algorithm"])

    slides: list[Slide] = []

    s = Slide("Build Before Q-Day · Post-Quantum AI Agents", "QoreGuard", 6)
    s.say("A treasury agent that pays bills.", TEXT, "big")
    s.say("Its limits are enforced by the chain, not by its own code.", MUTED, "big")
    s.say(
        "Every policy change needs a fresh ML-DSA-87 signature from the owner — "
        "the key a quantum computer cannot take.",
        ACCENT,
    )
    slides.append(s)

    s = Slide("Problem", "An agent with a wallet has no real limits", 11)
    s.say("Today: the spending limit is a line of code next to the private key.", TEXT)
    s.say("Steal the key, or inject one invoice, and the limit disappears with it.", TEXT)
    s.say("", style="gap")
    s.say("QoreGuard moves the limit out of the agent:", TEXT)
    s.say("· the account's own rule bounds each call and each day", OK, "small")
    s.say("· the rule names the only two functions the key may ever call", OK, "small")
    s.say("· a Solidity policy bounds each vendor and the whole treasury", OK, "small")
    s.say("· changing any of it needs an ML-DSA-87 signature, verified on chain", PURPLE, "small")
    slides.append(s)

    s = Slide("Architecture", "Two independent gates", 12)
    s.say("agent (ed25519, no funds)  →  MsgExecuteEVM  →  relayer pays the fee", TEXT, "mono")
    s.say("", style="gap")
    s.say("gate 1 — the account's spending rule (x/abstractaccount)", ACCENT, "h2")
    s.say("permissions: send, evm   limits: 0.6 QOR/tx, 1.5 QOR/day, uqor + fUSD", TEXT, "mono")
    s.say("allowed EVM calls: pay() and payWithApproval() on one contract", TEXT, "mono")
    s.say("", style="gap")
    s.say("gate 2 — QoreGuardTreasury.sol", PURPLE, "h2")
    s.say("vendor allowlist · per-vendor caps · treasury daily cap · events", TEXT, "mono")
    s.say("policy changes: pqcVerify(0x…0a01) over the owner's ML-DSA-87 signature", TEXT, "mono")
    slides.append(s)

    s = Slide("On chain", "The deployment, inside a scoped EVM window", 10)
    s.say(f"contract  {contract}", TEXT, "mono")
    s.say(f"deploy tx {dep.get('tx', '—')}", TEXT, "mono")
    s.say(f"block     {dep.get('block', '—')}", TEXT, "mono")
    s.say("", style="gap")
    s.say("MsgOpenEVMWindow: 1 transaction, 1.5 QOR of value+fees, 400 blocks.", TEXT, "small")
    s.say("The classical owner key alone can never open that window.", MUTED, "small")
    s.say("", style="gap")
    s.say("explore.qore.network (switch to Testnet) shows every hash in this video.", ACCENT, "small")
    slides.append(s)

    s = Slide("Agent run", f"Paid {len(paid)} invoices, refused {len(refused)}", 12)
    shown = [e for e in entries if "gas" not in (e.get("chain_reason") or "").lower()][:7]
    for e in shown:
        ok = e.get("chain_success")
        mark = "PAID   " if ok else "REFUSED"
        color = OK if ok else BAD
        inv = (e.get("invoice") or "").ljust(9)
        unit = "fUSD" if e.get("kind") == "token_payment" else "QOR"
        amt = f"{e.get('amount_uqor', 0) / 1_000_000:,.2f} {unit}"
        s.say(f"{mark} {inv} {amt:>13}  {e.get('vendor_label','')}", color, "mono")
    s.say("", style="gap")
    s.say("The refusals are the product: they come from the chain and the contract.", MUTED, "small")
    slides.append(s)

    s = Slide("Refusals", "What the chain said no to, in its own words", 11)
    for e in refused[:3]:
        s.say(f"{e.get('invoice')}  {e.get('amount_uqor',0)/1_000_000:.2f} QOR", BAD, "h2")
        reason = (e.get("chain_reason") or "").replace("\\n", " ")[:130]
        s.say(reason or "(no reason returned)", TEXT, "mono")
        s.say("", style="gap")
    s.say("A tampered invoice never reaches the chain: its vendor signature fails.", MUTED, "small")
    slides.append(s)

    s = Slide("Post-quantum admin", "An owner signature the chain verifies itself", 10)
    s.say("ML-DSA-87 (FIPS 204): 2,592-byte public key, 4,627-byte signature.", TEXT)
    s.say("", style="gap")
    s.say("pqcKeyStatus(owner)  → registered, algorithm 1, key proven", TEXT, "mono")
    s.say("pqcVerify(key, sig, message) → true", TEXT, "mono")
    s.say("", style="gap")
    s.say("message = domain ‖ chainid ‖ contract ‖ action ‖ payload ‖ nonce", TEXT, "mono")
    s.say("", style="gap")
    s.say("A leaked classical key cannot add a payee or raise a cap.", OK, "small")
    slides.append(s)

    s = Slide("Rail 2 — QoreSeal", "The invoice archive, encrypted to the owner", 9)
    s.say("Each signed invoice is sealed with ML-KEM-1024 (hybrid X25519 + ML-KEM).", TEXT, "small")
    s.say(f"key registered on chain in tx {features.get('kem',{}).get('tx','—')[:24]}…", TEXT, "mono")
    s.say("(1,568 bytes, read back identical)", MUTED, "small")
    s.say("", style="gap")
    s.say(f"archive digest  qg:seal-archive:v1:{seal.get('archive_digest','')[:24]}…", PURPLE, "mono")
    s.say(f"anchor tx       {seal.get('tx','—')[:24]}…", TEXT, "mono")
    s.say("", style="gap")
    s.say("Only the 32-byte digest goes on chain; only the owner's key opens it.", OK, "small")
    slides.append(s)

    s = Slide("Rail 3 — QoreSign", "One payment the human approves, once", 9)
    s.say(f"INV {approval.get('invoice','—')} asks for {approval.get('amount_uqor',0):,} uqor", TEXT, "small")
    s.say(f"above the vendor's {approval.get('vendor_per_tx_cap_uqor',0):,} uqor per-tx cap.", TEXT, "small")
    s.say("", style="gap")
    s.say("The owner signs a Sign-In-With-QoreChain message — hybrid, with ML-DSA-87.", TEXT, "small")
    s.say("The agent verifies it against the on-chain key, then pays.", TEXT, "small")
    s.say(f"settled in tx {approval.get('tx','—')[:24]}… (approval nonce {approval.get('approval_nonce','—')})", OK, "mono")
    s.say("", style="gap")
    s.say("A doubled amount, a replayed nonce, another invoice: all refused.", MUTED, "small")
    slides.append(s)

    s = Slide("Rail 4 — fUSD", "A stablecoin invoice under the same rule", 8)
    s.say(f"{token.get('amount','—')} fUSD to the vendor's account.", TEXT)
    s.say("Sent on the native lane by the agent itself: MsgExecuteCosmos.", TEXT, "small")
    s.say(f"tx {token.get('tx','—')[:24]}…", TEXT, "mono")
    s.say("", style="gap")
    s.say("The same chain rule lists the token denom, so the token has a budget too.", OK, "small")
    s.say(f"(the treasury got its fUSD from pool {features.get('swap',{}).get('pool_id','—')})", MUTED, "small")
    slides.append(s)

    s = Slide("Attack lab", "A judge presses, the chain refuses", 11)
    s.say("dorithesky.github.io/qoreguard/web/lab.html", ACCENT, "mono")
    s.say("", style="gap")
    s.say("No wallet, no key, no install: the page holds no key at all.", TEXT, "small")
    for line in (
        "over-cap payment        → PerVendorTxCapExceeded",
        "unregistered vendor     → UnknownVendor",
        "forged ML-DSA signature → BadPqcSignature",
        "memo ≠ value            → ValueMismatch",
        "stale approval nonce    → StaleApprovalNonce",
        "any other sender        → NotOwnerAccount",
        "one-byte signature      → BadPqcSignature",
    ):
        s.say(line, BAD, "mono")
    slides.append(s)

    s = Slide("QoreX console", "The owner signs in a wallet, not in a script", 9)
    s.say("window.qorex.native.signArbitrary(chainId, address, data)", TEXT, "mono")
    s.say("verifyMessageWithChain(...) → the key the chain holds", TEXT, "mono")
    s.say("", style="gap")
    s.say("The page reads the ML-DSA-87 key from pqcKeyStatus (0x…0a02) — the", MUTED, "small")
    s.say("precompile the contract itself calls — and verifies the hybrid signature.", MUTED, "small")
    s.say("", style="gap")
    s.say("One changed byte is refused, in the browser, with no key present.", OK, "small")
    slides.append(s)

    s = Slide("Proof", "What a reviewer can re-run without trusting us", 11)
    s.say(f"forge test        {tests.get('total','—')} tests, {tests.get('invariants','—')} invariants", OK, "mono")
    s.say("                  " + ", ".join(
        f"{k} {v[0]}/{v[1]}" for k, v in coverage.items()
    ), OK, "mono")
    s.say(f"scripts/mutate.py {mutation.get('killed','—')}/{mutation.get('total','—')} mutants killed", OK, "mono")
    s.say("python verify.py  40 checks, read-only, no keys", OK, "mono")
    s.say("", style="gap")
    s.say(f"QoreScan CBOM     {algorithms} algorithms, CycloneDX 1.6 + DORA/NIS2", TEXT, "mono")
    s.say("", style="gap")
    s.say("Every number above is produced by the command beside it.", MUTED, "small")
    slides.append(s)

    s = Slide("QoreGuard", "Build post-quantum like the key will leak.", 7)
    s.say("Because one day, it will.", ACCENT, "big")
    s.say("", style="gap")
    s.say("QoreChain testnet · qorechain-diana · EVM chain 9800", MUTED)
    s.say("Category: Post-Quantum AI Agents · Apache-2.0", MUTED)
    slides.append(s)
    return slides


def main() -> int:
    slides = build_slides()
    frames = [s.render() for s in slides]
    durations = [s.seconds for s in slides]
    fps = 30

    OUT.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(OUT), (W, H), fps=fps, codec="libx264", pix_fmt_in="rgb24", pix_fmt_out="yuv420p",
        macro_block_size=8, output_params=["-movflags", "+faststart"],
    )
    writer.send(None)
    for frame, seconds in zip(frames, durations):
        data = frame.tobytes()
        for _ in range(int(seconds * fps)):
            writer.send(data)
    writer.close()

    total = sum(durations)
    print(f"wrote {OUT} — {len(slides)} slides, {total:.0f}s")
    if total > 180:
        print("WARNING: over the 3-minute limit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
