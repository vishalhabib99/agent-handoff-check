# Input format

One scenario is a JSON object: the grants in the chain, the company's policy, and the tool call to check.

```json
{
  "grants": [
    {
      "id": "g1", "parent": null, "issued_by": "auth-service",
      "principal": "cust_42", "holder": "support-agent",
      "actions": {
        "orders.lookup": {"order_id": {"in": ["A100"]}},
        "refunds.create": {"order_id": {"in": ["A100"]}, "amount": {"max": 200}, "reason": {"any": true}}
      },
      "expires_at": "2026-09-26T18:00:00Z", "delegations_left": 2
    },
    {
      "id": "g2", "parent": "g1", "issued_by": "support-agent",
      "principal": "cust_42", "holder": "billing-agent",
      "actions": {
        "refunds.create": {"order_id": {"eq": "A100"}, "amount": {"max": 80}, "reason": {"any": true}}
      },
      "expires_at": "2026-09-26T17:00:00Z", "delegations_left": 0
    }
  ],
  "policy": {
    "trusted_issuers": ["auth-service"],
    "tools": {
      "orders.lookup": {},
      "refunds.create": {"approval_over": {"amount": 100}, "max": {"amount": 500}}
    }
  },
  "call": {
    "agent": "billing-agent", "grant": "g2", "on_behalf_of": "cust_42",
    "tool": "refunds.create",
    "arguments": {"order_id": "A100", "amount": 80, "reason": "damaged"},
    "at": "2026-09-26T16:30:00Z"
  }
}
```

## Grants

| Field | Meaning |
|---|---|
| `id` | Unique within the scenario |
| `parent` | The grant this one was handed on from; `null` for the customer's root grant |
| `issued_by` | Root: the service that authenticated the customer (must be in `policy.trusted_issuers`). Child: the agent handing it on (must be the parent's `holder`) |
| `principal` | The customer the work is for. Must be the same on every grant in the chain |
| `holder` | The agent allowed to use this grant |
| `actions` | Tool name → argument constraints |
| `expires_at` | ISO 8601 UTC. A child can't outlive its parent |
| `delegations_left` | How many more handoffs are allowed. A child needs a parent with at least 1 and must have at most the parent's value minus 1 |

## Argument constraints

| Constraint | Allows |
|---|---|
| `{"eq": v}` | Exactly `v` |
| `{"in": [v1, v2]}` | One of the listed values |
| `{"max": n}` / `{"min": n}` | A number at most / at least `n`. Both can be given |
| `{"any": true}` | Any value, stated explicitly |

**Narrowing rules for a child grant:** it can drop whole actions, but not add one. For each argument the parent constrains, the child must constrain it at least as strictly (a smaller `in` set, an `eq` inside the parent's set or range, a lower `max`, a higher `min`). Leaving out a constraint the parent had counts as widening. The child can't give `any` to an argument the parent didn't mention.

## Policy

| Field | Meaning |
|---|---|
| `trusted_issuers` | Services allowed to issue root grants |
| `tools` | Every tool agents may call. A tool not listed is blocked |
| `tools.<name>.max` | Argument → hard cap. Above it → BLOCK, whatever the grant says |
| `tools.<name>.approval_over` | Argument → threshold. Above it → ESCALATE |

## The call

| Field | Meaning |
|---|---|
| `agent` | The agent making the call. Must be the final grant's `holder` |
| `grant` | The grant it's using. The chain is followed from here to the root |
| `on_behalf_of` | The customer. Must match the chain's `principal` |
| `tool`, `arguments` | The tool call |
| `at` | When the call happens (ISO 8601 UTC) |

## Decision

- **BLOCK:** any hop or the call goes outside the customer's authority or the policy's hard limits, or something can't be checked at all (an unknown constraint, a missing parent, a constrained argument missing from the call).
- **ESCALATE:** inside authority, but above an `approval_over` threshold, or the call has an argument that some grant in the chain doesn't mention.
- **ACT:** everything checks out.

The worst reason wins. Every reason names its rule and the grant (hop) it came from.
