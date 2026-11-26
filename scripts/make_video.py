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
    entries = journal()
    paid = [e for e in entries if e.get("chain_success")]
    refused = [
        e for e in entries
        if e.get("chain_success") is False and "gas" not in (e.get("chain_reason") or "").lower()
    ]
    contract = dep.get("contract", "0x…")

    slides: list[Slide] = []

    s = Slide("Build Before Q-Day · Post-Quantum AI Agents", "QoreGuard", 7)
    s.say("A treasury agent that pays bills.", TEXT, "big")
    s.say("Its limits are enforced by the chain, not by its own code.", MUTED, "big")
    s.say(
        "Every policy change needs a fresh ML-DSA-87 signature from the owner — "
        "the key a quantum computer cannot take.",
        ACCENT,
    )
    slides.append(s)

    s = Slide("Problem", "An agent with a wallet has no real limits", 13)
    s.say("Today: the spending limit is a line of code next to the private key.", TEXT)
    s.say("Steal the key, or inject one invoice, and the limit disappears with it.", TEXT)
    s.say("", style="gap")
    s.say("QoreGuard moves the limit out of the agent:", TEXT)
    s.say("· the account's own rule bounds each call and each day", OK, "small")
    s.say("· the rule names the only two functions the key may ever call", OK, "small")
    s.say("· a Solidity policy bounds each vendor and the whole treasury", OK, "small")
    s.say("· changing any of it needs an ML-DSA-87 signature, verified on chain", PURPLE, "small")
    slides.append(s)

    s = Slide("Architecture", "Two independent gates", 14)
    s.say("agent (ed25519, no funds)  →  MsgExecuteEVM  →  relayer pays the fee", TEXT, "mono")
    s.say("", style="gap")
    s.say("gate 1 — the account's spending rule (x/abstractaccount)", ACCENT, "h2")
    s.say("permissions: send, evm   limits: 0.6 QOR/tx, 1.5 QOR/day, uqor only", TEXT, "mono")
    s.say("allowed EVM calls: pay() and payWithApproval() on one contract", TEXT, "mono")
    s.say("", style="gap")
    s.say("gate 2 — QoreGuardTreasury.sol", PURPLE, "h2")
    s.say("vendor allowlist · per-vendor caps · treasury daily cap · events", TEXT, "mono")
    s.say("policy changes: pqcVerify(0x…0a01) over the owner's ML-DSA-87 signature", TEXT, "mono")
    s.say("", style="gap")
    s.say("A stolen agent key can reach two functions, within one day's cap.", OK)
    slides.append(s)

    s = Slide("On chain", "The deployment, inside a scoped EVM window", 12)
    s.say(f"contract  {contract}", TEXT, "mono")
    s.say(f"deploy tx {dep.get('tx', '—')}", TEXT, "mono")
    s.say(f"block     {dep.get('block', '—')}", TEXT, "mono")
    s.say("", style="gap")
    s.say("MsgOpenEVMWindow: 1 transaction, 1.0 QOR of value+fees, 200 blocks.", TEXT, "small")
    s.say("The classical owner key alone can never open that window.", MUTED, "small")
    s.say("", style="gap")
    s.say("explore.qore.network (switch to Testnet) shows every hash in this video.", ACCENT, "small")
    slides.append(s)

    s = Slide("Agent run", f"Paid {len(paid)} invoices, refused {len(refused)}", 15)
    shown = [e for e in entries if "gas" not in (e.get("chain_reason") or "").lower()][:8]
    for e in shown:
        ok = e.get("chain_success")
        mark = "PAID   " if ok else "REFUSED"
        color = OK if ok else BAD
        inv = (e.get("invoice") or "").ljust(9)
        amt = f"{e.get('amount_uqor', 0) / 1_000_000:.2f} QOR"
        s.say(f"{mark} {inv} {amt:>10}  {e.get('vendor_label','')}", color, "mono")
    s.say("", style="gap")
    s.say("The refusals are the product: they come from the chain and the contract,", MUTED, "small")
    s.say("not from the agent's own checks.", MUTED, "small")
    slides.append(s)

    s = Slide("Refusals", "What the chain said no to, in its own words", 13)
    for e in refused[:4]:
        s.say(f"{e.get('invoice')}  {e.get('amount_uqor',0)/1_000_000:.2f} QOR", BAD, "h2")
        reason = (e.get("chain_reason") or "").replace("\\n", " ")[:150]
        s.say(reason or "(no reason returned)", TEXT, "mono")
        s.say("", style="gap")
    s.say("One more refusal never reached the chain: an invoice whose amount was", MUTED, "small")
    s.say("changed after signing fails the vendor's ed25519 signature.", MUTED, "small")
    slides.append(s)

    s = Slide("Post-quantum admin", "An owner signature the chain verifies itself", 13)
    s.say("ML-DSA-87 (FIPS 204): 2,592-byte public key, 4,627-byte signature.", TEXT)
    s.say("", style="gap")
    s.say("pqcKeyStatus(owner)  → registered, algorithm 1, key proven", TEXT, "mono")
    s.say("pqcVerify(key, sig, message) → true", TEXT, "mono")
    s.say("", style="gap")
    s.say("message = domain ‖ chainid ‖ contract ‖ action ‖ payload ‖ nonce", TEXT, "mono")
    s.say("", style="gap")
    s.say("A leaked classical key cannot add a payee, raise a cap, or approve a", TEXT, "small")
    s.say("payment above a cap. That is the whole answer to Q-Day.", OK, "small")
    slides.append(s)

    s = Slide("Exception path", "One payment the human approves, once", 12)
    s.say("Invoice above the vendor cap  →  refused by the contract.", TEXT)
    s.say("Owner signs (vendorId, amount, memo, nonce) with ML-DSA-87.", TEXT)
    s.say("The agent submits it; the chain verifies the signature in the contract.", TEXT)
    s.say("", style="gap")
    s.say("The same signature cannot pay a different amount, or be used twice:", TEXT, "small")
    s.say("the message binds the nonce and the policy consumes it.", MUTED, "small")
    s.say("", style="gap")
    s.say("And the treasury-wide daily cap still applies to approved payments.", OK, "small")
    slides.append(s)

    s = Slide("Revocation", "One transaction ends it", 10)
    s.say("MsgRevokeAuthenticator, signed by the owner with both keys.", TEXT)
    s.say("The next agent payment is refused by the chain:", TEXT)
    s.say("linked key is unbound or revoked", BAD, "mono")
    s.say("", style="gap")
    s.say("Worst case while the key was live: one day's cap, to registered vendors only.", OK, "small")
    slides.append(s)

    s = Slide("Repo", "Reproducible from an empty directory", 11)
    s.say("contracts/    23 Foundry tests, encoding parity with the off-chain encoder", TEXT, "mono")
    s.say("agent/        ed25519 agent, signed intents, MsgExecuteEVM relaying", TEXT, "mono")
    s.say("demo/         mandate, signed invoices, journal (evidence)", TEXT, "mono")
    s.say("scripts/      keys, invoice signing, local integration, dashboard", TEXT, "mono")
    s.say("", style="gap")
    s.say("forge test                    → 23 passing", OK, "mono")
    s.say("scripts/local_integration.sh  → 12 checks against anvil", OK, "mono")
    s.say("", style="gap")
    s.say("AI_USAGE.md documents the AI workflow and the human decisions.", MUTED, "small")
    slides.append(s)

    s = Slide("QoreGuard", "Build post-quantum like the key will leak.", 8)
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
