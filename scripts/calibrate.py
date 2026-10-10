"""Calibrate geo tables, price rate cards and ETA statistics from the real Olist CSVs.

Usage:  python scripts/calibrate.py --data /path/to/olist_csvs [--train-until 2018-01-01]
Outputs small reference files into app/data/ so the app runs without the 120MB CSVs.
"""
from __future__ import annotations
import argparse, json, unicodedata
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "app" / "data"
ZONE_EDGES = [0, 100, 400, 1000, 2000, 1e9]  # km -> zones 1..5


def norm(s) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


def hav(lat1, lng1, lat2, lng2):
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lng2) - np.radians(lng1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def zone_of(d):
    return np.digitize(d, ZONE_EDGES[1:-1], right=False) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--train-until", default="2018-01-01", help="orders purchased before this date train the models; later ones are held out")
    a = ap.parse_args()
    D = Path(a.data)
    OUT.mkdir(parents=True, exist_ok=True)

    geo = pd.read_csv(D / "olist_geolocation_dataset.csv", dtype={"geolocation_zip_code_prefix": str})
    geo["zip"] = geo["geolocation_zip_code_prefix"].str.zfill(5)
    geo = geo[(geo.geolocation_lat.between(-34, 6)) & (geo.geolocation_lng.between(-74, -34))]
    zipg = geo.groupby("zip").agg(lat=("geolocation_lat", "median"), lng=("geolocation_lng", "median"),
                                  state=("geolocation_state", lambda s: s.mode().iat[0]),
                                  city=("geolocation_city", lambda s: norm(s.mode().iat[0]))).reset_index()
    zipg.round({"lat": 5, "lng": 5}).to_csv(OUT / "geo_zip.csv", index=False)
    geo["city_n"] = geo.geolocation_city.map(norm)
    cityg = geo.groupby(["city_n", "geolocation_state"]).agg(lat=("geolocation_lat", "median"), lng=("geolocation_lng", "median"), n=("zip", "size")).reset_index()
    cityg = cityg[cityg.n >= 5].rename(columns={"city_n": "city", "geolocation_state": "state"})
    cityg.round({"lat": 5, "lng": 5}).to_csv(OUT / "geo_city.csv", index=False)
    statec = geo.groupby("geolocation_state").agg(lat=("geolocation_lat", "median"), lng=("geolocation_lng", "median")).reset_index().rename(columns={"geolocation_state": "state"})
    statec.round({"lat": 5, "lng": 5}).to_csv(OUT / "geo_state.csv", index=False)
    zmap = zipg.set_index("zip")
    smap = statec.set_index("state")

    cust = pd.read_csv(D / "olist_customers_dataset.csv", dtype={"customer_zip_code_prefix": str})
    cust["czip"] = cust.customer_zip_code_prefix.str.zfill(5)
    sel = pd.read_csv(D / "olist_sellers_dataset.csv", dtype={"seller_zip_code_prefix": str})
    sel["szip"] = sel.seller_zip_code_prefix.str.zfill(5)
    prod = pd.read_csv(D / "olist_products_dataset.csv")
    items = pd.read_csv(D / "olist_order_items_dataset.csv")
    tcols = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"]
    orders = pd.read_csv(D / "olist_orders_dataset.csv", parse_dates=tcols)

    def coords(zips, states):
        lat = zips.map(zmap["lat"]).fillna(states.map(smap["lat"]))
        lng = zips.map(zmap["lng"]).fillna(states.map(smap["lng"]))
        return lat, lng

    sel["slat"], sel["slng"] = coords(sel.szip, sel.seller_state)
    cust["clat"], cust["clng"] = coords(cust.czip, cust.customer_state)
    sel[["seller_id", "szip", "seller_city", "seller_state", "slat", "slng"]].round({"slat": 5, "slng": 5}).to_csv(OUT / "sellers.csv", index=False)

    prod["weight_kg"] = (prod.product_weight_g.fillna(prod.product_weight_g.median()) / 1000).clip(lower=0.05)
    prod["vol_cm3"] = prod.product_length_cm * prod.product_height_cm * prod.product_width_cm
    prod["vol_cm3"] = prod.vol_cm3.fillna(prod.vol_cm3.median())
    it = items.merge(prod[["product_id", "weight_kg", "vol_cm3"]], on="product_id", how="left")
    it = it.merge(orders[["order_id", "customer_id", "order_status"] + tcols], on="order_id")
    it = it.merge(sel[["seller_id", "seller_state", "slat", "slng"]], on="seller_id", how="left").merge(cust[["customer_id", "customer_state", "clat", "clng"]], on="customer_id", how="left")

    g = it.groupby(["order_id", "seller_id"]).agg(
        freight=("freight_value", "sum"), kg=("weight_kg", "sum"), vol=("vol_cm3", "sum"),
        s_state=("seller_state", "first"), c_state=("customer_state", "first"), slat=("slat", "first"), slng=("slng", "first"), clat=("clat", "first"), clng=("clng", "first"),
        purchase=("order_purchase_timestamp", "first"), approved=("order_approved_at", "first"), handoff=("order_delivered_carrier_date", "first"),
        delivered=("order_delivered_customer_date", "first"), estimated=("order_estimated_delivery_date", "first"), status=("order_status", "first")).reset_index()
    g = g.dropna(subset=["slat", "clat"])
    g["dist"] = hav(g.slat.values, g.slng.values, g.clat.values, g.clng.values)
    g["zone"] = zone_of(g.dist.values)
    g["chg_kg"] = np.maximum(g.kg, g.vol / 6000.0)
    train = g[g.purchase < a.train_until]

    # ---- price rate cards by zone: freight = base + per_kg * chargeable_kg
    ok = train[(train.freight > 0) & (train.freight < train.freight.quantile(0.99))]
    cards = {}
    for z in range(1, 6):
        s = ok[ok.zone == z]
        s = s[s.chg_kg < s.chg_kg.quantile(0.99)]
        X = np.c_[np.ones(len(s)), s.chg_kg.values]
        coef, *_ = np.linalg.lstsq(X, s.freight.values, rcond=None)
        cards[z] = {"base": round(max(float(coef[0]), 5.0), 2), "per_kg": round(max(float(coef[1]), 0.5), 2), "n": int(len(s))}

    def predict(df):
        return np.array([cards[int(z)]["base"] + cards[int(z)]["per_kg"] * k for z, k in zip(df.zone, df.chg_kg)])

    def fit(df):
        p = predict(df); y = df.freight.values
        return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum()), float(np.abs(y - p).mean())

    test = g[(g.purchase >= a.train_until) & (g.freight > 0)]
    r2_tr, mae_tr = fit(ok)
    r2_te, mae_te = fit(test) if len(test) else (None, None)

    # ---- ETA: transit days (handoff -> delivered) per state pair, shrunk toward zone stats
    dl = g[(g.status == "delivered") & g.delivered.notna() & g.handoff.notna()].copy()
    dl["transit"] = (dl.delivered - dl.handoff).dt.total_seconds() / 86400
    dl["handling"] = (dl.handoff - dl.approved).dt.total_seconds() / 86400
    dl["promised"] = (dl.estimated - dl.purchase).dt.total_seconds() / 86400
    dl["late"] = (dl.delivered > dl.estimated).astype(int)
    dl = dl[(dl.transit > 0) & (dl.transit < 90)]
    dtr = dl[dl.purchase < a.train_until]
    zs = dtr.groupby("zone").agg(p50=("transit", lambda s: s.quantile(.5)), p90=("transit", lambda s: s.quantile(.9)), late=("late", "mean"), n=("transit", "size"))
    rows = []
    for (s, c), s_ in dtr.groupby(["s_state", "c_state"]):
        n = len(s_); z = int(s_.zone.mode().iat[0]); w = n / (n + 30.0)
        p50 = w * s_.transit.quantile(.5) + (1 - w) * zs.loc[z, "p50"]
        p90 = w * s_.transit.quantile(.9) + (1 - w) * zs.loc[z, "p90"]
        late = w * s_.late.mean() + (1 - w) * zs.loc[z, "late"]
        rows.append((s, c, n, round(p50, 2), round(max(p90, p50 * 1.15), 2), round(late, 4), round(s_.dist.median(), 1), round(s_.promised.median(), 1)))
    rs_df = pd.DataFrame(rows, columns=["origin_state", "dest_state", "n", "transit_p50", "transit_p90", "late_rate", "median_km", "median_promised_days"])
    rs_df.to_csv(OUT / "route_stats.csv", index=False)
    zrows = {int(z): {"p50": round(r.p50, 2), "p90": round(r.p90, 2), "late": round(r.late, 4), "n": int(r.n)} for z, r in zs.iterrows()}

    # seller handling time (approved -> handed to carrier)
    hv = dtr[(dtr.handling >= 0) & (dtr.handling < 30)]
    gh = float(hv.handling.median())
    sstat = hv.groupby("seller_id").handling.agg(["median", "size"]).reset_index().rename(columns={"median": "handling_p50", "size": "n"})
    sstat["handling_p50"] = (sstat.n / (sstat.n + 5) * sstat.handling_p50 + 5 / (sstat.n + 5) * gh).round(2)
    sstat.to_csv(OUT / "seller_stats.csv", index=False)

    # products sold by 2+ sellers -> sourcing candidates
    ps = items.groupby("product_id").seller_id.nunique()
    multi = ps[ps >= 2].index
    pm = items[items.product_id.isin(multi)].groupby(["product_id", "seller_id"]).size().reset_index(name="n").merge(
        prod[["product_id", "weight_kg", "product_length_cm", "product_height_cm", "product_width_cm", "product_category_name"]], on="product_id")
    pm.round(3).to_csv(OUT / "product_sellers.csv", index=False)

    # held-out ETA calibration
    rs = rs_df.set_index(["origin_state", "dest_state"])
    te = dl[dl.purchase >= a.train_until].copy()
    def lk(r, col):
        try: return rs.loc[(r.s_state, r.c_state), col]
        except KeyError: return zs.loc[int(r.zone), col.replace("transit_", "")]
    cal = {}
    if len(te):
        te["p50"] = [lk(r, "transit_p50") for r in te.itertuples()]
        te["p90"] = [lk(r, "transit_p90") for r in te.itertuples()]
        cal = {"holdout_n": int(len(te)), "p50_coverage": float((te.transit <= te.p50).mean()), "p90_coverage": float((te.transit <= te.p90).mean())}

    calib = {"train_until": a.train_until, "currency": "BRL", "zone_edges_km": ZONE_EDGES[1:-1], "rate_cards": cards, "zone_eta": zrows,
             "global_handling_days": round(gh, 2),
             "price_model_fit": {"r2_train": r2_tr, "mae_train_brl": mae_tr, "r2_holdout": r2_te, "mae_holdout_brl": mae_te},
             "eta_holdout_calibration": cal, "n_subshipments": int(len(g)), "multi_seller_orders": int((g.groupby("order_id").size() > 1).sum()),
             "freight_median_brl": float(g.freight.median()), "transit_median_days": float(dl.transit.median())}
    (OUT / "calibration.json").write_text(json.dumps(calib, indent=2))
    print(json.dumps(calib, indent=2))


if __name__ == "__main__":
    main()
