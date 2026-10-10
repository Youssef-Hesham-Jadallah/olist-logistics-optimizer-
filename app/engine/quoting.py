"""Per-order optimisation: quote every carrier on the real route, filter, score, rank, Pareto frontier."""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Iterable, Optional
from .carriers import (CarrierAdapter, CarrierUnavailable, NotCovered, Quote, QuoteInput, HEALTH)
from .geo import Place
from . import reference

PRESETS = {"cost": (1.0, 0.0, 0.0), "speed": (0.0, 1.0, 0.0), "reliability": (0.0, 0.0, 1.0), "balanced": (0.40, 0.35, 0.25)}
Z90 = 1.2816


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def late_probability(p50: float, p90: float, deadline_days: float) -> float:
    """P(total delivery time > deadline) under a lognormal fitted to (P50, P90)."""
    if deadline_days <= 0:
        return 1.0
    mu = math.log(max(p50, 0.05)); sigma = max(math.log(max(p90, p50 * 1.05) / max(p50, 0.05)) / Z90, 0.05)
    return round(1 - _phi((math.log(deadline_days) - mu) / sigma), 4)


def default_penalty() -> float:
    return float(reference.models().get("default_late_penalty_brl", 40.0))


@dataclass
class Weights:
    cost: float
    speed: float
    risk: float
    mode: str = "weights"      # "weights" = weighted normalised score ; "expected_cost" = minimise price + P(late) x penalty

    @classmethod
    def from_objective(cls, objective: str, custom: Optional[tuple] = None) -> "Weights":
        if objective == "expected_cost":
            return cls(0.0, 0.0, 0.0, "expected_cost")
        w = custom if (objective == "custom" and custom) else PRESETS[objective if objective in PRESETS else "balanced"]
        s = sum(w) or 1.0
        return cls(w[0] / s, w[1] / s, w[2] / s)


def _norm(vals: list[float]) -> list[float]:
    lo, hi = min(vals), max(vals)
    return [0.0 if hi - lo < 1e-9 else (v - lo) / (hi - lo) for v in vals]


def pareto_front(points: list[tuple[float, float]]) -> list[bool]:
    """True for points not dominated in (price, time) – lower is better for both."""
    flags = []
    for i, (c, t) in enumerate(points):
        dom = any((c2 <= c and t2 <= t) and (c2 < c or t2 < t) for j, (c2, t2) in enumerate(points) if j != i)
        flags.append(not dom)
    return flags


def collect_quotes(adapters: dict[str, CarrierAdapter], q: QuoteInput, exclude: Iterable[str] = ()) -> tuple[list[Quote], list[dict]]:
    """Call every adapter; failures/uncovered routes are reported, never raised (fallback behaviour)."""
    quotes, excluded = [], []
    for code, ad in adapters.items():
        if code in set(exclude):
            excluded.append({"carrier_code": code, "carrier": ad.profile.name, "reason": "excluded by request"}); continue
        try:
            quotes.append(ad.quote(q)); HEALTH.record_success(code)
        except NotCovered as e:
            excluded.append({"carrier_code": code, "carrier": ad.profile.name, "reason": f"not covered: {e}"})
        except CarrierUnavailable as e:
            HEALTH.record_failure(code)
            excluded.append({"carrier_code": code, "carrier": ad.profile.name, "reason": f"unavailable: {e}"})
        except Exception as e:  # adapter bug / API error -> degrade gracefully
            HEALTH.record_failure(code)
            excluded.append({"carrier_code": code, "carrier": ad.profile.name, "reason": f"error: {type(e).__name__}"})
    return quotes, excluded


def optimize(adapters: dict[str, CarrierAdapter], q: QuoteInput, *, objective: str = "balanced", custom_weights: Optional[tuple] = None,
             deadline_days: Optional[float] = None, deadline_mode: str = "p90", exclude: Iterable[str] = (), late_penalty: Optional[float] = None) -> dict:
    quotes, excluded = collect_quotes(adapters, q, exclude)
    pen = default_penalty() if late_penalty is None else float(late_penalty)
    w = Weights.from_objective(objective, custom_weights)
    rows = []
    for x in quotes:
        d = x.to_dict()
        if deadline_days is not None:
            lim = x.eta_p90_days if deadline_mode == "p90" else x.eta_p50_days
            d["late_risk"] = late_probability(x.eta_p50_days, x.eta_p90_days, deadline_days)
            d["eligible"] = lim <= deadline_days
            d["deadline_slack_days"] = round(deadline_days - lim, 1)
        else:
            d["eligible"] = True; d["deadline_slack_days"] = None
        d["expected_cost"] = round(d["price"] + d["late_risk"] * pen, 2)
        rows.append(d)
    pool = [r for r in rows if r["eligible"]] or []
    if pool:
        nc, nt, nr = _norm([r["price"] for r in pool]), _norm([r["eta_p90_days"] if w.speed else r["eta_p50_days"] for r in pool]), _norm([r["late_risk"] for r in pool])
        ne = _norm([r["expected_cost"] for r in pool])
        for r, a, b, c, e in zip(pool, nc, nt, nr, ne):
            r["score"] = round(e if w.mode == "expected_cost" else w.cost * a + w.speed * b + w.risk * c, 4)
        flags = pareto_front([(r["price"], r["eta_p90_days"]) for r in pool])
        for r, f in zip(pool, flags):
            r["pareto"] = f
    for r in rows:
        r.setdefault("score", None); r.setdefault("pareto", False)
    rows.sort(key=lambda r: (not r["eligible"], r["score"] if r["score"] is not None else 9, r["price"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i if r["eligible"] else None
    _explain(rows, w, deadline_days, deadline_mode)
    best = next((r for r in rows if r["eligible"]), None)
    result = {"origin": q.origin.as_dict(), "destination": q.destination.as_dict(), "weight_kg": q.weight_kg, "currency": "BRL",
              "objective": objective, "weights": {"cost": round(w.cost, 3), "speed": round(w.speed, 3), "risk": round(w.risk, 3)}, "late_penalty": pen,
              "deadline_days": deadline_days, "deadline_mode": deadline_mode if deadline_days is not None else None,
              "quotes": rows, "excluded": excluded, "recommendation": best,
              "pareto_frontier": sorted([{"carrier_code": r["carrier_code"], "price": r["price"], "eta_p90_days": r["eta_p90_days"]} for r in rows if r.get("pareto")], key=lambda r: r["price"]),
              "notice": "Simulated carriers calibrated on Olist 2016-2018 data (not live carrier prices)."}
    if best is None:
        closest = min(rows, key=lambda r: r["eta_p90_days"]) if rows else None
        result["infeasible"] = {"reason": "no carrier meets the deadline" if rows else "no carrier covers this route",
                                "fastest_available": {"carrier_code": closest["carrier_code"], "eta_p90_days": closest["eta_p90_days"]} if closest else None}
    return result


def _explain(rows, w: Weights, deadline, mode):
    ok = [r for r in rows if r["eligible"]]
    if not ok:
        for r in rows:
            r["reason"] = f"P90 {r['eta_p90_days']}d exceeds deadline {deadline}d" if deadline is not None else "not eligible"
        return
    cheapest = min(ok, key=lambda r: r["price"]); fastest = min(ok, key=lambda r: r["eta_p90_days"]); safest = min(ok, key=lambda r: r["late_risk"])
    for r in rows:
        if not r["eligible"]:
            r["reason"] = f"{mode.upper()} ETA {r['eta_p90_days'] if mode == 'p90' else r['eta_p50_days']}d misses the {deadline}d deadline"; continue
        bits = []
        if r is cheapest: bits.append("lowest price")
        if r is fastest: bits.append("fastest (P90)")
        if r is safest: bits.append("lowest late-risk")
        if r is min(ok, key=lambda x: x["expected_cost"]): bits.append("lowest expected cost")
        if not bits:
            extra = round((r["price"] / cheapest["price"] - 1) * 100)
            bits.append(f"+{extra}% vs cheapest, {round(r['eta_p90_days'] - fastest['eta_p90_days'], 1)}d slower than fastest")
        if r["rank"] == 1:
            bits.insert(0, "best weighted score")
        if r["deadline_slack_days"] is not None:
            bits.append(f"{r['deadline_slack_days']}d slack vs deadline")
        r["reason"] = "; ".join(bits)
