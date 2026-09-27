"""Check an agent-to-agent handoff chain and the tool call at the end of it.

Deterministic, no model. See FORMAT.md for the input and PRD.md for why.
"""

from __future__ import annotations

from datetime import datetime

ACT, ESCALATE, BLOCK = "ACT", "ESCALATE", "BLOCK"
_RANK = {ACT: 0, ESCALATE: 1, BLOCK: 2}
_KNOWN_OPS = {"eq", "in", "max", "min", "any"}
_GRANT_FIELDS = ("id", "issued_by", "principal", "holder", "actions", "expires_at", "delegations_left")


def _reason(decision: str, rule: str, hop: str | None, detail: str) -> dict:
    return {"decision": decision, "rule": rule, "hop": hop, "detail": detail}


def _time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# --- constraints ---------------------------------------------------------

def _constraint_problem(c) -> str | None:
    """Why a constraint can't be checked, or None if it's well formed."""
    if not isinstance(c, dict) or not c:
        return "constraint must be a non-empty object"
    unknown = set(c) - _KNOWN_OPS
    if unknown:
        return f"unknown constraint {sorted(unknown)}"
    if "any" in c and (c["any"] is not True or len(c) > 1):
        return '"any" must be true and stand alone'
    if "in" in c and not isinstance(c["in"], list):
        return '"in" must be a list'
    for op in ("max", "min"):
        if op in c and not _is_num(c[op]):
            return f'"{op}" must be a number'
    return None


def _same(a, b) -> bool:
    """Equality that never lets a boolean match a number (in Python, True == 1)."""
    return isinstance(a, bool) == isinstance(b, bool) and a == b


def _member(v, values: list) -> bool:
    return any(_same(v, x) for x in values)


def _bounds(c: dict) -> tuple:
    """(low, high) for a range. A max with no min means a floor of 0: a limit on an amount
    doesn't authorize its negative. State a min to allow negative numbers."""
    return c.get("min", 0 if "max" in c else None), c.get("max")


def _finite(c: dict) -> list | None:
    """The allowed values if the constraint lists them, else None."""
    if "eq" in c and "in" in c:
        return [c["eq"]] if _member(c["eq"], c["in"]) else []
    if "eq" in c:
        return [c["eq"]]
    if "in" in c:
        return list(c["in"])
    return None


def _satisfies(c: dict, v) -> bool:
    if c.get("any") is True:
        return True
    if "eq" in c and not _same(v, c["eq"]):
        return False
    if "in" in c and not _member(v, c["in"]):
        return False
    low, high = _bounds(c)
    if low is not None or high is not None:
        if not _is_num(v):
            return False
        if (low is not None and v < low) or (high is not None and v > high):
            return False
    return True


def _narrower(parent: dict, child: dict) -> bool:
    """True if everything the child allows, the parent allows too."""
    if parent.get("any") is True:
        return True
    if child.get("any") is True:
        return False
    values = _finite(child)
    if values is not None:
        return all(_satisfies(parent, v) for v in values)
    if _finite(parent) is not None:
        return False  # a range can't fit inside a list of values
    plow, phigh = _bounds(parent)
    clow, chigh = _bounds(child)
    if phigh is not None and not (chigh is not None and chigh <= phigh):
        return False
    if plow is not None and not (clow is not None and clow >= plow):
        return False
    return True


# --- chain ---------------------------------------------------------------

def _chain(grants: list, start: str, reasons: list) -> list[dict] | None:
    """Grants from the root down to `start`, or None if the chain is broken."""
    by_id: dict = {}
    for g in grants:
        gid = g.get("id") if isinstance(g, dict) else None
        if gid is None or gid in by_id:
            reasons.append(_reason(BLOCK, "grant.bad_id", gid, "every grant needs a unique id"))
            return None
        by_id[gid] = g
    chain, seen, gid = [], set(), start
    while gid is not None:
        if gid in seen:
            reasons.append(_reason(BLOCK, "grant.cycle", gid, "the chain loops back on itself"))
            return None
        if gid not in by_id:
            reasons.append(_reason(BLOCK, "grant.missing", gid, f"grant {gid!r} isn't in the scenario"))
            return None
        seen.add(gid)
        chain.append(by_id[gid])
        gid = by_id[gid].get("parent")
    return list(reversed(chain))


def _check_grant_shape(g: dict, reasons: list) -> bool:
    gid = g["id"]
    ok = True
    for f in _GRANT_FIELDS:
        if f not in g:
            reasons.append(_reason(BLOCK, "grant.missing_field", gid, f"missing {f!r}"))
            ok = False
    if not ok:
        return False
    if _time(g["expires_at"]) is None:
        reasons.append(_reason(BLOCK, "grant.bad_expiry", gid, "expires_at must be an ISO 8601 time with a timezone"))
        ok = False
    dl = g["delegations_left"]
    if not (isinstance(dl, int) and not isinstance(dl, bool) and dl >= 0):
        reasons.append(_reason(BLOCK, "grant.bad_delegations", gid, "delegations_left must be a whole number ≥ 0"))
        ok = False
    if not isinstance(g["actions"], dict):
        reasons.append(_reason(BLOCK, "grant.bad_actions", gid, "actions must be an object"))
        return False
    for tool, args in g["actions"].items():
        if not isinstance(args, dict):
            reasons.append(_reason(BLOCK, "grant.bad_actions", gid, f"{tool}: constraints must be an object"))
            ok = False
            continue
        for arg, c in args.items():
            problem = _constraint_problem(c)
            if problem:
                reasons.append(_reason(BLOCK, "grant.unknown_constraint", gid, f"{tool}.{arg}: {problem}"))
                ok = False
    return ok


def _check_hop(parent: dict, child: dict, reasons: list) -> None:
    cid = child["id"]
    if child["issued_by"] != parent["holder"]:
        reasons.append(_reason(BLOCK, "hop.not_holder", cid,
                               f"{child['issued_by']!r} handed on grant {parent['id']!r}, which belongs to {parent['holder']!r}"))
    if child["principal"] != parent["principal"]:
        reasons.append(_reason(BLOCK, "hop.principal_switch", cid,
                               f"principal changed from {parent['principal']!r} to {child['principal']!r}"))
    if parent["delegations_left"] < 1:
        reasons.append(_reason(BLOCK, "hop.no_delegations_left", cid, f"{parent['id']!r} had no handoffs left"))
    elif child["delegations_left"] > parent["delegations_left"] - 1:
        reasons.append(_reason(BLOCK, "hop.more_delegations", cid,
                               f"delegations_left {child['delegations_left']} > parent's {parent['delegations_left']} − 1"))
    if _time(child["expires_at"]) > _time(parent["expires_at"]):
        reasons.append(_reason(BLOCK, "hop.later_expiry", cid, "expires after its parent"))
    for tool, cargs in child["actions"].items():
        pargs = parent["actions"].get(tool)
        if pargs is None:
            reasons.append(_reason(BLOCK, "hop.new_action", cid, f"adds {tool!r}, which the parent doesn't have"))
            continue
        for arg, pc in pargs.items():
            if arg not in cargs:
                reasons.append(_reason(BLOCK, "hop.dropped_constraint", cid, f"{tool}.{arg}: parent's constraint dropped"))
            elif not _narrower(pc, cargs[arg]):
                reasons.append(_reason(BLOCK, "hop.wider_constraint", cid,
                                       f"{tool}.{arg}: {cargs[arg]} is wider than parent's {pc}"))
        for arg in set(cargs) - set(pargs):
            if cargs[arg].get("any") is True:
                reasons.append(_reason(BLOCK, "hop.any_added", cid, f"{tool}.{arg}: 'any' on an argument the parent didn't mention"))


# --- the check -----------------------------------------------------------

def check(scenario: dict) -> dict:
    """Return {'decision': ACT|ESCALATE|BLOCK, 'reasons': [...], 'chain': [grant ids]}."""
    reasons: list[dict] = []
    grants = scenario.get("grants") or []
    policy = scenario.get("policy") or {}
    call = scenario.get("call") or {}

    chain = _chain(grants, call.get("grant"), reasons) if call.get("grant") else None
    if chain is None and not reasons:
        reasons.append(_reason(BLOCK, "call.no_grant", "call", "the call names no grant"))
    if chain is None:
        return _result(reasons, [])

    ids = [g["id"] for g in chain]
    if not all(_check_grant_shape(g, reasons) for g in chain):
        return _result(reasons, ids)

    root = chain[0]
    if root["issued_by"] not in (policy.get("trusted_issuers") or []):
        reasons.append(_reason(BLOCK, "root.untrusted_issuer", root["id"], f"{root['issued_by']!r} isn't a trusted issuer"))
    for parent, child in zip(chain, chain[1:]):
        _check_hop(parent, child, reasons)

    _check_call(chain, policy, call, reasons)
    return _result(reasons, ids)


def _check_call(chain: list, policy: dict, call: dict, reasons: list) -> None:
    last = chain[-1]
    if call.get("agent") != last["holder"]:
        reasons.append(_reason(BLOCK, "call.wrong_agent", last["id"],
                               f"{call.get('agent')!r} used a grant held by {last['holder']!r}"))
    if call.get("on_behalf_of") != last["principal"]:
        reasons.append(_reason(BLOCK, "call.principal_mismatch", last["id"],
                               f"call is for {call.get('on_behalf_of')!r}, the grant is for {last['principal']!r}"))
    at = _time(call.get("at"))
    if at is None:
        reasons.append(_reason(BLOCK, "call.bad_time", "call", "call.at must be an ISO 8601 time with a timezone"))
    else:
        for g in chain:
            if at > _time(g["expires_at"]):
                reasons.append(_reason(BLOCK, "call.expired", g["id"], f"grant expired at {g['expires_at']}"))

    tool, args = call.get("tool"), call.get("arguments") or {}
    tools = policy.get("tools") or {}
    if tool not in tools:
        reasons.append(_reason(BLOCK, "policy.unknown_tool", "policy", f"{tool!r} isn't in the policy"))
    else:
        rules = tools[tool] or {}
        caps, floors = rules.get("max") or {}, rules.get("min") or {}
        for arg in set(caps) | set(floors):
            if arg not in args:
                continue
            low, high = _bounds({k: v for k, v in (("min", floors.get(arg)), ("max", caps.get(arg))) if v is not None})
            v = args[arg]
            if not _is_num(v):
                reasons.append(_reason(BLOCK, "policy.over_cap", "policy", f"{arg}={v!r} isn't a number, so the policy limits can't be checked"))
            elif high is not None and v > high:
                reasons.append(_reason(BLOCK, "policy.over_cap", "policy", f"{arg}={v!r} is over the policy cap of {high}"))
            elif low is not None and v < low:
                reasons.append(_reason(BLOCK, "policy.under_floor", "policy", f"{arg}={v!r} is under the policy floor of {low}"))
        for arg, limit in (rules.get("approval_over") or {}).items():
            if arg in args and _is_num(args[arg]) and args[arg] > limit:
                reasons.append(_reason(ESCALATE, "policy.approval_needed", "policy", f"{arg}={args[arg]} is over {limit}, which needs approval"))

    for g in chain:
        constraints = g["actions"].get(tool)
        if constraints is None:
            reasons.append(_reason(BLOCK, "call.tool_not_granted", g["id"], f"{tool!r} isn't in this grant"))
            continue
        for arg, c in constraints.items():
            if arg not in args:
                if c.get("any") is not True:
                    reasons.append(_reason(BLOCK, "call.argument_missing", g["id"], f"{arg!r} is constrained but missing from the call"))
            elif not _satisfies(c, args[arg]):
                reasons.append(_reason(BLOCK, "call.argument_outside", g["id"], f"{arg}={args[arg]!r} is outside {c}"))
        for arg in set(args) - set(constraints):
            reasons.append(_reason(ESCALATE, "call.argument_unchecked", g["id"], f"{arg!r} isn't mentioned by this grant"))

    # Every number must be bounded on both sides by something the business wrote.
    rules = tools.get(tool) or {}
    for arg, v in args.items():
        if not _is_num(v):
            continue
        sources = [g["actions"].get(tool, {}).get(arg) or {} for g in chain]
        sources.append({k: rules[k][arg] for k in ("min", "max") if arg in (rules.get(k) or {})})
        bounds = [(0, 0) if _finite(c) is not None else _bounds(c) for c in sources if c.get("any") is not True]
        missing = [side for side, i in (("lower", 0), ("upper", 1)) if all(b[i] is None for b in bounds)]
        if missing:
            reasons.append(_reason(ESCALATE, "call.unbounded_number", "call",
                                   f"{arg}={v}: no {' or '.join(missing)} limit anywhere in the chain or the policy"))


def _result(reasons: list, chain: list) -> dict:
    decision = max((r["decision"] for r in reasons), key=_RANK.__getitem__, default=ACT)
    return {"decision": decision, "reasons": reasons, "chain": chain}
