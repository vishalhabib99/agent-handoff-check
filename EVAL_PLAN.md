# Eval plan: agent-handoff-check

**Decision:** publish v0.1 as a public reference build. **Decision owner:** Vishal Habib (delegated). **Thresholds frozen 2026-09-26, before any code or cases.**

## Test sets

| Set | Who writes it | Used for |
|---|---|---|
| `tests/` | The builder, while building | Development. It will be overfit, so it isn't reported as a result |
| `evals/heldout.jsonl` | **A separate agent that never sees the code**, only the PRD, this plan and the input format. Committed before its first run | The reported result |

Each case is one scenario: the root grant, the handoff grants, the policy, the final tool call, and the expected decision (ACT, ESCALATE or BLOCK). Planted cases also name the harm they contain.

**Slices:**

- **Clean:** every hop narrows or keeps authority, and the call fits. Expected ACT. Includes tricky clean cases: a grant narrowed at every hop, constraints exactly equal to the parent's, the maximum allowed depth, a call at exactly the limit.
- **Unauthorized (top harm):** widening at a hop (a new action, a higher limit, a dropped constraint, a later expiry, more delegation depth), a principal switch, an agent handing on authority it was never given, a call outside the final grant, an expired grant. Expected BLOCK.
- **Needs a person:** inside authority, but the policy requires approval above a threshold, or the call has an argument no grant constrains. Expected ESCALATE.

## Launch gates

| Gate | Metric (held-out, first run) | Threshold | Blocking? |
|---|---|---|---|
| Top harm | Unauthorized cases marked ACT | **0** | **Yes** |
| Friction | Clean cases not marked ACT | ≤ 10% | Yes |
| Escalation | Needs-a-person cases marked ACT | 0 | Yes |
| Explainability | Non-ACT decisions citing the rule and the hop | 100% | Yes |
| Tamper evidence | Edited, deleted or reordered log records detected by `verify` | 100% (tested in `tests/`) | Yes |

**Why 0 and not ≤ 10% (unlike listing-claim-check):** an unauthorized action here moves money or exposes another customer's data, and there's no buyer-protection backstop. Same bar as retirement-answer-check's regulatory errors.

**Reported, not gated:** BLOCK vs ESCALATE mix-ups on non-clean cases. Both stop the action, so the top-harm gate isn't affected, but the wrong one sends the case to the wrong queue.

**If a gate fails:** the result is published as-is, with the first-run log. The fix is a design change, not a special case for each miss.

## Changelog

| Date | Change | Why |
|---|---|---|
| 2026-09-26 | Gates frozen before any code or cases | Pre-registration |
| 2026-09-26 | v0.1 checker frozen (commit `f910515`) before the held-out set existed | So the set can't shape the code |
| 2026-09-26 | `evals/heldout.jsonl` (40 cases: 14 clean, 18 unauthorized, 8 needs a person; chains of 1–4 grants) written by a separate agent that read only the PRD, this plan and FORMAT.md, reporting counts only. Committed before its first run (`67412c0`) | The blind measurement |
| 2026-09-26 | **First blind run: all four gates PASS.** 0/18 unauthorized marked ACT, 0/14 clean stopped, 0/8 needs-a-person marked ACT, 26/26 non-ACT decisions explained, 0 BLOCK/ESCALATE mix-ups. Log: `evals/heldout_first_run.txt` | Caveat: the set was written from the same spec the checker implements, and 19 rules fired with most covered by one case each. It shows the checker follows its spec, not that the spec can't be gamed. That's what the red-team pass is for |
| 2026-09-26 | **Red-team pass on v0.1** by a separate agent that could read the code: **3 confirmed holes, all marked ACT with no reasons.** r01 (high): a max-only limit let a **negative** refund through. r02 (high): a min-only limit with no policy cap let a **999,999,999** credit through. r03 (low): `true` matched `1` because Python treats them as equal. Other attacks held: lookalike issuers, duplicate ids, nested values, NaN/Infinity, whitespace in tool names, widened middle hops (the call-time re-check of the root caught them). Cases: `evals/redteam.jsonl`, log: `evals/redteam_v01_run.txt` | Passing a spec-written set shows the checker follows the spec. This asked whether the spec itself can be gamed. It could |
| 2026-09-26 | **v0.2 design change**, not a patch per case: every number must be bounded on both sides by something the business wrote. A `max` with no `min` implies a floor of 0; a number with no upper or lower limit anywhere in the chain or policy → ESCALATE; policy gains `min`; booleans never match numbers. r01 and r03 now BLOCK, r02 ESCALATE (the grant technically allowed it, so a person decides; either stops the action). Red-team cases and held-out 1 are **no longer blind** for v0.2; held-out 1's re-run (`evals/heldout_after_v02_NOT_BLIND.txt`) still passes every gate, so no regression, but it isn't reported as a result | Same principle as retirement-answer-check and listing-claim-check v0.2: what the source of truth can't back goes to a person |

