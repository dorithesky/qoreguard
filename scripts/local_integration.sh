#!/usr/bin/env bash
# Local end-to-end check against anvil with the PQC precompiles mocked.
# Nothing here touches a network; it is the cheap check that runs before testnet.
set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="$PATH:/workspace/.foundry/bin"

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
PYTHONPATH=agent /workspace/.venv-qore/bin/python scripts/local_integration.py
