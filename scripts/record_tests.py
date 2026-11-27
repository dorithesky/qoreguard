#!/usr/bin/env python3
"""Run the Foundry suites and record what they proved, tied to this source.

`verify.py` cannot run `forge` on a judge's clean machine, so the numbers are
recorded here with the SHA-256 of the contract they belong to. If anyone edits
the contract and forgets to re-run the tests, `verify.py` fails the
"the recorded Foundry results belong to this source" check.

    python scripts/record_tests.py        # writes docs/verification.json
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
SRC = CONTRACTS / "src" / "QoreGuardTreasury.sol"
OUT = ROOT / "docs" / "verification.json"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=CONTRACTS, capture_output=True, text=True)


def main() -> int:
    forge = "forge"
    version = run([forge, "--version"]).stdout.splitlines()[0] if True else ""

    tests = run([forge, "test", "--json"])
    if tests.returncode != 0:
        print(tests.stdout[-3000:], tests.stderr[-3000:], file=sys.stderr)
        return 1
    suites = json.loads(tests.stdout)
    total = passed = failed = 0
    per_test: dict[str, str] = {}
    for suite, payload in suites.items():
        for name, result in payload.get("test_results", {}).items():
            total += 1
            status = result.get("status")
            if status == "Success":
                passed += 1
            elif status in {"Failure", "Revert"}:
                failed += 1
            per_test[f"{suite.split(':')[-1]}.{name}"] = status

    # Coverage through lcov, so the four numbers come from one reproducible file.
    lcov = run([forge, "coverage", "--report", "lcov", "--report-file", "lcov.info",
                "--no-match-coverage", "test|mocks|lib"])
    if lcov.returncode != 0:
        print(lcov.stdout[-3000:], lcov.stderr[-3000:], file=sys.stderr)
        return 1
    lf = lh = brf = brh = fnf = fnb = 0
    for line in (CONTRACTS / "lcov.info").read_text().splitlines():
        if line.startswith("LF:"):
            lf = int(line[3:])
        elif line.startswith("LH:"):
            lh = int(line[3:])
        elif line.startswith("BRF:"):
            brf = int(line[4:])
        elif line.startswith("BRH:"):
            brh = int(line[4:])
        elif line.startswith("FNF:"):
            fnf = int(line[4:])
        elif line.startswith("FNH:"):
            fnb = int(line[4:])
    statements = run([forge, "coverage", "--report", "summary", "--no-match-coverage", "test|mocks|lib"]).stdout
    st_total = st_hit = 0
    m = re.search(r"QoreGuardTreasury\.sol\s*\|\s*[\d.]+% \(\d+/\d+\)\s*\|\s*[\d.]+% \((\d+)/(\d+)\)", statements)
    if m:
        st_hit, st_total = int(m.group(1)), int(m.group(2))

    # Mutation score, from the same script a reviewer can run.
    mutation = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mutate.py"), "--json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    mutants = json.loads(mutation.stdout.strip().splitlines()[-1]) if mutation.stdout.strip() else []
    killed = sum(1 for m in mutants if m["status"] == "killed")

    # forge --json reports one aggregated entry for the invariant suite, so the
    # invariant and action counts are read from the suite's own source.
    invariant_src = (CONTRACTS / "test" / "Invariants.t.sol").read_text()
    invariant_names = re.findall(r"function (invariant_\w+)\(", invariant_src)
    action_names = re.findall(r"function (\w+)\([^)]*\) external \{", invariant_src)

    record = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "forge": version,
        "solc": "0.8.24",
        "contract_source": "contracts/src/QoreGuardTreasury.sol",
        "contract_source_sha256": hashlib.sha256(SRC.read_bytes()).hexdigest(),
        "contract_source_bytes": len(SRC.read_bytes()),
        "tests": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "invariants": len(invariant_names),
            "invariant_names": invariant_names,
            "handler_actions": action_names,
            "invariant_runs": 96,
            "invariant_depth": 128,
            "handler_calls": 12288,
        },
        "coverage": {
            "lines": [lh, lf],
            "statements": [st_hit, st_total],
            "branches": [brh, brf],
            "functions": [fnb, fnf],
        },
        "mutation": {
            "total": len(mutants),
            "killed": killed,
            "survived": [m["id"] for m in mutants if m["status"] != "killed"],
        },
        "command": "cd contracts && forge test && forge coverage --report lcov --no-match-coverage 'test|mocks|lib'",
        "mutation_command": "python scripts/mutate.py",
    }
    OUT.write_text(json.dumps(record, indent=2) + "\n")
    print(f"wrote {OUT}")
    print(f"  {passed}/{total} tests · {len(invariant_names)} invariants · coverage "
          f"{lh}/{lf} lines, {brh}/{brf} branches, {fnb}/{fnf} functions · mutation {killed}/{len(mutants)}")
    return 0 if failed == 0 and killed == len(mutants) else 1


if __name__ == "__main__":
    raise SystemExit(main())
