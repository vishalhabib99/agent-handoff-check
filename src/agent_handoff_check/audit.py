"""A tamper-evident record of every handoff chain checked, one JSON object per line.

Each record carries the SHA-256 of the previous record ("prev") and of itself ("hash"), so
editing, deleting or reordering any line breaks the chain from that point on, and verify()
says where. Tamper-evident, not tamper-proof: someone who can rewrite the whole file can
rebuild the chain, so anchor the last hash somewhere else if that matters.

A record keeps who handed what to whom (grant ids, holders, issuers and a hash of each
grant), the call's tool and argument names, and the decision with its rules. Argument values
are left out by default because they can hold customer data; pass log_arguments=True to keep them.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


def _record_hash(record: dict) -> str:
    return _sha({k: v for k, v in record.items() if k != "hash"})


class AuditLog:
    def __init__(self, path: str | Path, log_arguments: bool = False):
        self.path = Path(path)
        self.log_arguments = log_arguments
        self._prev = self._last_hash()

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = None
        with self.path.open() as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last)["hash"] if last else GENESIS

    def write(self, scenario: dict, result: dict) -> dict:
        grants = {g.get("id"): g for g in scenario.get("grants") or [] if isinstance(g, dict)}
        call = scenario.get("call") or {}
        arguments = call.get("arguments") or {}
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "chain": [
                {"id": gid, "issued_by": grants[gid].get("issued_by"), "holder": grants[gid].get("holder"),
                 "sha256": _sha(grants[gid])}
                for gid in result["chain"]
            ],
            "agent": call.get("agent"),
            "on_behalf_of": call.get("on_behalf_of"),
            "tool": call.get("tool"),
            "argument_names": sorted(arguments),
            "arguments_sha256": _sha(arguments),
            "decision": result["decision"],
            "rules": [f"{r['rule']}@{r['hop']}" for r in result["reasons"]],
            "prev": self._prev,
        }
        if self.log_arguments:
            record["arguments"] = arguments
        record["hash"] = _record_hash(record)
        with self.path.open("a") as f:
            f.write(_canonical(record) + "\n")
        self._prev = record["hash"]
        return record


def verify(path: str | Path) -> tuple[bool, str]:
    """(True, summary) if every record's hash and link check out, else (False, where it breaks)."""
    prev, n = GENESIS, 0
    with Path(path).open() as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return False, f"line {lineno}: not valid JSON"
            if record.get("prev") != prev:
                return False, f"line {lineno}: chain broken (a record before it was edited, removed or reordered)"
            if record.get("hash") != _record_hash(record):
                return False, f"line {lineno}: contents don't match its hash (this record was edited)"
            prev = record["hash"]
            n += 1
    return True, f"{n} records, chain intact, last hash {prev[:16]}…"
