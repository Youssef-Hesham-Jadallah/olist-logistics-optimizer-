"""Batch optimisation: capacity-constrained assignment (scipy/HiGHS MILP), consolidation, baselines."""
from __future__ import annotations
import random
from collections import defaultdict
from typing import Optional
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import lil_matrix
from .carriers import CarrierAdapter, QuoteInput
from .quoting import Weights, collect_quotes, late_probability, default_penalty


def build_options_with_diag(adapters: dict[str, CarrierAdapter], orders: list[dict], deadline_mode: str = "p90", relax_deadline: bool = False, late_penalty: Optional[float] = None):
    """orders: [{id, q: QuoteInput, deadline_days}] -> (options, diag).
    options[i] = feasible carrier options for order i; diag[i] explains WHY an order has none (or marks it `relaxed`)."""
    opts, diag = [], []
    pen = default_penalty() if late_penalty is None else float(late_penalty)
    for o in orders:
        quotes, excluded = collect_quotes(adapters, o["q"])
        dd = o.get("deadline_days"); row, info = [], None
        for x in quotes:
            lim = x.eta_p90_days if deadline_mode == "p90" else x.eta_p50_days
            if dd is not None and lim > dd:
                continue
            risk = late_probability(x.eta_p50_days, x.eta_p90_days, dd) if dd is not None else x.late_risk
            row.append({"code": x.carrier_code, "carrier": x.carrier, "price": x.price, "eta_p50": x.eta_p50_days, "eta_p90": x.eta_p90_days, "risk": risk, "relaxed": False, "exp_cost": round(x.price + risk * pen, 2)})
        if not row:
            if not quotes:
                kinds = {e["reason"].split(":")[0] for e in excluded}
                if kinds <= {"not covered"} and excluded:
                    info = {"reason_code": "NO_COVERAGE", "reason": f"no carrier covers {o['q'].origin.state or o['q'].origin.label} -> {o['q'].destination.state or o['q'].destination.label}"}
                elif excluded and "unavailable" in kinds:
                    info = {"reason_code": "CARRIERS_UNAVAILABLE", "reason": "every carrier that covers this route is currently unavailable"}
                else:
                    info = {"reason_code": "NO_QUOTE", "reason": "no carrier returned a quote"}
            else:
                fast = min(quotes, key=lambda x: x.eta_p90_days if deadline_mode == "p90" else x.eta_p50_days)
                fl = fast.eta_p90_days if deadline_mode == "p90" else fast.eta_p50_days
                info = {"reason_code": "DEADLINE_INFEASIBLE", "deadline_days": round(dd, 1), "fastest_eta_days": round(fl, 1), "fastest_carrier": fast.carrier_code,
                        "reason": f"deadline {dd:.1f}d is shorter than the fastest carrier's {deadline_mode.upper()} ETA {fl:.1f}d ({fast.carrier})"}
                if relax_deadline:
                    for x in quotes:
                        row.append({"code": x.carrier_code, "carrier": x.carrier, "price": x.price, "eta_p50": x.eta_p50_days, "eta_p90": x.eta_p90_days,
                                    "risk": (rk := late_probability(x.eta_p50_days, x.eta_p90_days, dd)), "relaxed": True, "exp_cost": round(x.price + rk * pen, 2)})
                    info = {**info, "relaxed": True, "reason": info["reason"] + " (assigned anyway: relax_deadline)"}
        opts.append(row); diag.append(info)
    return opts, diag


def build_options(adapters: dict[str, CarrierAdapter], orders: list[dict], deadline_mode: str = "p90", relax_deadline: bool = False, late_penalty: Optional[float] = None) -> list[list[dict]]:
    return build_options_with_diag(adapters, orders, deadline_mode, relax_deadline, late_penalty)[0]


def _scores(options: list[list[dict]], w: Weights):
    flat = [o for row in options for o in row]
    if not flat:
        return
    keys = ("price", "eta_p90", "risk", "exp_cost")
    lo = {k: min(o[k] for o in flat) for k in keys}; hi = {k: max(o[k] for o in flat) for k in keys}
    n = lambda o, k: 0.0 if hi[k] - lo[k] < 1e-9 else (o[k] - lo[k]) / (hi[k] - lo[k])
    for o in flat:
        o["score"] = n(o, "exp_cost") if w.mode == "expected_cost" else w.cost * n(o, "price") + w.speed * n(o, "eta_p90") + w.risk * n(o, "risk")


def _summ(orders, options, pick: list[Optional[int]]):
    cost = eta = risk = ec = 0.0; k = 0; per = defaultdict(int)
    for oo, i in zip(options, pick):
        if i is None: continue
        o = oo[i]; cost += o["price"]; eta += o["eta_p50"]; risk += o["risk"]; ec += o["exp_cost"]; k += 1; per[o["code"]] += 1
    return {"assigned": k, "total_cost": round(cost, 2), "total_expected_cost": round(ec, 2), "avg_eta_p50_days": round(eta / k, 2) if k else None, "expected_late_orders": round(risk, 1), "carrier_load": dict(per)}


def _compare(options, opt_pick, base_pick) -> dict:
    """Like-for-like comparison: only orders that BOTH policies assigned. Negative cost_change = optimized is cheaper."""
    common = [i for i, (a, b) in enumerate(zip(opt_pick, base_pick)) if a is not None and b is not None]
    if not common:
        return {"common_orders": 0}
    tot = lambda pk, key: sum(options[i][pk[i]][key] for i in common)
    oc, bc = tot(opt_pick, "price"), tot(base_pick, "price")
    oe, be = tot(opt_pick, "eta_p50") / len(common), tot(base_pick, "eta_p50") / len(common)
    ol, bl = tot(opt_pick, "risk"), tot(base_pick, "risk")
    d = {"common_orders": len(common), "optimized_cost": round(oc, 2), "baseline_cost": round(bc, 2), "cost_change_pct": round((oc / bc - 1) * 100, 1) if bc else None,
         "avg_eta_change_days": round(oe - be, 2), "expected_late_change": round(ol - bl, 1),
         "expected_cost_change_pct": round((tot(opt_pick, "exp_cost") / max(tot(base_pick, "exp_cost"), 1e-9) - 1) * 100, 1)}
    bits = [f"cost {d['cost_change_pct']:+.1f}%" if d["cost_change_pct"] is not None else "cost n/a", f"ETA {d['avg_eta_change_days']:+.1f}d", f"expected late orders {d['expected_late_change']:+.1f}", f"expected total cost (price + late penalty) {d['expected_cost_change_pct']:+.1f}%"]
    d["verdict"] = f"Optimized vs this policy on the same {len(common)} orders: " + ", ".join(bits) + "  (negative = optimized is better)"
    return d


def _unassigned_detail(orders, options, pick, capacity, diag) -> list[dict]:
    """One explicit reason per order left out: no coverage / deadline infeasible / carriers unavailable / capacity exhausted."""
    out = []
    for i, p in enumerate(pick):
        if p is not None: continue
        if not options[i]:
            info = (diag[i] if diag and diag[i] else {"reason_code": "NO_OPTIONS", "reason": "no feasible carrier option"})
        else:
            codes = sorted({o["code"] for o in options[i]})
            info = {"reason_code": "CAPACITY", "reason": "every feasible carrier is out of capacity: " + ", ".join(f"{c} (left {capacity.get(c, 0)})" for c in codes)}
        out.append({"id": orders[i]["id"], **info})
    return out


def solve_assignment(orders: list[dict], options: list[list[dict]], capacity: dict[str, int], w: Weights, seed: int = 7, diag: Optional[list] = None) -> dict:
    """min sum score_ij x_ij  s.t. each order <=1 carrier, carrier load <= capacity. Compared with baselines."""
    for row in options:
        for o in row:
            o.setdefault("exp_cost", o["price"]); o.setdefault("relaxed", False)
    _scores(options, w)
    idx = [(i, j) for i, row in enumerate(options) for j in range(len(row))]
    n, m = len(options), len(idx)
    if m == 0:
        return {"assignments": [], "unassigned": [o["id"] for o in orders], "unassigned_detail": _unassigned_detail(orders, options, [None] * n, capacity, diag),
                "optimized": _summ(orders, options, [None] * n), "baselines": {}}
    codes = sorted({o["code"] for row in options for o in row})
    c = np.array([options[i][j]["score"] for i, j in idx]) - 2.0   # scores are in [0,1]; shift by 2 so assigning ALWAYS beats leaving an order out (-1.0 left score==1.0 options at 0 = indifferent)
    A = lil_matrix((n + len(codes), m))
    for k, (i, j) in enumerate(idx):
        A[i, k] = 1; A[n + codes.index(options[i][j]["code"]), k] = 1
    ub = np.array([1.0] * n + [float(capacity.get(cd, 10 ** 9)) for cd in codes])
    res = milp(c, constraints=LinearConstraint(A.tocsr(), -np.inf, ub), integrality=np.ones(m), bounds=Bounds(0, 1))
    pick: list[Optional[int]] = [None] * n
    if res.x is not None:
        for k, (i, j) in enumerate(idx):
            if res.x[k] > 0.5: pick[i] = j
    assignments = [{"order_id": orders[i]["id"], **{k: options[i][j][k] for k in ("code", "carrier", "price", "eta_p50", "eta_p90", "risk", "relaxed")}} for i, j in enumerate(pick) if j is not None]

    def greedy(key):
        load = defaultdict(int); out: list[Optional[int]] = []
        for row in options:
            out.append(None)
            for j in sorted(range(len(row)), key=lambda j: row[j][key]):
                if load[row[j]["code"]] < capacity.get(row[j]["code"], 10 ** 9):
                    load[row[j]["code"]] += 1; out[-1] = j; break
        return out
    rng = random.Random(seed)
    load = defaultdict(int); rand: list[Optional[int]] = []
    for row in options:
        js = [j for j in range(len(row)) if load[row[j]["code"]] < capacity.get(row[j]["code"], 10 ** 9)]
        j = rng.choice(js) if js else None
        rand.append(j)
        if j is not None: load[row[j]["code"]] += 1
    base = {"random_carrier": _summ(orders, options, rand), "cheapest_greedy": _summ(orders, options, greedy("price")), "fastest_greedy": _summ(orders, options, greedy("eta_p90"))}
    opt = _summ(orders, options, pick)
    base_picks = {"random_carrier": rand, "cheapest_greedy": greedy("price"), "fastest_greedy": greedy("eta_p90")}
    for name, b in base.items():
        b["vs_optimized"] = _compare(options, pick, base_picks[name])
    return {"assignments": assignments, "unassigned": [orders[i]["id"] for i, j in enumerate(pick) if j is None],
            "unassigned_detail": _unassigned_detail(orders, options, pick, capacity, diag), "optimized": opt, "baselines": base,
            "solver": {"name": "scipy.optimize.milp (HiGHS)", "status": int(res.status), "success": bool(res.success)}}


def consolidate(shipments: list[dict], adapters: dict[str, CarrierAdapter], last_mile_fee: float = 6.0) -> dict:
    """Group parcels that leave the same origin zip towards the same destination state and price them as one line-haul.
    shipments: [{id, origin(Place), destination(Place), weight_kg}] -> groups with estimated saving (assumption-based)."""
    groups = defaultdict(list)
    for s in shipments:
        groups[(s["origin"].zip or s["origin"].label, s["destination"].state)].append(s)
    out, total_save = [], 0.0
    for (oz, ds), g in groups.items():
        if len(g) < 2: continue
        solo = 0.0; ok = True
        for s in g:
            qs, _ = collect_quotes(adapters, QuoteInput(s["origin"], s["destination"], s["weight_kg"]))
            if not qs: ok = False; break
            solo += min(x.price for x in qs)
        if not ok: continue
        head = g[0]; wsum = sum(s["weight_kg"] for s in g)
        qs, _ = collect_quotes(adapters, QuoteInput(head["origin"], head["destination"], wsum))
        if not qs: continue
        cons = min(x.price for x in qs) + last_mile_fee * (len(g) - 1)
        save = solo - cons
        if save > 0:
            total_save += save
            out.append({"origin": oz, "destination_state": ds, "shipment_ids": [s["id"] for s in g], "parcels": len(g), "total_kg": round(wsum, 2),
                        "individual_cost": round(solo, 2), "consolidated_cost": round(cons, 2), "saving": round(save, 2), "saving_pct": round(save / solo * 100, 1)})
    out.sort(key=lambda x: -x["saving"])
    return {"groups": out, "total_saving": round(total_save, 2), "assumption": f"one line-haul for the combined weight + {last_mile_fee} BRL last-mile fee per extra parcel (estimate, not a carrier tariff)"}
