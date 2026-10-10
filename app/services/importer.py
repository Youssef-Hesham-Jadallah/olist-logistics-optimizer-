"""Olist import: join orders x items x products x sellers x customers x geolocation, split multi-seller orders into
sub-shipments, map statuses, bulk insert (executemany in batches). Works from a directory of Olist CSVs."""
from __future__ import annotations
import logging, zipfile
from pathlib import Path
from typing import Callable, Optional
import numpy as np
import pandas as pd
from ..db import audit, db, now
from ..domain import OLIST_STATUS_MAP, Status
from ..engine import geo
from .core import sanitize_cell

log = logging.getLogger("olist.import")
REQUIRED = ["olist_orders_dataset.csv", "olist_order_items_dataset.csv", "olist_customers_dataset.csv", "olist_sellers_dataset.csv", "olist_products_dataset.csv"]
BATCH = 5000


def extract_zip(zpath: Path, dest: Path, max_bytes: int) -> Path:
    dest.mkdir(parents=True, exist_ok=True); total = 0
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            if info.is_dir() or not name.endswith(".csv"): continue
            total += info.file_size
            if total > max_bytes: raise ValueError("archive too large when extracted")
            with z.open(info) as src, open(dest / name, "wb") as out:
                while chunk := src.read(1 << 20): out.write(chunk)
    return dest


def _zip_coords(df: pd.DataFrame, zcol: str, scol: str, label_col: str):
    zips, _, states, _ = geo._tables()
    z = df[zcol].astype(str).str.zfill(5)
    lat = z.map(lambda k: zips[k][0] if k in zips else np.nan); lng = z.map(lambda k: zips[k][1] if k in zips else np.nan)
    lat = lat.fillna(df[scol].map(lambda s: states.get(s, (np.nan, np.nan))[0])); lng = lng.fillna(df[scol].map(lambda s: states.get(s, (np.nan, np.nan))[1]))
    return z, lat, lng


def import_directory(data_dir: Path, *, limit: Optional[int] = None, user: Optional[str] = "import", progress: Optional[Callable[[int, int], None]] = None) -> dict:
    missing = [f for f in REQUIRED if not (data_dir / f).exists()]
    if missing: raise ValueError(f"missing Olist files: {', '.join(missing)}")
    orders = pd.read_csv(data_dir / "olist_orders_dataset.csv", usecols=["order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date", "order_delivered_carrier_date"])
    if limit: orders = orders.head(limit)
    items = pd.read_csv(data_dir / "olist_order_items_dataset.csv", usecols=["order_id", "product_id", "seller_id", "price", "freight_value"])
    items = items[items.order_id.isin(orders.order_id)]
    prod = pd.read_csv(data_dir / "olist_products_dataset.csv", usecols=["product_id", "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"])
    wmed = prod.product_weight_g.median()
    prod["kg"] = (prod.product_weight_g.fillna(wmed) / 1000).clip(lower=0.05)
    prod["vol"] = (prod.product_length_cm * prod.product_height_cm * prod.product_width_cm)
    prod["vol"] = prod.vol.fillna(prod.vol.median())
    sel = pd.read_csv(data_dir / "olist_sellers_dataset.csv", dtype=str)
    sel["szip"], sel["slat"], sel["slng"] = _zip_coords(sel, "seller_zip_code_prefix", "seller_state", "seller_city")
    cust = pd.read_csv(data_dir / "olist_customers_dataset.csv", dtype=str)
    cust["czip"], cust["clat"], cust["clng"] = _zip_coords(cust, "customer_zip_code_prefix", "customer_state", "customer_city")
    it = items.merge(prod[["product_id", "kg", "vol"]], on="product_id", how="left")
    g = it.groupby(["order_id", "seller_id"], sort=False).agg(kg=("kg", "sum"), vol=("vol", "sum"), value=("price", "sum"), freight=("freight_value", "sum")).reset_index()
    g = g.merge(orders, on="order_id").merge(sel[["seller_id", "seller_city", "seller_state", "szip", "slat", "slng"]], on="seller_id", how="left") \
         .merge(cust[["customer_id", "customer_city", "customer_state", "czip", "clat", "clng"]], on="customer_id", how="left")
    skipped_nocoord = int(g.slat.isna().sum() + g.clat.isna().sum() - (g.slat.isna() & g.clat.isna()).sum())
    g = g.dropna(subset=["slat", "clat"])
    g["n_sub"] = g.groupby("order_id")["seller_id"].transform("size")
    g["k"] = g.groupby("order_id").cumcount() + 1
    for c in ("order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date", "order_delivered_carrier_date"):
        g[c] = pd.to_datetime(g[c], errors="coerce", utc=True)
    g["transit"] = (g.order_delivered_customer_date - g.order_delivered_carrier_date).dt.total_seconds() / 86400
    ts = now(); total = len(g); inserted = skipped = multi = 0
    iso = lambda x: None if pd.isna(x) else x.isoformat(timespec="seconds")
    with db() as con:
        existing = {r[0] for r in con.execute("SELECT order_id FROM shipments")}
        batch = []
        def flush():
            nonlocal batch, inserted
            if batch:
                con.executemany("""INSERT OR IGNORE INTO shipments(order_id,parent_order_id,seller_id,origin_label,origin_state,origin_zip,origin_lat,origin_lng,dest_label,dest_state,dest_zip,dest_lat,dest_lng,
                    weight_kg,vol_cm3,declared_value,deadline,ref_ts,status,hist_freight,hist_transit_days,hist_delivered_ts,source,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'olist-import',?,?)""", batch)
                inserted += len(batch); batch = []
        for i, r in enumerate(g.itertuples(index=False), 1):
            oid = r.order_id if r.n_sub == 1 else f"{r.order_id}#{r.k}"
            if oid in existing: skipped += 1; continue
            if r.n_sub > 1 and r.k == 1: multi += 1
            st = OLIST_STATUS_MAP.get(str(r.order_status).lower(), Status.PENDING).value
            batch.append((sanitize_cell(oid), None if r.n_sub == 1 else r.order_id, r.seller_id, f"{str(r.seller_city).title()}/{r.seller_state} ({r.szip})", r.seller_state, r.szip, float(r.slat), float(r.slng),
                          f"{str(r.customer_city).title()}/{r.customer_state} ({r.czip})", r.customer_state, r.czip, float(r.clat), float(r.clng), float(r.kg), float(r.vol), round(float(r.value), 2),
                          iso(r.order_estimated_delivery_date), iso(r.order_purchase_timestamp) or ts, st, round(float(r.freight), 2),
                          None if pd.isna(r.transit) else round(float(r.transit), 2), iso(r.order_delivered_customer_date), ts, ts))
            if len(batch) >= BATCH:
                flush()
                if progress: progress(i, total)
        flush()
        audit(con, user, "import", "olist", None, {"inserted": inserted, "skipped_existing": skipped, "multi_seller_orders": multi, "dropped_no_coords": len(orders) - g.order_id.nunique()})
    return {"inserted": inserted, "skipped_existing": skipped, "multi_seller_orders_split": multi, "rows_without_coordinates_dropped": skipped_nocoord, "orders_in_source": int(len(orders)),
            "note": "Orders with several sellers are split into sub-shipments (<order_id>#1, #2 ...); status values are mapped to the internal enum."}
