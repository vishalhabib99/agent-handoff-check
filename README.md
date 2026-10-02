# agent-handoff-check

Checks every **agent-to-agent handoff**, and the tool call at the end of the chain, against what the customer actually authorized. Returns **ACT**, **ESCALATE** (a person decides) or **BLOCK**, with every reason and the hop it came from.

A customer asks a support agent for a refund. The support agent hands the task to a billing agent, which hands it to a refunds agent, which calls the refunds tool. Every hop is a language model deciding what to pass on, and one poisoned support ticket can steer all of them. Nothing in that chain checks that the last agent is still acting for *that* customer, on *that* order, for *that* amount. This does.

**[Try it in your browser →](https://vishalhabib99.github.io/agent-handoff-check/)** Six scenarios, from a normal refund to a poisoned support ticket. Runs locally, no account, no tracking.

> Reference build by an AI product manager: [PRD](PRD.md) → [eval plan with gates set first](EVAL_PLAN.md) → build → **blind evals**. Deterministic, no model, no API cost.

## The rule: authority can only narrow

The customer's login issues a **root grant**: which tools, which arguments, until when, and how many more handoffs. Every handoff issues a **child grant**, and a child can only be narrower than its parent. When the tool call arrives, the **whole chain** is re-checked, not just the last hop.

```
customer (cust_42) ──login──▶ support-agent      refunds.create  order A100, ≤ $200, 2 handoffs left
                                   │ hands on
                                   ▼
                              billing-agent      refunds.create  order A100, ≤ $80,  1 handoff left
                                   │ hands on (after reading a poisoned ticket)
                                   ▼
                              refunds-agent      refunds.create  any order, ≤ $900, refund_to: any   ◀── BLOCK
```

| What goes wrong | Example | Decision |
|---|---|---|
| **Widening at a hop** | a higher limit, a dropped constraint, a new tool, a later expiry, more handoffs | BLOCK |
| **Confused deputy** | a hop switches to another customer, or an agent hands on a grant it doesn't hold | BLOCK |
| **Drift at the call** | a different order, a bigger amount, the wrong agent, an expired grant | BLOCK |
| **Policy hard cap** | a $600 refund when policy caps refunds at $500, whatever the grant says | BLOCK |
| **Can't be checked** | an argument no grant mentions (`refund_to: acct_7781`), a constraint it doesn't understand | ESCALATE / BLOCK |
| **Needs approval** | inside the customer's authority, but over the policy's approval threshold | ESCALATE |

The worst reason wins: one BLOCK makes the decision BLOCK, whatever else passed.

## Results

Gates were [set before any code](EVAL_PLAN.md). Each held-out set was written by a separate agent that never saw the code, and was committed before its first run.

| Run | Top harm: unauthorized marked ACT (gate 0) | Friction: clean calls stopped (gate ≤ 10%) | Needs a person, marked ACT (gate 0) |
|---|---|---|---|
| **v0.1**, held-out 1 (40 cases), first run | 0 of 18: PASS | 0 of 14: PASS | 0 of 8: PASS |
| **v0.1**, red team with the code open | **3 holes marked ACT: FAIL** | | |
| **v0.2**, held-out 2 (40 fresh cases), first run | **0 of 18: PASS** | 0 of 14: PASS | 0 of 8: PASS |

**The first pass wasn't enough.** Held-out 1 was written from the same spec the checker implements, so passing it showed the checker follows its spec, not that the spec can't be gamed. A red-team agent with the code open found three calls that got ACT with no reasons at all:

- a **negative $500 refund**, because every limit was written as a `max` and nothing said amounts can't go below zero;
- a **$999,999,999 credit**, because the grant only set a `min` and the policy had no cap;
- `true` accepted as order `1`, because Python treats them as equal.

**What changed:** not a patch per case, but one rule: **every number must be bounded on both sides by something the business wrote.** A `max` with no `min` now implies a floor of 0, a number with no upper or lower limit anywhere goes to a person, and booleans never match numbers. Everything else the red team tried held up: lookalike issuer names, duplicate ids, NaN and Infinity, and widened middle hops, which the call-time re-check of the root grant caught every time.

Held-out 2 was written fresh for v0.2 and included attacks on the new number rules. One disclosure: its author read the eval plan's changelog, which summarizes the three red-team holes, but not the code or any earlier cases. Logs: [`evals/heldout_first_run.txt`](evals/heldout_first_run.txt), [`evals/redteam_v01_run.txt`](evals/redteam_v01_run.txt), [`evals/heldout2_first_run.txt`](evals/heldout2_first_run.txt).

**How much "0 of 18" proves.** It passes the gate, but with 0 unauthorized calls through in 18, the true rate could still be as high as **15%**, and 0 of 14 legitimate calls stopped still allows friction up to **19%** (one-sided 95% exact bounds). Showing unauthorized calls get through less than 1% of the time would take **299** in a row with none through.

**Shadow-mode status.** The exit rule in [PRD §8](PRD.md#8-rollout) was fixed before any shadow data exists. This table is rebuilt from [`shadow/log.jsonl`](shadow/log.jsonl) by `python shadow/status.py`, never edited by hand, and CI fails if the two disagree. Any change to the rule after a miss shows up in the history.

<!-- shadow-status -->
| Run | Checker | Cases reviewed | Misses | Exit at | Status |
|---|---|---|---|---|---|
| – | – | 0 | 0 | 381 | Not started: no shadow traffic yet |
<!-- /shadow-status -->

**Limits of this evidence:** 80 blind cases plus 3 red-team cases, all synthetic, all written by one model family, in one domain (SaaS support). Most rules are covered by one to three cases each. It hasn't run against a real agent framework's traffic.

## Use it

```bash
pip install git+https://github.com/vishalhabib99/agent-handoff-check
```

```python
from agent_handoff_check import check, AuditLog

result = check(scenario)          # grants + policy + call, see FORMAT.md
result["decision"]                # "ACT" | "ESCALATE" | "BLOCK"
result["reasons"]                 # [{"decision", "rule", "hop", "detail"}, ...]
AuditLog("audit.jsonl").write(scenario, result)
```

```bash
agent-handoff-check check examples/refund_injected.json    # exit 0 ACT, 1 ESCALATE, 2 BLOCK
agent-handoff-check verify audit.jsonl                     # detects edited, deleted or reordered records
```

Input format: [FORMAT.md](FORMAT.md). Run the evals: `python evals/run.py evals/heldout2.jsonl`

## Where it sits

The check belongs in the **orchestrator**, on the path every tool call already takes, not inside an agent. An agent asked to check its own authority can be talked out of it by the same prompt injection this is meant to stop.

It's the multi-agent layer of the same idea behind [mcp-trust-check](https://github.com/vishalhabib99/mcp-trust-check)'s per-call ACT / ESCALATE / BLOCK decision, [retirement-answer-check](https://github.com/vishalhabib99/retirement-answer-check) and [listing-claim-check](https://github.com/vishalhabib99/listing-claim-check): check what an AI does against a source of truth the business already has. Here the source of truth is the customer's own authorization and the company's policy.

## Built on public techniques

Attenuation (a capability can be narrowed as it's handed on, never widened) is the rule behind capability tokens such as [macaroons](https://research.google/pubs/macaroons-cookies-with-contextual-caveats-for-decentralized-authorization-in-the-cloud/) and [OAuth 2.0 Token Exchange (RFC 8693)](https://www.rfc-editor.org/rfc/rfc8693). Hash-chained logs are standard tamper evidence. This repo applies them to agent handoffs. Related: the author is the sole inventor on U.S. provisional patent application No. 63/980,243 on agentic AI orchestration and governance. This repo implements only the public techniques above.

## Limits

- **It trusts the root grant.** Whether the customer really asked for a $200 refund is the login's and the product's job.
- **It checks structure, not intent.** A refund inside every limit, for the right customer and order, is ACT even if a manipulated agent chose to issue it. That's why the policy's approval threshold exists.
- **Grants are JSON here, not signed tokens.** In production, sign them (or use RFC 8693 tokens) so a hop can't forge its parent.

## License

MIT
