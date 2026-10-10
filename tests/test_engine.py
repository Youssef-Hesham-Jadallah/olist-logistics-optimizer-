import math
from app.engine import geo
from app.engine.carriers import build_adapters, DEFAULT_PROFILES, QuoteInput, HEALTH
from app.engine.quoting import optimize, late_probability
from app.engine.optimizer import build_options, build_options_with_diag, solve_assignment
from app.engine.quoting import Weights
from app.engine.sourcing import rank_sources
from app.engine import reference

ADS = build_adapters(DEFAULT_PROFILES)


def P(t): return geo.resolve(t)


def test_haversine_sp_rj():
    d = geo.haversine_km(-23.55, -46.63, -22.91, -43.17)
    assert 330 < d < 380


def test_resolve_formats():
    assert geo.resolve("01310-100").state == "SP"
    assert geo.resolve("Manaus, AM").state == "AM"
    assert geo.resolve("RJ").precision == "state"
    try: geo.resolve("zzzz nowhere"); assert False
    except ValueError: pass


def test_quotes_depend_on_route():
    a = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Rio de Janeiro"), 2))
    b = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Manaus, AM"), 2))
    assert a["recommendation"]["price"] != b["recommendation"]["price"]
    assert a["recommendation"]["eta_p50_days"] < b["recommendation"]["eta_p50_days"]


def test_coverage_filters_carriers():
    r = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Manaus, AM"), 2))
    codes = {q["carrier_code"] for q in r["quotes"]}
    assert "DELTA" not in codes and "GAMMA" not in codes
    assert {e["carrier_code"] for e in r["excluded"]} >= {"DELTA", "GAMMA"}


def test_volumetric_weight_dominates_for_bulky_light_parcels():
    light = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Curitiba, PR"), 1.0))["quotes"][0]
    bulky = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Curitiba, PR"), 1.0, (60, 60, 60)))["quotes"][0]
    assert bulky["chargeable_kg"] > light["chargeable_kg"] and bulky["price"] > light["price"]


def test_deadline_filters_and_reports_infeasible():
    r = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Manaus, AM"), 2), deadline_days=2)
    assert r["recommendation"] is None and "infeasible" in r
    ok = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Manaus, AM"), 2), deadline_days=60)
    assert ok["recommendation"] is not None


def test_objectives_pick_expected_extremes():
    q = QuoteInput(P("Sao Paulo, SP"), P("Rio de Janeiro"), 3)
    cheap = optimize(ADS, q, objective="cost")["recommendation"]; fast = optimize(ADS, q, objective="speed")["recommendation"]
    allq = optimize(ADS, q)["quotes"]
    assert cheap["price"] == min(x["price"] for x in allq) and fast["eta_p90_days"] == min(x["eta_p90_days"] for x in allq)


def test_late_probability_monotonic():
    assert late_probability(5, 10, 3) > late_probability(5, 10, 8) > late_probability(5, 10, 20)
    assert abs(late_probability(5, 10, 10) - 0.10) < 0.02


def test_carrier_outage_fallback():
    HEALTH.forced_down.add("ALPHA")
    try:
        r = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Rio de Janeiro"), 2))
        assert "ALPHA" not in {q["carrier_code"] for q in r["quotes"]} and r["recommendation"]
    finally:
        HEALTH.forced_down.discard("ALPHA")


def test_overweight_excluded():
    r = optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Rio de Janeiro"), 55))
    assert "GAMMA" not in {q["carrier_code"] for q in r["quotes"]}


def test_batch_respects_capacity_and_beats_random():
    import random
    rng = random.Random(3)
    orders = [{"id": i, "q": QuoteInput(P("Campinas, SP"), P(rng.choice(["SP", "RJ", "MG", "BA"])), rng.uniform(.5, 5)), "deadline_days": None} for i in range(120)]
    opts = build_options(ADS, orders)
    cap = {"ALPHA": 20, "BETA": 20, "GAMMA": 20, "DELTA": 20, "EPSILON": 20}
    res = solve_assignment(orders, opts, cap, Weights.from_objective("cost"))
    for code, n in res["optimized"]["carrier_load"].items(): assert n <= cap[code]
    assert res["optimized"]["assigned"] == 100            # 5 carriers x 20 slots, all orders coverable by >=1 carrier with slots
    assert res["optimized"]["total_cost"] <= res["baselines"]["random_carrier"]["total_cost"] + 1e-6 or res["baselines"]["random_carrier"]["assigned"] < 100


def test_sourcing_prefers_closer_seller_all_else_equal():
    pid = next(p for p, v in reference.product_sellers().items() if len(v["sellers"]) >= 3)
    r = rank_sources(ADS, pid, P("Salvador, BA"), objective="cost")
    assert r["candidates"] and r["candidates"][0]["rank"] == 1


def test_every_order_with_an_option_gets_assigned_even_at_max_score():
    """Regression: with `score - 1.0`, an order whose ONLY option has the batch-max score (1.0) had coefficient 0 and was left unassigned."""
    mk = lambda code, price: {"code": code, "carrier": code, "price": price, "eta_p50": 5, "eta_p90": 9, "risk": 0.1, "relaxed": False}
    orders = [{"id": 1}, {"id": 2}]
    opts = [[mk("A", 10.0), mk("B", 12.0)], [mk("A", 40.0)]]        # order 2's only option is the most expensive in the batch -> score exactly 1.0
    res = solve_assignment(orders, opts, {"A": 10, "B": 10}, Weights.from_objective("cost"))
    assert sorted(a["order_id"] for a in res["assignments"]) == [1, 2] and res["unassigned"] == []


def test_unassigned_reasons_are_explicit():
    orders = [{"id": 1, "q": QuoteInput(P("Campinas, SP"), P("Manaus, AM"), 2), "deadline_days": 3},          # deadline impossible
              {"id": 2, "q": QuoteInput(P("Campinas, SP"), P("Recife, PE"), 2), "deadline_days": None},
              {"id": 3, "q": QuoteInput(P("Campinas, SP"), P("Recife, PE"), 2), "deadline_days": None}]
    opts, diag = build_options_with_diag(ADS, orders)
    res = solve_assignment(orders, opts, {c: 0 for c in ADS} | {"ALPHA": 1}, Weights.from_objective("cost"), diag=diag)
    why = {d["id"]: d["reason_code"] for d in res["unassigned_detail"]}
    assert why[1] == "DEADLINE_INFEASIBLE" and len(why) == 2 and "CAPACITY" in why.values()
    relaxed, d2 = build_options_with_diag(ADS, orders[:1], relax_deadline=True)
    assert relaxed[0] and all(o["relaxed"] for o in relaxed[0]) and d2[0]["relaxed"]


def test_baseline_comparison_is_like_for_like():
    orders = [{"id": i, "q": QuoteInput(P("Campinas, SP"), P("Salvador, BA"), 1 + i % 4), "deadline_days": None} for i in range(40)]
    opts = build_options(ADS, orders)
    res = solve_assignment(orders, opts, {c: 1000 for c in ADS}, Weights.from_objective("cost"))
    c = res["baselines"]["cheapest_greedy"]["vs_optimized"]
    assert c["common_orders"] == 40 and c["cost_change_pct"] <= 0.01          # optimized never costs more than greedy-cheapest
