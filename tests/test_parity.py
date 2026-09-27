"""The web demo (docs/checker.js) and the Python checker must reach the same decision, rules and hops."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_handoff_check import check

from test_checker import BASE

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _cases():
    rows = []
    for f in sorted((ROOT / "evals").glob("*.jsonl")) + sorted((ROOT / "examples").glob("*.json")):
        text = f.read_text()
        rows += [json.loads(text)] if f.suffix == ".json" else [json.loads(l) for l in text.splitlines() if l.strip()]
    rows.append(BASE)
    for s in json.loads((ROOT / "docs/scenarios.json").read_text()):
        rows.append(s["scenario"])
    # a few hand-made edge cases
    for mutate in (lambda s: s["grants"][1].update(id="g1"), lambda s: s["call"].update(grant=""),
                   lambda s: s["grants"][0].pop("holder"), lambda s: s["call"]["arguments"].update(amount=True),
                   lambda s: s["grants"][1]["actions"]["refunds.create"].update(amount={}),
                   lambda s: s["call"].update(at="2026-09-26T16:30:00+02:00")):
        s = copy.deepcopy(BASE)
        mutate(s)
        rows.append(s)
    return rows


def _summary(r):
    return r["decision"], sorted([x["decision"], x["rule"], str(x["hop"])] for x in r["reasons"]), [str(c) for c in r["chain"]]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_matches_python():
    cases = _cases()
    script = ("const C=require(process.argv[1]);const cs=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "console.log(JSON.stringify(cs.map(c=>C.check(c))));")
    out = subprocess.run([NODE, "-e", script, str(ROOT / "docs/checker.js")],
                         input=json.dumps(cases), capture_output=True, text=True, check=True).stdout
    assert len(cases) > 90
    for case, js in zip(cases, json.loads(out)):
        assert _summary(js) == _summary(check(case)), case.get("id", case)
