#!/usr/bin/env bash
# Local end-to-end check against anvil with the PQC precompiles mocked.
# Nothing here touches a network; it is the cheap check that runs before testnet.
set -euo pipefail
cd "$(dirname "$0")/.."

# Toolchain: foundry and a python with the requirements installed. Override with
# FORGE_BIN / PYTHON if they live somewhere else.
export PATH="$PATH:${FOUNDRY_BIN:-$HOME/.foundry/bin}"
PYTHON="${PYTHON:-python3}"
command -v forge >/dev/null || { echo "forge not found: run 'foundryup' or set FOUNDRY_BIN"; exit 1; }

# 1. build artifacts
(cd contracts && forge build --quiet)

# 2. start anvil on the testnet EVM chain id
anvil --chain-id 9800 --port 8545 --silent &
ANVIL_PID=$!
trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  if curl -s -m 1 -X POST http://127.0.0.1:8545 -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' >/dev/null; then break; fi
  sleep 0.25
done

# 3. run the integration script
PYTHONPATH=agent "$PYTHON" scripts/local_integration.py

# 4. render the dashboard from the local deployment (a preview, not evidence)
QOREGUARD_DEPLOYMENT=.keys/deployment.local.json \
QORE_EVM_RPC=http://127.0.0.1:8545 \
QORE_EVM_CHAIN_ID=9800 \
QOREGUARD_OUT=web/preview.html \
PYTHONPATH=agent "$PYTHON" scripts/render_dashboard.py
