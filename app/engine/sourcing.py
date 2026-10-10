"""Nearest-source selection: among sellers that can supply a product, rank by distance, cost, speed."""
from __future__ import annotations
from typing import Optional
from . import reference
from .carriers import CarrierAdapter, QuoteInput
from .geo import Place, distance_km, resolve
from .quoting import optimize


def rank_sources(adapters: dict[str, CarrierAdapter], product_id: str, destination: Place, *, quantity: int = 1, objective: str = "balanced",
                 deadline_days: Optional[float] = None, top: int = 10) -> dict:
    prod = reference.product_sellers().get(product_id)
    if not prod:
        raise KeyError(product_id)
    kg = prod["weight_kg"] * quantity
    dims = tuple(prod["dims"]) if all(prod["dims"]) else None
    cands = []
    sell = reference.sellers()
    for s in prod["sellers"]:
        sd = sell.get(s["seller_id"])
        if not sd: continue
        origin = Place(sd["lat"], sd["lng"], sd["state"], f"{sd['city'].title()}/{sd['state']}", sd["zip"], "zip")
        h = reference.handling_days(s["seller_id"])
        res = optimize(adapters, QuoteInput(origin, destination, kg, dims, 0.0, h), objective=objective, deadline_days=deadline_days)
        best = res["recommendation"]
        cands.append({"seller_id": s["seller_id"], "origin": origin.as_dict(), "distance_km": round(distance_km(origin, destination), 1), "seller_handling_days": h,
                      "past_orders_of_product": s["past_orders"], "best_option": best, "feasible": best is not None})
    feas = [c for c in cands if c["feasible"]]
    if feas:
        def key(c): return (c["best_option"]["price"] / max(min(x["best_option"]["price"] for x in feas), 1e-6)) * 0.4 + (c["best_option"]["eta_p90_days"] / max(min(x["best_option"]["eta_p90_days"] for x in feas), 1e-6)) * 0.45 + (c["distance_km"] / max(min(x["distance_km"] for x in feas), 1.0)) * 0.15
        for c in feas: c["source_score"] = round(key(c), 3)
        feas.sort(key=lambda c: c["source_score"])
    ranked = feas + [c for c in cands if not c["feasible"]]
    for i, c in enumerate(ranked, 1): c["rank"] = i
    return {"product_id": product_id, "category": prod["category"], "quantity": quantity, "weight_kg": round(kg, 2), "destination": destination.as_dict(), "candidates": ranked[:top],
            "note": "Olist has no inventory data: candidates are sellers that sold this product historically."}
