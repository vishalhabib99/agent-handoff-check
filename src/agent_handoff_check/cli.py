"""agent-handoff-check check scenario.json [--log audit.jsonl]  ·  agent-handoff-check verify audit.jsonl

Exit codes for `check`: 0 ACT, 1 ESCALATE, 2 BLOCK, so a pipeline can gate on it.
"""

from __future__ import annotations

import argparse
import json
import sys

from .audit import AuditLog, verify
from .checker import check

_EXIT = {"ACT": 0, "ESCALATE": 1, "BLOCK": 2}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent-handoff-check")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check one scenario (a JSON file, or - for stdin)")
    c.add_argument("scenario")
    c.add_argument("--log", help="append the decision to this audit log")
    v = sub.add_parser("verify", help="verify an audit log's hash chain")
    v.add_argument("log")
    args = p.parse_args(argv)

    if args.cmd == "verify":
        ok, msg = verify(args.log)
        print(("OK: " if ok else "TAMPERED: ") + msg)
        return 0 if ok else 3

    scenario = json.load(sys.stdin if args.scenario == "-" else open(args.scenario))
    result = check(scenario)
    if args.log:
        AuditLog(args.log).write(scenario, result)
    print(json.dumps(result, indent=2))
    return _EXIT[result["decision"]]


if __name__ == "__main__":
    sys.exit(main())
