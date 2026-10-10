"""Backtest on the Olist HOLD-OUT year (orders purchased on/after --train-until; models were calibrated on earlier orders only).

Evidence produced (app/data/backtest.json, shown on the Backtest page):
 1. Model accuracy: price (R2/MAE) and ETA (P50/P90 coverage) on unseen orders.
 2. Policy comparison on the same orders inside the simulated carrier universe: random / single carrier (status quo) /
    cheapest / fastest / SYSTEM (balanced + deadline). Deadline = the delivery date Olist actually promised.
 3. Nearest-source: for single-item orders of products sold by 2+ sellers, actual seller vs the system's best seller.
IMPORTANT: carrier prices/ETAs are SIMULATED (calibrated), so saving numbers are valid inside that universe, not live-market claims.
Usage: python scripts/backtest.py --data /path/to/olist_csvs [--sample 6000]
"""
from __future__ import annotations
import argparse, json, os, random, sys, tempfile
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--sample", type=int, default=6000); ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sources", type=int, default=1200)
    a = ap.parse_args()
    os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
    from app import db as dbm
    from app.services import importer, core
    from app.engine import reference, geo
    from app.engine.carriers import QuoteInput, build_adapters, DEFAULT_PROFILES
    from app.engine.geo import Place
    from app.engine.quoting import optimize, collect_quotes, late_probability
    from app.engine.sourcing import rank_sources
    dbm.init_db(); importer.import_directory(Path(a.data))
    cal = reference.calibration(); cut = cal["train_until"]
    ads = build_adapters(DEFAULT_PROFILES)
    with dbm.db() as con:
        rows = [dict(r) for r in con.execute("""SELECT * FROM shipments WHERE status='Delivered' AND ref_ts>=? AND hist_delivered_ts IS NOT NULL AND deadline IS NOT NULL AND hist_freight>0""", (cut,))]
    rng = random.Random(a.seed); rng.shuffle(rows); rows = rows[:a.sample]
    pol = {k: {"cost": 0.0, "eta50": 0.0, "exp_late": 0.0, "n": 0} for k in ("random", "status_quo_single_carrier", "cheapest", "fastest", "system_cost_with_deadline", "system_expected_cost", "system_balanced")}
    actual = {"freight": 0.0, "late": 0, "n": 0}
    for s in rows:
        o = Place(s["origin_lat"], s["origin_lng"], s["origin_state"], s["origin_label"], s["origin_zip"]); d = Place(s["dest_lat"], s["dest_lng"], s["dest_state"], s["dest_label"], s["dest_zip"])
        c = (s["vol_cm3"] or 0) ** (1 / 3) if s["vol_cm3"] else None
        qi = QuoteInput(o, d, s["weight_kg"], (c, c, c) if c else None, s["declared_value"], reference.handling_days(s["seller_id"]), ship_month=datetime.fromisoformat(s["ref_ts"]).month)
        promised = (datetime.fromisoformat(s["deadline"]) - datetime.fromisoformat(s["ref_ts"])).total_seconds() / 86400
        qs, _ = collect_quotes(ads, qi)
        if not qs: continue
        def stat(q): return q.price, q.eta_p50_days, late_probability(q.eta_p50_days, q.eta_p90_days, promised)
        alpha = next((q for q in qs if q.carrier_code == "ALPHA"), qs[0])
        def sys_pick(obj):
            r_ = optimize(ads, qi, objective=obj, deadline_days=promised)["recommendation"]
            return next(q for q in qs if q.carrier_code == r_["carrier_code"]) if r_ else min(qs, key=lambda q: q.eta_p90_days)   # infeasible -> fastest
        picks = {"random": rng.choice(qs), "status_quo_single_carrier": alpha, "cheapest": min(qs, key=lambda q: q.price), "fastest": min(qs, key=lambda q: q.eta_p90_days),
                 "system_cost_with_deadline": sys_pick("cost"), "system_expected_cost": sys_pick("expected_cost"), "system_balanced": sys_pick("balanced")}
        for k, q in picks.items():
            p, e, l = stat(q); pol[k]["cost"] += p; pol[k]["eta50"] += e; pol[k]["exp_late"] += l; pol[k]["n"] += 1
        actual["freight"] += s["hist_freight"]; actual["late"] += int(s["hist_delivered_ts"] > s["deadline"]); actual["n"] += 1
    n = pol["system_balanced"]["n"]; out_pol = {}
    for k, v in pol.items():
        out_pol[k] = {"total_cost_brl": round(v["cost"], 2), "avg_cost_brl": round(v["cost"] / v["n"], 2), "avg_eta_p50_days": round(v["eta50"] / v["n"], 2), "expected_late_rate": round(v["exp_late"] / v["n"], 4)}
    base = out_pol["status_quo_single_carrier"]
    def gain(name):
        x = out_pol[name]
        return {"cost_saving_pct": round((1 - x["total_cost_brl"] / base["total_cost_brl"]) * 100, 1), "late_rate_change_pp": round((x["expected_late_rate"] - base["expected_late_rate"]) * 100, 2),
                "eta_p50_change_days": round(x["avg_eta_p50_days"] - base["avg_eta_p50_days"], 2)}
    gains = {"system_cost_with_deadline_vs_status_quo": gain("system_cost_with_deadline"), "system_expected_cost_vs_status_quo": gain("system_expected_cost"), "system_balanced_vs_status_quo": gain("system_balanced"), "cheapest_vs_status_quo": gain("cheapest")}

    # ---- nearest-source backtest
    ps = reference.product_sellers()
    import pandas as pd
    items = pd.read_csv(Path(a.data) / "olist_order_items_dataset.csv", usecols=["order_id", "product_id", "seller_id"])
    cnt = items.groupby("order_id").size(); single = items[items.order_id.isin(cnt[cnt == 1].index) & items.product_id.isin(ps.keys())]
    with dbm.db() as con:
        ship = {r["order_id"]: dict(r) for r in con.execute("SELECT * FROM shipments WHERE status='Delivered' AND ref_ts>=? AND parent_order_id IS NULL", (cut,))}
    single = single[single.order_id.isin(ship.keys())].sample(frac=1, random_state=a.seed).head(a.sources)
    d_act = d_best = 0.0; p_act = p_best = 0.0; e_act = e_best = 0.0; changed = 0; k = 0
    for r in single.itertuples():
        s = ship[r.order_id]; dest = Place(s["dest_lat"], s["dest_lng"], s["dest_state"], s["dest_label"], s["dest_zip"])
        prod = ps[r.product_id]
        try: res = rank_sources(ads, r.product_id, dest, objective="balanced", top=20)
        except Exception: continue
        act = next((c for c in res["candidates"] if c["seller_id"] == r.seller_id), None)
        best = next((c for c in res["candidates"] if c["feasible"]), None)
        if not act or not best or not act["feasible"]: continue
        k += 1; changed += int(best["seller_id"] != r.seller_id)
        d_act += act["distance_km"]; d_best += best["distance_km"]; p_act += act["best_option"]["price"]; p_best += best["best_option"]["price"]; e_act += act["best_option"]["eta_p50_days"]; e_best += best["best_option"]["eta_p50_days"]
    src = {"orders_evaluated": k, "pct_orders_where_system_picks_different_seller": round(changed / k * 100, 1) if k else None,
           "avg_distance_km": {"actual_seller": round(d_act / k, 1), "system_pick": round(d_best / k, 1)} if k else None,
           "avg_price_brl": {"actual_seller": round(p_act / k, 2), "system_pick": round(p_best / k, 2)} if k else None,
           "avg_eta_p50_days": {"actual_seller": round(e_act / k, 2), "system_pick": round(e_best / k, 2)} if k else None,
           "assumption": "Olist data has no inventory: any seller that sold the product historically is assumed able to supply it."}

    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "train_until": cut, "holdout_orders_evaluated": n,
              "model_accuracy": {"price": reference.models()["price"]["fit_new_model"]["holdout"], "price_previous_model": reference.models()["price"]["fit_previous_zone_card"]["holdout"], "eta": cal["eta_holdout_calibration"],
                                 "note": "Price: log-linear model (distance, weight, volume, value, destination) fitted on pre-cutoff orders; R2 is modest because Olist freight also depends on carrier/product factors not in the data, and 2018 freight drifted upward (negative bias). ETA P90 should cover ~90% of unseen orders."},
              "actual_history_same_orders": {"avg_freight_paid_brl": round(actual["freight"] / actual["n"], 2), "late_rate": round(actual["late"] / actual["n"], 4)},
              "policies": out_pol, "gains": gains, "nearest_source": src,
              "late_penalty_brl": 40.0, "assumptions": ["Expected-cost policy charges R$ 40 per expected late delivery (editable assumption, not measured).", "Carriers are simulated; rate cards/ETAs calibrated on pre-cutoff Olist orders; per-carrier multipliers are assumptions.",
                              "Status quo = a single Alpha-like carrier (multipliers 1.0, so ~ historical averages).", "Deadline = delivery date Olist actually promised the customer; late probability from a lognormal fitted to P50/P90.",
                              "No inventory or carrier capacity in this per-order comparison (capacity is handled in the batch optimizer)."]}
    (Path(__file__).resolve().parent.parent / "app" / "data" / "backtest.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
