# PRD: agent-handoff-check

**Owner:** Vishal Habib · **Status:** v0.1 reference build · **Date:** 2026-09-26

## 1. Problem and user

- **User:** the product manager and platform team shipping a multi-agent system, and the risk or compliance reviewer who has to sign off on it.
- **Job to be done:** "Let agents hand work to other agents without any of them ending up able to do more than the customer actually asked for."
- **Today:** a customer asks a support agent for a refund. The support agent hands the task to a billing agent, which calls a refunds tool. Each hop is a language model deciding what to pass on. Nothing checks that the refunds tool is still acting only for *that* customer, on *that* order, for *that* amount. Authority leaks in three ways:
  - **Widening:** a hop passes on more than it received ("refund up to $200" becomes "refund").
  - **Confused deputy:** a hop acts for a different customer than the one who asked, using its own service-level access.
  - **Drift at the call:** the final tool call's arguments don't match what was granted (a different order, a bigger amount, a new destination account).
- Prompt injection makes all three easier: one poisoned document read by one agent can steer every agent after it.
- **The source of truth already exists:** what the authenticated customer asked for, and the company's own refund policy. The check is whether every hop and every call stays inside both.

## 2. Scope (v0.1)

- **Input:** a chain of grants (the customer's root grant plus one grant per handoff) and the tool call at the end of it. JSON, no framework required.
- **Output:** **ACT**, **ESCALATE** (a person decides) or **BLOCK**, with every reason listed and the hop it came from.
- **Demo domain:** SaaS customer support: order lookups, refunds, account credits.
- **Not in scope:** authenticating the customer (the root grant is assumed to come from the product's real login); reading the agents' messages; judging whether a refund is a good idea; enforcing anything at the network layer.

## 3. Autonomy

| Action | Level |
|---|---|
| ACT | Only when every hop narrows authority and the call fits the final grant and the policy |
| ESCALATE | When the call is inside the customer's authority but the policy needs approval, or an argument can't be checked against anything |
| BLOCK | When any hop or the call goes outside the customer's authority. Not overridable by an agent |

## 4. Top-harm error

**A call outside the customer's authority marked ACT.** That is money moved, or data read, that nobody authorized.

## 5. Design

Deterministic, no model. Built only on established, public techniques:

- **Attenuation:** a grant can be narrowed as it's handed on, never widened, the same rule as capability tokens such as macaroons and OAuth token exchange (RFC 8693). Each child grant must have the same principal (the customer), a subset of the parent's actions, constraints at least as strict, an expiry no later, and one less hop of delegation left.
- **Check the whole chain at call time.** Every hop is re-verified when the call arrives, so an earlier check that was skipped or spoofed doesn't matter.
- **Fail closed on what can't be checked.** A constraint operator it doesn't know is a BLOCK. An argument the grant says nothing about is an ESCALATE, not a silent pass. Same principle as retirement-answer-check and listing-claim-check: what the source of truth can't back goes to a person.
- **Worst finding wins.** One BLOCK reason makes the decision BLOCK, whatever else passed, the same rule as mcp-trust-check's release decision.
- **Tamper-evident record.** Each grant and decision is written to a hash-chained log, so a reviewer can replay who handed what to whom.

**Why no model:** the check sits on the path of every tool call and must be cheap, fast and explainable. An LLM deciding whether another LLM is allowed to act would be exposed to the same prompt injection it's meant to stop.

**Related work:** the author is the sole inventor on U.S. provisional patent application No. 63/980,243 on agentic AI orchestration and governance. This repo implements only the public techniques above, not the application's specific mechanisms.

## 6. Success metrics (for a real deployment)

- **Primary:** unauthorized agent actions that reach a system of record (target: 0), measured by replaying the audit log against the root grants.
- **Guardrails:** share of legitimate calls escalated or blocked (friction), added latency per call.

## 7. Evals and launch gates

See [EVAL_PLAN.md](EVAL_PLAN.md). Gates are set before the first run.

## 8. Rollout

Shadow mode (log the decision, don't enforce) → enforce BLOCK on money-moving tools → enforce everywhere. **Kill switch:** fall back to shadow mode.

**Shadow-mode exit rule (set before shadow mode starts).** Bar: unauthorized calls get through less than 1% of the time, at 95% confidence (one-sided exact binomial). A miss is an unauthorized call the checker would have let through (marked ACT), found by replaying the audit log against the root grants. Each run uses one fixed checker version. The run exits when the number of reviewed cases reaches the count for the misses so far:

| Misses so far | Exit at this many reviewed cases |
|---|---|
| 0 | 381 |
| 1 | 571 |
| 2 | 738 |
| 3 | 894 |
| 4 or more | The run fails. Fix the checker and start a new run |

- **Why not stop at the first clean count:** taken alone, 0 misses in 299 meets the bar. But checking at each row in turn gives the checker four chances to pass, and a checker whose true rate is exactly 1% would then exit about 11% of the time instead of 5%. The counts above are set so the whole schedule, all four chances together, stays at 5% (exact, summed over every path a run can take). The price is 82 more cases on a clean run.
- **A miss doesn't restart the count.** If a miss leads to a change in the checker, that's a new version and a new run starting from 0, and the stopped run stays in the report with its count and its misses. Restarting would keep only the runs that happened to finish clean.
- **The result covers only what the run sampled**, which is live traffic. Blind-set and red-team results are bounds on other populations and are reported next to it, not merged into it.
- The continuation rule came out of [a reader's review](https://dev.to/arhancanli/comment/3g81b) of the "0 of N" write-up.
