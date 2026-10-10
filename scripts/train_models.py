"""Train the lightweight price model + peak-season ETA factors on pre-cutoff Olist orders, evaluate on the hold-out year.
Writes app/data/models.json. Run AFTER scripts/calibrate.py (it needs the geo/route tables).
Usage: python scripts/train_models.py --data ./olist_data [--train-until 2018-01-01]"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.engine.pricing import feature_matrix, FEATURE_NAMES
from scripts.calibrate import hav, zone_of

OUT = ROOT / "app" / "data"


def build(D: Path):
    zm = pd.read_csv(OUT / "geo_zip.csv", dtype={"zip": str}).set_index("zip"); sm = pd.read_csv(OUT / "geo_state.csv").set_index("state")
    cust = pd.read_csv(D / "olist_customers_dataset.csv", dtype={"customer_zip_code_prefix": str}); sel = pd.read_csv(D / "olist_sellers_dataset.csv", dtype={"seller_zip_code_prefix": str})
    cust["z"] = cust.customer_zip_code_prefix.str.zfill(5); sel["z"] = sel.seller_zip_code_prefix.str.zfill(5)
    for df, s, p in ((cust, "customer_state", "c"), (sel, "seller_state", "s")):
        df[p + "lat"] = df.z.map(zm.lat).fillna(df[s].map(sm.lat)); df[p + "lng"] = df.z.map(zm.lng).fillna(df[s].map(sm.lng))
    prod = pd.read_csv(D / "olist_products_dataset.csv")
    prod["kg"] = (prod.product_weight_g.fillna(prod.product_weight_g.median()) / 1000).clip(lower=.05)
    prod["vol"] = prod.product_length_cm * prod.product_height_cm * prod.product_width_cm; prod["vol"] = prod.vol.fillna(prod.vol.median())
    items = pd.read_csv(D / "olist_order_items_dataset.csv")
    orders = pd.read_csv(D / "olist_orders_dataset.csv", parse_dates=["order_purchase_timestamp", "order_delivered_carrier_date", "order_delivered_customer_date"])
    it = items.merge(prod[["product_id", "kg", "vol"]], on="product_id").merge(orders[["order_id", "customer_id", "order_purchase_timestamp", "order_delivered_carrier_date", "order_delivered_customer_date"]], on="order_id") \
        .merge(sel[["seller_id", "seller_state", "slat", "slng"]], on="seller_id").merge(cust[["customer_id", "customer_state", "clat", "clng"]], on="customer_id")
    g = it.groupby(["order_id", "seller_id"]).agg(freight=("freight_value", "sum"), kg=("kg", "sum"), vol=("vol", "sum"), value=("price", "sum"), n=("order_item_id", "size"), ss=("seller_state", "first"), cs=("customer_state", "first"),
        slat=("slat", "first"), slng=("slng", "first"), clat=("clat", "first"), clng=("clng", "first"), ts=("order_purchase_timestamp", "first"), hand=("order_delivered_carrier_date", "first"), dlv=("order_delivered_customer_date", "first")).reset_index().dropna(subset=["slat", "clat"])
    g["dist"] = hav(g.slat.values, g.slng.values, g.clat.values, g.clng.values); g["zone"] = zone_of(g.dist.values)
    g["chg"] = np.maximum(g.kg, g.vol / 6000.0); g["month"] = g.ts.dt.month
    return g


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--train-until", default="2018-01-01"); a = ap.parse_args()
    g = build(Path(a.data)); cut = pd.Timestamp(a.train_until)
    pr = g[g.freight > 0]; tr, te = pr[pr.ts < cut], pr[pr.ts >= cut]; trc = tr[tr.freight < tr.freight.quantile(.99)]
    X = lambda d: feature_matrix(d.dist, d.chg, d.vol, d.value, d.n, d.ss == d.cs, d.cs)
    coef, *_ = np.linalg.lstsq(X(trc), np.log(trc.freight.values), rcond=None)
    smear = float(np.exp(((np.log(trc.freight.values) - X(trc) @ coef) ** 2).mean() / 2 * 0 + (np.log(trc.freight.values) - X(trc) @ coef).var() / 2))
    pred = lambda d: np.exp(X(d) @ coef) * smear
    fit = lambda d, p: {"r2": float(1 - ((d.freight - p) ** 2).sum() / ((d.freight - d.freight.mean()) ** 2).sum()), "mae_brl": float(np.abs(d.freight - p).mean()), "bias_brl": float((p - d.freight).mean()), "n": int(len(d))}
    # baseline: zone rate card (previous model) for an honest before/after
    cards = {}
    for z in range(1, 6):
        s = trc[trc.zone == z]; c, *_ = np.linalg.lstsq(np.c_[np.ones(len(s)), s.chg], s.freight, rcond=None); cards[z] = c
    base = lambda d: np.array([cards[z][0] + cards[z][1] * k for z, k in zip(d.zone, d.chg)])
    fit_new = {"train": fit(trc, pred(trc)), "holdout": fit(te, pred(te))}; fit_old = {"train": fit(trc, base(trc)), "holdout": fit(te, base(te))}
    # peak-season ETA factors (Nov/Dec) from the training year: ratio of transit to the route+zone baseline, relative to the all-month ratio
    cal = json.loads((OUT / "calibration.json").read_text()); rs = pd.read_csv(OUT / "route_stats.csv").set_index(["origin_state", "dest_state"])
    d = g[(g.dlv.notna()) & (g.hand.notna())].copy(); d["tr"] = (d.dlv - d.hand).dt.total_seconds() / 86400; d = d[(d.tr > 0) & (d.tr < 90)]; d = d[d.ts < cut]
    def base50(r):
        z = cal["zone_eta"][str(int(r.zone))]; k = (r.ss, r.cs)
        if k in rs.index: w = rs.loc[k, "n"] / (rs.loc[k, "n"] + 30); return w * rs.loc[k, "transit_p50"] + (1 - w) * z["p50"]
        return z["p50"]
    d["b"] = [base50(r) for r in d.itertuples()]; d["r"] = d.tr / d.b
    season = {}
    for m in (11, 12):
        s = d[d.month == m]
        if len(s) >= 500: season[str(m)] = {"p50": round(float(s.r.median() / d.r.median()), 3), "p90": round(float(s.r.quantile(.9) / d.r.quantile(.9)), 3), "n": int(len(s)), "evidence": f"{a.train_until} training window only (one peak season); NOT validated on the hold-out (Jan-Aug)"}
    models = {"price": {"features": FEATURE_NAMES, "coef": [float(x) for x in coef], "smear": smear, "median_value": float(trc.value.median()), "train_until": a.train_until,
                        "fit_new_model": fit_new, "fit_previous_zone_card": fit_old,
                        "note": "Hold-out bias is negative: 2018 freight was higher than the training years (price drift) - an unavoidable limit of fitting on the past."},
              "season": season, "default_late_penalty_brl": 40.0}
    (OUT / "models.json").write_text(json.dumps(models, indent=2)); print(json.dumps({k: models[k] for k in ("season",)}, indent=1)); print(json.dumps({"new": fit_new, "previous": fit_old}, indent=1))


if __name__ == "__main__":
    main()
