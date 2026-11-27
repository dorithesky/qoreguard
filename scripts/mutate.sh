#!/usr/bin/env bash
# Mutation-test the treasury policy. Every mutant must be killed.
#   bash scripts/mutate.sh           # all mutants
#   bash scripts/mutate.sh --list
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="${HOME}/.foundry/bin:/workspace/.foundry/bin:${PATH}"
exec python3 scripts/mutate.py "$@"
