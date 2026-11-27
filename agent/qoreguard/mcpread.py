"""The agent's read layer, over the QoreChain MCP server (`qorechain-mcp`).

The kit ships a Model Context Protocol server that answers questions about a
QoreChain network — an account's balances and nonce, whether a key is linked,
a transaction's hybrid-signature status, and any EVM view function
(`read_contract`). QoreGuard's read-only surfaces (the dashboard's numbers, the
`status` command, a reviewer's spot checks) go through that server instead of
hand-rolled REST calls, so the same answers are available to any MCP client —
an editor, another agent, or a judge's own tooling — without credentials.

Nothing here can write: every tool in `qorechain-mcp` is read-only by design
(`prepare_transfer` returns an unsigned message, and there is no broadcast
tool). The treasury's own limits are what stop the agent; this module cannot
even see a key.

    python -m qoreguard.mcpread status         # treasury state via MCP tools
    python -m qoreguard.mcpread address qor1…  # one account
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = [sys.executable, "-m", "qorechain_mcp"]


def _server_params() -> StdioServerParameters:
    env = dict(os.environ)
    env.setdefault("QORECHAIN_MCP_NETWORK", "testnet")
    return StdioServerParameters(command=SERVER[0], args=SERVER[1:], env=env)


async def _with_session(fn):
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(_server_params()))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return await fn(session)


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, args)
    if getattr(result, "isError", False):
        raise RuntimeError(f"{name} failed: {result.content}")
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call one QoreChain MCP tool and return its JSON answer."""

    async def run():
        return await _with_session(lambda s: _call(s, name, args))

    return asyncio.run(run())


def list_tools() -> list[str]:
    async def run():
        return await _with_session(lambda s: _tools(s))

    return asyncio.run(run())


async def _tools(session: ClientSession) -> list[str]:
    listed = await session.list_tools()
    return [t.name for t in listed.tools]


def read_uint(contract: str, function: str, args: list[str] | None = None) -> int:
    """One uint256 view through the MCP server."""
    req: dict[str, Any] = {"to": contract, "function": function, "network": "testnet"}
    if args:
        req["args"] = args
    answer = call("read_contract", req)
    raw = answer.get("result") or answer.get("return") or ""
    if isinstance(raw, dict):
        raw = raw.get("data", "")
    raw = str(raw)
    if raw.startswith("0x"):
        raw = raw[2:]
    return int(raw, 16) if raw else 0


def treasury_state(contract: str) -> dict[str, Any]:
    """The treasury as the chain sees it, read the MCP way."""
    return {
        "vendors": read_uint(contract, "vendorCount()"),
        "approval_nonce": read_uint(contract, "approvalNonce()"),
        "treasury_daily_cap_uqor": read_uint(contract, "treasuryDailyCapUqor()"),
        "spent_today_uqor": read_uint(contract, "treasurySpentToday()"),
        "total_spent_uqor": read_uint(contract, "totalSpentUqor()"),
    }


def main() -> None:
    from .config import ROOT

    dep = json.loads((ROOT / ".keys" / "deployment.json").read_text())
    what = sys.argv[1] if len(sys.argv) > 1 else "status"
    if what == "tools":
        print(json.dumps(list_tools(), indent=2))
    elif what == "address":
        print(json.dumps(call("get_address_info", {"address": sys.argv[2], "network": "testnet"}), indent=2)[:4000])
    elif what == "tx":
        print(json.dumps(call("get_transaction", {"hash": sys.argv[2], "network": "testnet"}), indent=2)[:4000])
    else:
        print(json.dumps(treasury_state(dep["contract"]), indent=2))


if __name__ == "__main__":
    main()
