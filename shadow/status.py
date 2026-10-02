"""Rebuild the shadow-mode status table in README.md from shadow/log.jsonl and the exit rule in PRD.md §8.

python shadow/status.py          # rewrite the table
python shadow/status.py --check  # exit 1 if the README table doesn't match the log (CI runs this)

The log gets one line per counted case, appended when the review is done and never edited:
  {"run": 1, "checker": "0.2.0", "case": "<id>", "reviewed": "2026-10-02", "miss": false}
Counted cases: unauthorized calls, found by replaying the audit log against the root grants.
"miss": true when the checker would have let that call through (ACT).
Each run uses one checker version. A changed checker is a new run starting from 0.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- shadow-status -->", "<!-- /shadow-status -->"


def schedule(prd: str) -> list[int]:
    """Exit counts by misses so far, read from the PRD table so the rule has one source."""
    rule = prd[prd.index("Shadow-mode exit rule"):]
    counts = {int(m): int(n) for m, n in re.findall(r"^\| (\d+) \| (\d+) \|$", rule, re.M)}
    assert sorted(counts) == list(range(len(counts))) and counts, "PRD §8 exit table not found"
    return [counts[m] for m in range(len(counts))]


def runs(entries: list[dict], exit_at: list[int]) -> list[dict]:
    out = {}
    for e in entries:
        r = out.setdefault(e["run"], {"run": e["run"], "checker": e["checker"], "n": 0, "misses": 0, "status": None})
        if e["checker"] != r["checker"]:
            raise ValueError(f"run {e['run']} mixes checker {r['checker']} and {e['checker']}; a changed checker is a new run")
        if r["status"]:
            raise ValueError(f"run {e['run']} already ended ({r['status']}) at {r['n']} cases; start a new run")
        r["n"] += 1
        r["misses"] += bool(e["miss"])
        if r["misses"] >= len(exit_at):
            r["status"] = "failed"
        elif r["n"] >= exit_at[r["misses"]]:
            r["status"] = "passed"
    return list(out.values())


def table(rs: list[dict], exit_at: list[int]) -> str:
    rows = ["| Run | Checker | Cases reviewed | Misses | Exit at | Status |", "|---|---|---|---|---|---|"]
    if not rs:
        rows.append(f"| – | – | 0 | 0 | {exit_at[0]} | Not started: no shadow traffic yet |")
    for r in rs:
        target = exit_at[r["misses"]] if r["misses"] < len(exit_at) else None
        status = {
            "passed": "Passed the bar",
            "failed": f"Failed ({r['misses']} misses). Stays in the report",
        }.get(r["status"]) or f"In progress, {target - r['n']} to go"
        rows.append(f"| {r['run']} | {r['checker']} | {r['n']} | {r['misses']} | {target or '–'} | {status} |")
    return "\n".join(rows)


def render(root: Path = ROOT) -> tuple[str, str]:
    exit_at = schedule((root / "PRD.md").read_text())
    log = root / "shadow/log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
    readme = (root / "README.md").read_text()
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    return readme, f"{head}{START}\n{table(runs(entries, exit_at), exit_at)}\n{END}{tail}"


def main(argv: list[str]) -> int:
    old, new = render()
    if "--check" in argv:
        if old != new:
            print("README shadow-mode table is out of date: run python shadow/status.py")
            return 1
        return 0
    (ROOT / "README.md").write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
