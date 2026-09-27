// Browser port of src/agent_handoff_check/checker.py. tests/test_parity.py checks that both give
// the same decision, rules and hops on every eval case, so change them together.
(function (root) {
  "use strict";
  const ACT = "ACT", ESCALATE = "ESCALATE", BLOCK = "BLOCK";
  const RANK = { ACT: 0, ESCALATE: 1, BLOCK: 2 };
  const KNOWN_OPS = new Set(["eq", "in", "max", "min", "any"]);
  const GRANT_FIELDS = ["id", "issued_by", "principal", "holder", "actions", "expires_at", "delegations_left"];

  const isObj = v => v !== null && typeof v === "object" && !Array.isArray(v);
  const isNum = v => typeof v === "number";
  const isBool = v => typeof v === "boolean";
  const has = (o, k) => isObj(o) && Object.prototype.hasOwnProperty.call(o, k);
  const get = (o, k) => (has(o, k) ? o[k] : undefined);
  const truthy = v => !(v === undefined || v === null || v === false || v === 0 || v === "" ||
    (Array.isArray(v) && v.length === 0) || (isObj(v) && Object.keys(v).length === 0));
  const show = v => JSON.stringify(v);

  // Python's ==, which treats True as 1.
  function pyEq(a, b) {
    if ((isNum(a) || isBool(a)) && (isNum(b) || isBool(b))) return Number(a) === Number(b);
    if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => pyEq(x, b[i]));
    if (isObj(a) && isObj(b)) {
      const ka = Object.keys(a), kb = Object.keys(b);
      return ka.length === kb.length && ka.every(k => has(b, k) && pyEq(a[k], b[k]));
    }
    return a === b;
  }
  // Equality that never lets a boolean match a number.
  const same = (a, b) => isBool(a) === isBool(b) && pyEq(a, b);
  const member = (v, values) => values.some(x => same(v, x));

  const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;
  function time(v) {
    if (typeof v !== "string" || !ISO.test(v)) return null;
    const t = Date.parse(v);
    return Number.isNaN(t) ? null : t;
  }

  const reason = (decision, rule, hop, detail) => ({ decision, rule, hop, detail });

  // --- constraints ---
  function constraintProblem(c) {
    if (!isObj(c) || Object.keys(c).length === 0) return "constraint must be a non-empty object";
    const unknown = Object.keys(c).filter(k => !KNOWN_OPS.has(k)).sort();
    if (unknown.length) return `unknown constraint ${show(unknown)}`;
    if (has(c, "any") && (c.any !== true || Object.keys(c).length > 1)) return '"any" must be true and stand alone';
    if (has(c, "in") && !Array.isArray(c.in)) return '"in" must be a list';
    for (const op of ["max", "min"]) if (has(c, op) && !isNum(c[op])) return `"${op}" must be a number`;
    return null;
  }
  // (low, high). A max with no min means a floor of 0.
  const bounds = c => [has(c, "min") ? c.min : (has(c, "max") ? 0 : null), has(c, "max") ? c.max : null];
  function finite(c) {
    if (has(c, "eq") && has(c, "in")) return member(c.eq, c.in) ? [c.eq] : [];
    if (has(c, "eq")) return [c.eq];
    if (has(c, "in")) return c.in.slice();
    return null;
  }
  function satisfies(c, v) {
    if (get(c, "any") === true) return true;
    if (has(c, "eq") && !same(v, c.eq)) return false;
    if (has(c, "in") && !member(v, c.in)) return false;
    const [low, high] = bounds(c);
    if (low !== null || high !== null) {
      if (!isNum(v)) return false;
      if ((low !== null && v < low) || (high !== null && v > high)) return false;
    }
    return true;
  }
  function narrower(parent, child) {
    if (get(parent, "any") === true) return true;
    if (get(child, "any") === true) return false;
    const values = finite(child);
    if (values !== null) return values.every(v => satisfies(parent, v));
    if (finite(parent) !== null) return false;
    const [plow, phigh] = bounds(parent), [clow, chigh] = bounds(child);
    if (phigh !== null && !(chigh !== null && chigh <= phigh)) return false;
    if (plow !== null && !(clow !== null && clow >= plow)) return false;
    return true;
  }

  // --- chain ---
  const key = v => (isBool(v) || isNum(v)) ? "n:" + Number(v) : typeof v === "string" ? "s:" + v : "j:" + show(v);
  function chainOf(grants, start, reasons) {
    const byId = new Map();
    for (const g of grants) {
      const gid = isObj(g) ? get(g, "id") : undefined;
      if (gid === undefined || gid === null || byId.has(key(gid))) {
        reasons.push(reason(BLOCK, "grant.bad_id", gid === undefined ? null : gid, "every grant needs a unique id"));
        return null;
      }
      byId.set(key(gid), g);
    }
    const chain = [], seen = new Set();
    let gid = start;
    while (gid !== undefined && gid !== null) {
      if (seen.has(key(gid))) { reasons.push(reason(BLOCK, "grant.cycle", gid, "the chain loops back on itself")); return null; }
      if (!byId.has(key(gid))) { reasons.push(reason(BLOCK, "grant.missing", gid, `grant ${show(gid)} isn't in the scenario`)); return null; }
      seen.add(key(gid));
      const g = byId.get(key(gid));
      chain.push(g);
      gid = get(g, "parent");
    }
    return chain.reverse();
  }

  function checkGrantShape(g, reasons) {
    const gid = g.id;
    let ok = true;
    for (const f of GRANT_FIELDS) if (!has(g, f)) { reasons.push(reason(BLOCK, "grant.missing_field", gid, `missing "${f}"`)); ok = false; }
    if (!ok) return false;
    if (time(g.expires_at) === null) { reasons.push(reason(BLOCK, "grant.bad_expiry", gid, "expires_at must be an ISO 8601 time with a timezone")); ok = false; }
    const dl = g.delegations_left;
    if (!(Number.isInteger(dl) && dl >= 0)) { reasons.push(reason(BLOCK, "grant.bad_delegations", gid, "delegations_left must be a whole number ≥ 0")); ok = false; }
    if (!isObj(g.actions)) { reasons.push(reason(BLOCK, "grant.bad_actions", gid, "actions must be an object")); return false; }
    for (const [tool, args] of Object.entries(g.actions)) {
      if (!isObj(args)) { reasons.push(reason(BLOCK, "grant.bad_actions", gid, `${tool}: constraints must be an object`)); ok = false; continue; }
      for (const [arg, c] of Object.entries(args)) {
        const problem = constraintProblem(c);
        if (problem) { reasons.push(reason(BLOCK, "grant.unknown_constraint", gid, `${tool}.${arg}: ${problem}`)); ok = false; }
      }
    }
    return ok;
  }

  function checkHop(parent, child, reasons) {
    const cid = child.id;
    if (!pyEq(child.issued_by, parent.holder))
      reasons.push(reason(BLOCK, "hop.not_holder", cid, `${show(child.issued_by)} handed on grant ${show(parent.id)}, which belongs to ${show(parent.holder)}`));
    if (!pyEq(child.principal, parent.principal))
      reasons.push(reason(BLOCK, "hop.principal_switch", cid, `principal changed from ${show(parent.principal)} to ${show(child.principal)}`));
    if (parent.delegations_left < 1)
      reasons.push(reason(BLOCK, "hop.no_delegations_left", cid, `${show(parent.id)} had no handoffs left`));
    else if (child.delegations_left > parent.delegations_left - 1)
      reasons.push(reason(BLOCK, "hop.more_delegations", cid, `delegations_left ${child.delegations_left} > parent's ${parent.delegations_left} − 1`));
    if (time(child.expires_at) > time(parent.expires_at))
      reasons.push(reason(BLOCK, "hop.later_expiry", cid, "expires after its parent"));
    for (const [tool, cargs] of Object.entries(child.actions)) {
      const pargs = get(parent.actions, tool);
      if (pargs === undefined) { reasons.push(reason(BLOCK, "hop.new_action", cid, `adds "${tool}", which the parent doesn't have`)); continue; }
      for (const [arg, pc] of Object.entries(pargs)) {
        if (!has(cargs, arg)) reasons.push(reason(BLOCK, "hop.dropped_constraint", cid, `${tool}.${arg}: parent's constraint dropped`));
        else if (!narrower(pc, cargs[arg])) reasons.push(reason(BLOCK, "hop.wider_constraint", cid, `${tool}.${arg}: ${show(cargs[arg])} is wider than parent's ${show(pc)}`));
      }
      for (const arg of Object.keys(cargs)) {
        if (!has(pargs, arg) && get(cargs[arg], "any") === true)
          reasons.push(reason(BLOCK, "hop.any_added", cid, `${tool}.${arg}: "any" on an argument the parent didn't mention`));
      }
    }
  }

  function checkCall(chain, policy, call, reasons) {
    const last = chain[chain.length - 1];
    if (!pyEq(get(call, "agent"), last.holder))
      reasons.push(reason(BLOCK, "call.wrong_agent", last.id, `${show(get(call, "agent"))} used a grant held by ${show(last.holder)}`));
    if (!pyEq(get(call, "on_behalf_of"), last.principal))
      reasons.push(reason(BLOCK, "call.principal_mismatch", last.id, `call is for ${show(get(call, "on_behalf_of"))}, the grant is for ${show(last.principal)}`));
    const at = time(get(call, "at"));
    if (at === null) reasons.push(reason(BLOCK, "call.bad_time", "call", "call.at must be an ISO 8601 time with a timezone"));
    else for (const g of chain) if (at > time(g.expires_at)) reasons.push(reason(BLOCK, "call.expired", g.id, `grant expired at ${g.expires_at}`));

    const tool = get(call, "tool");
    const args = truthy(get(call, "arguments")) ? call.arguments : {};
    const tools = truthy(get(policy, "tools")) ? policy.tools : {};
    const toolKnown = typeof tool === "string" && has(tools, tool);
    if (!toolKnown) reasons.push(reason(BLOCK, "policy.unknown_tool", "policy", `${show(tool)} isn't in the policy`));
    const rules = toolKnown && truthy(tools[tool]) ? tools[tool] : {};
    if (toolKnown) {
      const caps = truthy(get(rules, "max")) ? rules.max : {}, floors = truthy(get(rules, "min")) ? rules.min : {};
      for (const arg of new Set([...Object.keys(caps), ...Object.keys(floors)])) {
        if (!has(args, arg)) continue;
        const c = {};
        if (has(floors, arg) && floors[arg] !== null) c.min = floors[arg];
        if (has(caps, arg) && caps[arg] !== null) c.max = caps[arg];
        const [low, high] = bounds(c), v = args[arg];
        if (!isNum(v)) reasons.push(reason(BLOCK, "policy.over_cap", "policy", `${arg}=${show(v)} isn't a number, so the policy limits can't be checked`));
        else if (high !== null && v > high) reasons.push(reason(BLOCK, "policy.over_cap", "policy", `${arg}=${v} is over the policy cap of ${high}`));
        else if (low !== null && v < low) reasons.push(reason(BLOCK, "policy.under_floor", "policy", `${arg}=${v} is under the policy floor of ${low}`));
      }
      for (const [arg, limit] of Object.entries(truthy(get(rules, "approval_over")) ? rules.approval_over : {})) {
        if (has(args, arg) && isNum(args[arg]) && args[arg] > limit)
          reasons.push(reason(ESCALATE, "policy.approval_needed", "policy", `${arg}=${args[arg]} is over ${limit}, which needs approval`));
      }
    }

    for (const g of chain) {
      const constraints = typeof tool === "string" ? get(g.actions, tool) : undefined;
      if (constraints === undefined) { reasons.push(reason(BLOCK, "call.tool_not_granted", g.id, `${show(tool)} isn't in this grant`)); continue; }
      for (const [arg, c] of Object.entries(constraints)) {
        if (!has(args, arg)) { if (get(c, "any") !== true) reasons.push(reason(BLOCK, "call.argument_missing", g.id, `"${arg}" is constrained but missing from the call`)); }
        else if (!satisfies(c, args[arg])) reasons.push(reason(BLOCK, "call.argument_outside", g.id, `${arg}=${show(args[arg])} is outside ${show(c)}`));
      }
      for (const arg of Object.keys(args)) if (!has(constraints, arg))
        reasons.push(reason(ESCALATE, "call.argument_unchecked", g.id, `"${arg}" isn't mentioned by this grant`));
    }

    // Every number must be bounded on both sides by something the business wrote.
    for (const [arg, v] of Object.entries(args)) {
      if (!isNum(v)) continue;
      const sources = chain.map(g => {
        const t = typeof tool === "string" ? get(g.actions, tool) : undefined;
        const c = t === undefined ? undefined : get(t, arg);
        return truthy(c) ? c : {};
      });
      const p = {};
      for (const k of ["min", "max"]) if (truthy(get(rules, k)) && has(rules[k], arg)) p[k] = rules[k][arg];
      sources.push(p);
      const bs = sources.filter(c => get(c, "any") !== true).map(c => finite(c) !== null ? [0, 0] : bounds(c));
      const missing = [["lower", 0], ["upper", 1]].filter(([, i]) => bs.every(b => b[i] === null)).map(([s]) => s);
      if (missing.length)
        reasons.push(reason(ESCALATE, "call.unbounded_number", "call", `${arg}=${v}: no ${missing.join(" or ")} limit anywhere in the chain or the policy`));
    }
  }

  function result(reasons, chain) {
    const decision = reasons.reduce((d, r) => (RANK[r.decision] > RANK[d] ? r.decision : d), ACT);
    return { decision, reasons, chain };
  }

  function check(scenario) {
    const reasons = [];
    const grants = truthy(get(scenario, "grants")) ? scenario.grants : [];
    const policy = truthy(get(scenario, "policy")) ? scenario.policy : {};
    const call = truthy(get(scenario, "call")) ? scenario.call : {};
    const chain = truthy(get(call, "grant")) ? chainOf(grants, call.grant, reasons) : null;
    if (chain === null && reasons.length === 0) reasons.push(reason(BLOCK, "call.no_grant", "call", "the call names no grant"));
    if (chain === null) return result(reasons, []);
    const ids = chain.map(g => g.id);
    if (!chain.every(g => checkGrantShape(g, reasons))) return result(reasons, ids);  // stops at the first bad grant, like Python's all()
    const rootGrant = chain[0];
    const trusted = truthy(get(policy, "trusted_issuers")) ? policy.trusted_issuers : [];
    if (!trusted.some(x => pyEq(x, rootGrant.issued_by)))
      reasons.push(reason(BLOCK, "root.untrusted_issuer", rootGrant.id, `${show(rootGrant.issued_by)} isn't a trusted issuer`));
    for (let i = 1; i < chain.length; i++) checkHop(chain[i - 1], chain[i], reasons);
    checkCall(chain, policy, call, reasons);
    return result(reasons, ids);
  }

  const api = { check, ACT, ESCALATE, BLOCK };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AgentHandoffCheck = api;
})(typeof self !== "undefined" ? self : this);
