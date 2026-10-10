"""Calibrated reference data (rate cards, ETA stats, sellers) produced by scripts/calibrate.py."""
from __future__ import annotations
import csv, json
from functools import lru_cache
from ..config import DATA_DIR


@lru_cache(maxsize=1)
def calibration() -> dict:
    return json.loads((DATA_DIR / "calibration.json").read_text())


@lru_cache(maxsize=1)
def route_stats() -> dict:
    out = {}
    with open(DATA_DIR / "route_stats.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[(r["origin_state"], r["dest_state"])] = {"n": int(r["n"]), "p50": float(r["transit_p50"]), "p90": float(r["transit_p90"]), "late": float(r["late_rate"]), "median_km": float(r["median_km"])}
    return out


@lru_cache(maxsize=1)
def sellers() -> dict:
    out = {}
    with open(DATA_DIR / "sellers.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["seller_id"]] = {"id": r["seller_id"], "zip": r["szip"], "city": r["seller_city"], "state": r["seller_state"], "lat": float(r["slat"]), "lng": float(r["slng"])}
    return out


@lru_cache(maxsize=1)
def seller_handling() -> dict:
    out = {}
    with open(DATA_DIR / "seller_stats.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["seller_id"]] = (float(r["handling_p50"]), int(r["n"]))
    return out


@lru_cache(maxsize=1)
def product_sellers() -> dict:
    out: dict[str, dict] = {}
    with open(DATA_DIR / "product_sellers.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = out.setdefault(r["product_id"], {"product_id": r["product_id"], "category": r["product_category_name"], "weight_kg": float(r["weight_kg"] or 0.5),
                                                  "dims": [_f(r["product_length_cm"]), _f(r["product_height_cm"]), _f(r["product_width_cm"])], "sellers": []})
            p["sellers"].append({"seller_id": r["seller_id"], "past_orders": int(r["n"])})
    return out


def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def handling_days(seller_id: str | None) -> float:
    if seller_id and seller_id in seller_handling():
        return seller_handling()[seller_id][0]
    return calibration()["global_handling_days"]


@lru_cache(maxsize=1)
def models() -> dict:
    f = DATA_DIR / "models.json"
    return json.loads(f.read_text()) if f.exists() else {}
