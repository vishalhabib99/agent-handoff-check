import copy
import json

import pytest

from agent_handoff_check import ACT, BLOCK, ESCALATE, AuditLog, check, verify

BASE = {
    "grants": [
        {"id": "g1", "parent": None, "issued_by": "auth-service", "principal": "cust_42", "holder": "support-agent",
         "actions": {"orders.lookup": {"order_id": {"in": ["A100"]}},
                     "refunds.create": {"order_id": {"in": ["A100"]}, "amount": {"max": 200}, "reason": {"any": True}}},
         "expires_at": "2026-09-26T18:00:00Z", "delegations_left": 2},
        {"id": "g2", "parent": "g1", "issued_by": "support-agent", "principal": "cust_42", "holder": "billing-agent",
         "actions": {"refunds.create": {"order_id": {"eq": "A100"}, "amount": {"max": 80}, "reason": {"any": True}}},
         "expires_at": "2026-09-26T17:00:00Z", "delegations_left": 0},
    ],
    "policy": {"trusted_issuers": ["auth-service"],
               "tools": {"orders.lookup": {}, "refunds.create": {"approval_over": {"amount": 100}, "max": {"amount": 500}}}},
    "call": {"agent": "billing-agent", "grant": "g2", "on_behalf_of": "cust_42", "tool": "refunds.create",
             "arguments": {"order_id": "A100", "amount": 80, "reason": "damaged"}, "at": "2026-09-26T16:30:00Z"},
}


def mutate(fn):
    s = copy.deepcopy(BASE)
    fn(s)
    return check(s)


def rules(result):
    return {r["rule"] for r in result["reasons"]}


def test_base_acts():
    r = check(BASE)
    assert r["decision"] == ACT and r["reasons"] == [] and r["chain"] == ["g1", "g2"]


def test_root_only_call():
    def f(s):
        s["call"].update(agent="support-agent", grant="g1")
    assert mutate(f)["decision"] == ACT


@pytest.mark.parametrize("fn, rule", [
    (lambda s: s["grants"][1]["actions"]["refunds.create"]["amount"].update(max=300), "hop.wider_constraint"),
    (lambda s: s["grants"][1]["actions"]["refunds.create"].pop("order_id"), "hop.dropped_constraint"),
    (lambda s: s["grants"][1]["actions"].update({"credits.issue": {"amount": {"max": 5}}}), "hop.new_action"),
    (lambda s: s["grants"][1].update(expires_at="2026-09-26T19:00:00Z"), "hop.later_expiry"),
    (lambda s: s["grants"][1].update(delegations_left=2), "hop.more_delegations"),
    (lambda s: s["grants"][1].update(principal="cust_99"), "hop.principal_switch"),
    (lambda s: s["grants"][1].update(issued_by="rogue-agent"), "hop.not_holder"),
    (lambda s: s["grants"][0].update(issued_by="some-agent"), "root.untrusted_issuer"),
    (lambda s: s["grants"][1]["actions"]["refunds.create"]["order_id"].update(eq="B200"), "hop.wider_constraint"),
    (lambda s: s["grants"][1]["actions"]["refunds.create"].update(dest={"any": True}), "hop.any_added"),
    (lambda s: s["grants"][1]["actions"]["refunds.create"].update(amount={"lte": 80}), "grant.unknown_constraint"),
    (lambda s: s["grants"][1].update(parent="g0"), "grant.missing"),
    (lambda s: s["call"]["arguments"].update(amount=81), "call.argument_outside"),
    (lambda s: s["call"]["arguments"].update(order_id="A101"), "call.argument_outside"),
    (lambda s: s["call"]["arguments"].pop("order_id"), "call.argument_missing"),
    (lambda s: s["call"]["arguments"].update(amount="80"), "call.argument_outside"),
    (lambda s: s["call"].update(agent="support-agent"), "call.wrong_agent"),
    (lambda s: s["call"].update(on_behalf_of="cust_99"), "call.principal_mismatch"),
    (lambda s: s["call"].update(at="2026-09-26T17:00:01Z"), "call.expired"),
    (lambda s: s["call"].update(tool="orders.lookup", arguments={"order_id": "A100"}), "call.tool_not_granted"),
    (lambda s: s["call"].update(tool="refunds.delete"), "policy.unknown_tool"),
])
def test_blocks(fn, rule):
    r = mutate(fn)
    assert r["decision"] == BLOCK
    assert rule in rules(r)
    assert all(x["rule"] and x["detail"] for x in r["reasons"])


def test_no_delegations_left():
    def f(s):
        s["grants"][1]["delegations_left"] = 0
        s["grants"].append({"id": "g3", "parent": "g2", "issued_by": "billing-agent", "principal": "cust_42",
                            "holder": "refunds-agent", "actions": {"refunds.create": s["grants"][1]["actions"]["refunds.create"]},
                            "expires_at": "2026-09-26T17:00:00Z", "delegations_left": 0})
        s["call"].update(agent="refunds-agent", grant="g3")
    assert "hop.no_delegations_left" in rules(mutate(f))


def test_cycle():
    def f(s):
        s["grants"][0]["parent"] = "g2"
    assert "grant.cycle" in rules(mutate(f))


def test_policy_cap_blocks_even_inside_grant():
    def f(s):
        s["grants"][0]["actions"]["refunds.create"]["amount"]["max"] = 1000
        s["grants"][1]["actions"]["refunds.create"]["amount"]["max"] = 900
        s["call"]["arguments"]["amount"] = 600
    r = mutate(f)
    assert r["decision"] == BLOCK and "policy.over_cap" in rules(r)


def test_escalates_over_approval_threshold():
    def f(s):
        s["grants"][1]["actions"]["refunds.create"]["amount"]["max"] = 150
        s["call"]["arguments"]["amount"] = 120
    r = mutate(f)
    assert r["decision"] == ESCALATE and rules(r) == {"policy.approval_needed"}


def test_at_threshold_acts():
    def f(s):
        s["grants"][1]["actions"]["refunds.create"]["amount"]["max"] = 100
        s["call"]["arguments"]["amount"] = 100
        s["call"]["at"] = "2026-09-26T17:00:00Z"
    assert mutate(f)["decision"] == ACT


def test_unchecked_argument_escalates():
    def f(s):
        s["call"]["arguments"]["refund_to"] = "acct_attacker"
    r = mutate(f)
    assert r["decision"] == ESCALATE and "call.argument_unchecked" in rules(r)


def test_worst_wins():
    def f(s):
        s["call"]["arguments"].update(refund_to="x", amount=81)
    assert mutate(f)["decision"] == BLOCK


@pytest.mark.parametrize("parent, child, ok", [
    ({"any": True}, {"max": 5}, True),
    ({"max": 10}, {"any": True}, False),
    ({"max": 10}, {"eq": 10}, True),
    ({"max": 10}, {"in": [3, 11]}, False),
    ({"in": ["a", "b"]}, {"in": ["a"]}, True),
    ({"in": ["a"]}, {"in": ["a", "b"]}, False),
    ({"in": [1, 2]}, {"max": 2}, False),
    ({"min": 1, "max": 10}, {"min": 2, "max": 9}, True),
    ({"min": 1, "max": 10}, {"max": 9}, False),
])
def test_narrower(parent, child, ok):
    from agent_handoff_check.checker import _narrower
    assert _narrower(parent, child) is ok


def test_audit_log_detects_tampering(tmp_path):
    log = tmp_path / "audit.jsonl"
    a = AuditLog(log)
    for amount in (10, 81, 50):
        s = copy.deepcopy(BASE)
        s["call"]["arguments"]["amount"] = amount
        a.write(s, check(s))
    assert verify(log)[0]
    lines = log.read_text().splitlines()
    assert "amount" in json.loads(lines[0])["argument_names"] and "arguments" not in json.loads(lines[0])

    edited = json.loads(lines[1]); edited["decision"] = "ACT"
    for variant in ([lines[0], json.dumps(edited), lines[2]], [lines[0], lines[2]], [lines[1], lines[0], lines[2]]):
        log.write_text("\n".join(variant) + "\n")
        assert not verify(log)[0]


# --- v0.2: every number bounded on both sides; booleans never match numbers (red-team r01–r03) ---

def test_negative_amount_blocks():
    def f(s):
        s["call"]["arguments"]["amount"] = -500
    r = mutate(f)
    assert r["decision"] == BLOCK and {"call.argument_outside", "policy.under_floor"} <= rules(r)


def test_explicit_min_allows_negative():
    def f(s):
        for g in s["grants"]:
            g["actions"]["refunds.create"]["amount"] = {"min": -50, "max": 80}
        s["policy"]["tools"]["refunds.create"]["min"] = {"amount": -50}
        s["call"]["arguments"]["amount"] = -20
    assert mutate(f)["decision"] == ACT


def test_child_cannot_open_negative_range():
    def f(s):
        s["grants"][1]["actions"]["refunds.create"]["amount"] = {"min": -10, "max": 80}
    assert "hop.wider_constraint" in rules(mutate(f))


def test_unbounded_above_escalates():
    def f(s):
        for g in s["grants"]:
            g["actions"]["refunds.create"]["amount"] = {"min": 0}
        s["policy"]["tools"]["refunds.create"] = {}
        s["call"]["arguments"]["amount"] = 999_999_999
    r = mutate(f)
    assert r["decision"] == ESCALATE and "call.unbounded_number" in rules(r)


def test_any_number_bounded_by_policy_acts():
    def f(s):
        for g in s["grants"]:
            g["actions"]["refunds.create"]["amount"] = {"any": True}
        s["grants"][1]["actions"]["refunds.create"]["amount"] = {"any": True}
        s["call"]["arguments"]["amount"] = 40
    assert mutate(f)["decision"] == ACT


def test_pinned_number_is_bounded():
    def f(s):
        s["grants"][0]["actions"]["refunds.create"]["amount"] = {"in": [40, 80]}
        s["grants"][1]["actions"]["refunds.create"]["amount"] = {"eq": 80}
        s["policy"]["tools"]["refunds.create"] = {}
    assert mutate(f)["decision"] == ACT


def test_boolean_never_matches_number():
    def f(s):
        for g in s["grants"]:
            g["actions"]["refunds.create"]["order_id"] = {"in": [1]}
        s["grants"][1]["actions"]["refunds.create"]["order_id"] = {"eq": 1}
        s["call"]["arguments"]["order_id"] = True
    r = mutate(f)
    assert r["decision"] == BLOCK and "call.argument_outside" in rules(r)
