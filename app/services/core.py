"""Business logic on top of the DB: carriers, shipments, quoting, booking, rerouting, KPIs, analytics. No web-framework imports."""
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional
from ..db import audit, now
from ..domain import Status, check_transition, InvalidTransition
from ..engine import geo, reference
from ..engine.carriers import CarrierProfile, QuoteInput, Quote, build_adapters, HEALTH, CarrierAdapter
from ..engine.geo import Place
from ..engine.quoting import optimize
from . import notify

SHIP_COLS = "notify_email,id,order_id,parent_order_id,seller_id,origin_label,origin_state,origin_zip,dest_label,dest_state,dest_zip,weight_kg,declared_value,deadline,status,carrier_code,price,currency,eta_p50,eta_p90,late_risk,tracking_code,booked_at,delivered_at,hist_freight,hist_transit_days,source,created_at,updated_at,ref_ts"


class NotFound(Exception): pass
class Conflict(Exception): pass


def sanitize_cell(v):
    """CSV/Excel formula-injection guard: neutralise cells that start with = + - @ (also tab/CR)."""
    if isinstance(v, str) and v and v[0] in "=+-@\t\r":
        return "'" + v
    return v


# ------------------------------------------------------------------ carriers
def carrier_profiles(con, only_active: bool = True) -> list[CarrierProfile]:
    rows = con.execute("SELECT * FROM carriers" + (" WHERE active=1" if only_active else "") + " ORDER BY code").fetchall()
    out = []
    for r in rows:
        d = dict(r); d.pop("id"); d["region_adjust"] = json.loads(d["region_adjust"] or "{}"); d["active"] = bool(d["active"])
        out.append(CarrierProfile(**d))
    return out


def adapters(con) -> dict[str, CarrierAdapter]:
    return build_adapters(carrier_profiles(con))


def load_today(con) -> dict[str, int]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {r["carrier_code"]: r["n"] for r in con.execute(
        "SELECT carrier_code, COUNT(*) n FROM shipments WHERE booked_at LIKE ? AND deleted_at IS NULL AND status!='Canceled' AND carrier_code IS NOT NULL GROUP BY carrier_code", (day + "%",))}


def capacity_exclusions(con) -> list[str]:
    load = load_today(con)
    return [p.code for p in carrier_profiles(con) if load.get(p.code, 0) >= p.daily_capacity]


def carriers_with_stats(con) -> list[dict]:
    load = load_today(con); out = []
    for p in carrier_profiles(con, only_active=False):
        d = p.to_dict(); d["load_today"] = load.get(p.code, 0); d["healthy"] = HEALTH.is_up(p.code)
        d["coverage_states"] = None if p.coverage == "*" else p.coverage.split(","); out.append(d)
    return out


# ------------------------------------------------------------------ shipments
def _parse_deadline(s: Optional[str]) -> Optional[str]:
    if s in (None, ""): return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("deadline must be an ISO date/datetime (YYYY-MM-DD)")
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return d.isoformat(timespec="seconds")


def row_to_shipment(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["origin"] = d.pop("origin_label"); d["destination"] = d.pop("dest_label")
    return d


def create_shipment(con, user: Optional[str], *, order_id: Optional[str], origin: str, destination: str, weight_kg: float, dims_cm=None, declared_value: float = 0.0,
                    deadline: Optional[str] = None, origin_zip: Optional[str] = None, dest_zip: Optional[str] = None, seller_id: Optional[str] = None, notify_email: Optional[str] = None) -> dict:
    o = geo.resolve(origin, zip=origin_zip); d = geo.resolve(destination, zip=dest_zip)
    vol = dims_cm[0] * dims_cm[1] * dims_cm[2] if dims_cm and all(dims_cm) else None
    ts = now(); oid = order_id or f"ORD-{int(datetime.now().timestamp() * 1000)}"
    try:
        cur = con.execute("""INSERT INTO shipments(order_id,seller_id,origin_label,origin_state,origin_zip,origin_lat,origin_lng,dest_label,dest_state,dest_zip,dest_lat,dest_lng,
              weight_kg,vol_cm3,declared_value,deadline,ref_ts,status,created_at,updated_at,source,notify_email) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'manual',?)""",
                          (sanitize_cell(oid), seller_id, o.label, o.state, o.zip, o.lat, o.lng, d.label, d.state, d.zip, d.lat, d.lng, weight_kg, vol, declared_value, _parse_deadline(deadline), ts, "Pending", ts, ts, notify_email))
    except sqlite3.IntegrityError:
        raise Conflict("Order ID already exists")
    audit(con, user, "create", "shipment", cur.lastrowid, {"order_id": oid})
    return get_shipment(con, cur.lastrowid)


def get_shipment(con, sid: int, include_deleted: bool = False) -> dict:
    r = con.execute(f"SELECT {SHIP_COLS} FROM shipments WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL"), (sid,)).fetchone()
    if not r: raise NotFound("Shipment not found")
    return row_to_shipment(r)


def list_shipments(con, *, q: str = "", status: Optional[str] = None, carrier: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict:
    where, args = ["deleted_at IS NULL"], []
    if q:
        where.append("(order_id LIKE ? OR origin_label LIKE ? OR dest_label LIKE ? OR tracking_code LIKE ?)"); args += [f"%{q}%"] * 4
    if status: where.append("status=?"); args.append(status)
    if carrier: where.append("carrier_code=?"); args.append(carrier)
    w = " AND ".join(where)
    total = con.execute(f"SELECT COUNT(*) FROM shipments WHERE {w}", args).fetchone()[0]
    rows = con.execute(f"SELECT {SHIP_COLS} FROM shipments WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    return {"items": [row_to_shipment(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def change_status(con, user, sid: int, new: Status, *, source: str = "api") -> dict:
    s = get_shipment(con, sid); cur = Status(s["status"])
    check_transition(cur, new)
    if new == Status.BOOKED and not s["carrier_code"]:
        raise InvalidTransition("assign a carrier first (POST /api/shipments/{id}/assign)")
    if new == Status.IN_TRANSIT and not s["carrier_code"]:
        raise InvalidTransition("cannot ship without an assigned carrier")
    ts = now(); sets, args = ["status=?", "updated_at=?"], [new.value, ts]
    if new == Status.PENDING:   # un-assign
        sets += ["carrier_code=NULL", "price=NULL", "eta_p50=NULL", "eta_p90=NULL", "late_risk=NULL", "tracking_code=NULL", "booked_at=NULL"]
    if new == Status.DELIVERED: sets.append("delivered_at=?"); args.append(ts)
    if new == Status.CANCELED and s["carrier_code"] and s["tracking_code"]:
        ad = adapters(con).get(s["carrier_code"])
        if ad:
            try: ad.cancel(s["tracking_code"])
            except Exception: pass
    con.execute(f"UPDATE shipments SET {','.join(sets)} WHERE id=?", (*args, sid))
    con.execute("INSERT INTO tracking_events(shipment_id,event,description,ts,source) VALUES(?,?,?,?,?)", (sid, new.value.lower().replace(" ", "_"), f"Status {cur.value} -> {new.value}", ts, source))
    audit(con, user, "status", "shipment", sid, {"from": cur.value, "to": new.value})
    out = get_shipment(con, sid); notify.enqueue(con, out, new.value)
    return out


def soft_delete(con, user, sid: int) -> None:
    get_shipment(con, sid)
    con.execute("UPDATE shipments SET deleted_at=?, updated_at=? WHERE id=?", (now(), now(), sid))
    audit(con, user, "delete", "shipment", sid, {"soft": True})


def restore(con, user, sid: int) -> dict:
    get_shipment(con, sid, include_deleted=True)
    con.execute("UPDATE shipments SET deleted_at=NULL, updated_at=? WHERE id=?", (now(), sid))
    audit(con, user, "restore", "shipment", sid)
    return get_shipment(con, sid)


def _deadline_days(s: dict) -> Optional[float]:
    if not s.get("deadline"): return None
    try:
        dl = datetime.fromisoformat(s["deadline"]); ref = datetime.fromisoformat(s["ref_ts"])
        if dl.tzinfo is None: dl = dl.replace(tzinfo=timezone.utc)
        if ref.tzinfo is None: ref = ref.replace(tzinfo=timezone.utc)
        return max((dl - ref).total_seconds() / 86400, 0.0)
    except ValueError:
        return None


def quote_input_for(con, sid: int) -> tuple[dict, QuoteInput]:
    r = con.execute("SELECT * FROM shipments WHERE id=? AND deleted_at IS NULL", (sid,)).fetchone()
    if not r: raise NotFound("Shipment not found")
    s = dict(r)
    o = Place(s["origin_lat"], s["origin_lng"], s["origin_state"], s["origin_label"], s["origin_zip"], "zip" if s["origin_zip"] else "city")
    d = Place(s["dest_lat"], s["dest_lng"], s["dest_state"], s["dest_label"], s["dest_zip"], "zip" if s["dest_zip"] else "city")
    dims = None
    if s["vol_cm3"]:
        c = s["vol_cm3"] ** (1 / 3); dims = (c, c, c)
    h = reference.handling_days(s["seller_id"]) if s["seller_id"] else None
    try: month = datetime.fromisoformat(s["ref_ts"]).month
    except ValueError: month = None
    return s, QuoteInput(o, d, s["weight_kg"], dims, s["declared_value"], h, ship_month=month)


def quote_shipment(con, sid: int, *, objective="balanced", custom_weights=None, deadline_mode="p90", ignore_capacity=False, late_penalty=None) -> dict:
    s, qi = quote_input_for(con, sid)
    excl = [] if ignore_capacity else capacity_exclusions(con)
    res = optimize(adapters(con), qi, objective=objective, custom_weights=custom_weights, deadline_days=_deadline_days(s), deadline_mode=deadline_mode, exclude=excl, late_penalty=late_penalty)
    for e in res["excluded"]:
        if e["reason"] == "excluded by request": e["reason"] = "daily capacity reached"
    res["shipment_id"] = sid
    return res


def assign(con, user, sid: int, carrier_code: str, *, objective="balanced", deadline_mode="p90", allow_late: bool = False) -> dict:
    """Book a carrier. The price/ETA are RE-QUOTED server-side (never trusted from the client)."""
    s, qi = quote_input_for(con, sid)
    if Status(s["status"]) != Status.PENDING:
        raise InvalidTransition(f"only Pending shipments can be assigned (current: {s['status']})")
    ads = adapters(con)
    if carrier_code not in ads: raise NotFound(f"carrier {carrier_code} not found or inactive")
    if carrier_code in capacity_exclusions(con): raise Conflict(f"{carrier_code} reached its daily capacity; see /reroute-suggestion")
    res = optimize(ads, qi, objective=objective, deadline_days=_deadline_days(s), deadline_mode=deadline_mode)
    q = next((x for x in res["quotes"] if x["carrier_code"] == carrier_code), None)
    if q is None:
        reason = next((e["reason"] for e in res["excluded"] if e["carrier_code"] == carrier_code), "carrier cannot serve this route")
        raise ValueError(f"{carrier_code} cannot serve this shipment: {reason}")
    if not q["eligible"] and not allow_late:
        raise ValueError(f"{carrier_code} cannot meet the deadline ({q['reason']})")
    booking = ads[carrier_code].book(s["order_id"], Quote(**{k: q[k] for k in Quote.__dataclass_fields__ if k in q}))
    ts = now()
    con.execute("UPDATE shipments SET carrier_code=?,price=?,currency=?,eta_p50=?,eta_p90=?,late_risk=?,tracking_code=?,booked_at=?,status='Booked',updated_at=? WHERE id=?",
                (carrier_code, q["price"], q["currency"], q["eta_p50_days"], q["eta_p90_days"], q["late_risk"], booking["tracking_code"], ts, ts, sid))
    con.execute("INSERT INTO tracking_events(shipment_id,event,description,ts,source) VALUES(?,?,?,?,?)", (sid, "booked", f"Booked with {q['carrier']}", ts, "system"))
    audit(con, user, "assign", "shipment", sid, {"carrier": carrier_code, "price": q["price"], "eta_p90": q["eta_p90_days"], **({"deadline_at_risk": True} if not q["eligible"] else {})})
    out = get_shipment(con, sid); notify.enqueue(con, out, "booked")
    return out


def reroute_suggestion(con, sid: int, objective="balanced") -> dict:
    s = get_shipment(con, sid)
    if not s["carrier_code"]:
        raise ValueError("shipment has no carrier yet; use /quotes")
    cur = s["carrier_code"]; why = []
    if not HEALTH.is_up(cur): why.append("carrier health check failing (circuit open / forced down)")
    if cur in capacity_exclusions(con): why.append("carrier at daily capacity")
    res = quote_shipment(con, sid, objective=objective, ignore_capacity=True)
    alts = [q for q in res["quotes"] if q["eligible"] and q["carrier_code"] != cur and q["carrier_code"] not in capacity_exclusions(con)]
    return {"shipment_id": sid, "current_carrier": cur, "reroute_recommended": bool(why), "reasons": why, "alternatives": alts[:3],
            "note": "Re-assigning requires moving the shipment back to Pending (un-assign) and assigning the alternative." if why else "Current carrier is healthy."}


def apply_reroute(con, user, sid: int, new_carrier: str) -> dict:
    s = get_shipment(con, sid)
    if Status(s["status"]) != Status.BOOKED: raise InvalidTransition("only Booked shipments can be re-routed")
    change_status(con, user, sid, Status.PENDING, source="reroute")
    out = assign(con, user, sid, new_carrier)
    audit(con, user, "reroute", "shipment", sid, {"from": s["carrier_code"], "to": new_carrier})
    return out


def apply_tracking_event(con, tracking_code: str, event: str, ts: Optional[str], description: Optional[str]) -> dict:
    r = con.execute("SELECT id,status FROM shipments WHERE tracking_code=? AND deleted_at IS NULL", (tracking_code,)).fetchone()
    if not r: raise NotFound("unknown tracking code")
    sid, cur = r["id"], Status(r["status"])
    con.execute("INSERT INTO tracking_events(shipment_id,event,description,ts,source) VALUES(?,?,?,?,'webhook')", (sid, event, description, ts or now()))
    target = {"picked_up": Status.IN_TRANSIT, "in_transit": Status.IN_TRANSIT, "out_for_delivery": Status.IN_TRANSIT, "delivered": Status.DELIVERED}.get(event)
    applied = None
    if target and target != cur:
        try:
            if cur == Status.BOOKED and target == Status.DELIVERED:
                change_status(con, "webhook", sid, Status.IN_TRANSIT, source="webhook")
            change_status(con, "webhook", sid, target, source="webhook"); applied = target.value
        except InvalidTransition:
            applied = None
    return {"shipment_id": sid, "event": event, "status_applied": applied}


def tracking(con, sid: int) -> dict:
    s = get_shipment(con, sid)
    ev = [dict(r) for r in con.execute("SELECT event,description,ts,source FROM tracking_events WHERE shipment_id=? ORDER BY id", (sid,))]
    out = {"shipment_id": sid, "status": s["status"], "tracking_code": s["tracking_code"], "events": ev, "projected": []}
    if s["carrier_code"] and s["tracking_code"] and s["booked_at"]:
        ad = adapters(con).get(s["carrier_code"])
        if ad:
            try: out["projected"] = ad.track(s["tracking_code"], datetime.fromisoformat(s["booked_at"]), s["eta_p50"] or 5.0)
            except Exception: pass
    return out


# ------------------------------------------------------------------ KPIs & analytics (computed over the WHOLE dataset)
def kpis(con) -> dict:
    st = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM shipments WHERE deleted_at IS NULL GROUP BY status")}
    tot = sum(st.values())
    b = con.execute("SELECT COUNT(*) n, AVG(price) p, SUM(price) s FROM shipments WHERE deleted_at IS NULL AND price IS NOT NULL").fetchone()
    h = con.execute("SELECT AVG(hist_freight) f, AVG(hist_transit_days) t FROM shipments WHERE deleted_at IS NULL AND hist_freight IS NOT NULL").fetchone()
    return {"total": tot, "by_status": st, "pending": st.get("Pending", 0), "booked": st.get("Booked", 0), "in_transit": st.get("In Transit", 0), "delivered": st.get("Delivered", 0), "canceled": st.get("Canceled", 0),
            "assigned": b["n"], "avg_booked_price": round(b["p"], 2) if b["p"] is not None else None, "total_booked_cost": round(b["s"], 2) if b["s"] is not None else 0.0,
            "avg_historical_freight": round(h["f"], 2) if h["f"] is not None else None, "avg_historical_transit_days": round(h["t"], 1) if h["t"] is not None else None, "currency": "BRL"}


def analytics(con) -> dict:
    per_carrier = []
    for r in con.execute("""SELECT carrier_code, COUNT(*) n, AVG(price) avg_price, AVG(eta_p50) avg_eta, AVG(late_risk) avg_late_risk,
              SUM(CASE WHEN status='Delivered' THEN 1 ELSE 0 END) delivered,
              SUM(CASE WHEN status='Delivered' AND deadline IS NOT NULL AND delivered_at > deadline THEN 1 ELSE 0 END) breaches,
              SUM(CASE WHEN status='Delivered' AND deadline IS NOT NULL THEN 1 ELSE 0 END) with_deadline
              FROM shipments WHERE deleted_at IS NULL AND carrier_code IS NOT NULL GROUP BY carrier_code ORDER BY n DESC"""):
        d = dict(r); d["on_time_rate"] = round(1 - d["breaches"] / d["with_deadline"], 4) if d["with_deadline"] else None
        d["avg_price"] = round(d["avg_price"], 2) if d["avg_price"] is not None else None; d["avg_eta"] = round(d["avg_eta"], 1) if d["avg_eta"] is not None else None
        d["avg_late_risk"] = round(d["avg_late_risk"], 4) if d["avg_late_risk"] is not None else None
        per_carrier.append(d)
    routes = [dict(r) for r in con.execute("""SELECT origin_state o, dest_state d, COUNT(*) n, AVG(COALESCE(price,hist_freight)) avg_cost, AVG(hist_transit_days) avg_transit_days
              FROM shipments WHERE deleted_at IS NULL GROUP BY origin_state,dest_state HAVING n>=20 ORDER BY n DESC LIMIT 25""")]
    for r in routes:
        r["avg_cost"] = round(r["avg_cost"], 2) if r["avg_cost"] is not None else None; r["avg_transit_days"] = round(r["avg_transit_days"], 1) if r["avg_transit_days"] is not None else None
    hist = con.execute("""SELECT COUNT(*) n, SUM(CASE WHEN hist_delivered_ts > deadline THEN 1 ELSE 0 END) late FROM shipments
              WHERE deleted_at IS NULL AND hist_delivered_ts IS NOT NULL AND deadline IS NOT NULL""").fetchone()
    return {"carriers": per_carrier, "routes": routes, "historical": {"delivered_with_deadline": hist["n"], "sla_breaches": hist["late"] or 0,
            "sla_breach_rate": round((hist["late"] or 0) / hist["n"], 4) if hist["n"] else None, "note": "Olist historical orders: actual delivery date vs the estimated date promised to the customer."}}


# ------------------------------------------------------------------ public tracking (no auth: minimal, non-sensitive fields only)
STAGES = ["Pending", "Booked", "In Transit", "Delivered"]


def _city_only(label: str) -> str:
    return label.split(" (")[0]


def public_tracking(con, code: str) -> dict:
    r = con.execute("SELECT * FROM shipments WHERE tracking_code=? AND deleted_at IS NULL", (code,)).fetchone()
    if not r: raise NotFound("tracking code not found")
    s = dict(r)
    ev = [dict(e) for e in con.execute("SELECT event,description,ts FROM tracking_events WHERE shipment_id=? ORDER BY id", (s["id"],))]
    eta = None
    if s["booked_at"] and s["eta_p50"]:
        b = datetime.fromisoformat(s["booked_at"])
        eta = {"expected": (b + timedelta(days=s["eta_p50"])).isoformat(), "latest_likely": (b + timedelta(days=s["eta_p90"] or s["eta_p50"])).isoformat()}
    stage = STAGES.index(s["status"]) if s["status"] in STAGES else (0 if s["status"] == "Pending" else -1)
    carrier = next((p.name for p in carrier_profiles(con, only_active=False) if p.code == s["carrier_code"]), None)
    rnd = lambda v: round(v, 1)
    return {"tracking_code": code, "status": s["status"], "stage": stage, "stages": STAGES, "carrier": carrier, "eta": eta, "promised_by": s["deadline"],
            "origin": {"label": _city_only(s["origin_label"]), "state": s["origin_state"], "lat": rnd(s["origin_lat"]), "lng": rnd(s["origin_lng"])},
            "destination": {"label": _city_only(s["dest_label"]), "state": s["dest_state"], "lat": rnd(s["dest_lat"]), "lng": rnd(s["dest_lng"])},
            "events": ev, "simulated": True}


# ------------------------------------------------------------------ extra analytics: timeline / flows / network scorecard
def timeline(con) -> dict:
    rows = con.execute("""SELECT substr(ref_ts,1,7) m, COUNT(*) n, AVG(hist_freight) freight, AVG(hist_transit_days) transit,
        SUM(CASE WHEN hist_delivered_ts IS NOT NULL AND deadline IS NOT NULL AND hist_delivered_ts > deadline THEN 1 ELSE 0 END) late,
        SUM(CASE WHEN hist_delivered_ts IS NOT NULL AND deadline IS NOT NULL THEN 1 ELSE 0 END) judged
        FROM shipments WHERE deleted_at IS NULL GROUP BY m ORDER BY m""").fetchall()
    out = [{"month": r["m"], "shipments": r["n"], "avg_freight": round(r["freight"], 2) if r["freight"] is not None else None, "avg_transit_days": round(r["transit"], 1) if r["transit"] is not None else None,
            "late_rate": round(r["late"] / r["judged"], 4) if r["judged"] else None, "late": r["late"], "judged": r["judged"]} for r in rows]
    return {"months": out}


def flows(con, top: int = 40) -> dict:
    rows = con.execute("SELECT origin_state o, dest_state d, COUNT(*) n FROM shipments WHERE deleted_at IS NULL GROUP BY origin_state,dest_state ORDER BY n DESC LIMIT ?", (top,)).fetchall()
    states = geo._tables()[2]
    return {"flows": [{"o": r["o"], "d": r["d"], "n": r["n"], "o_ll": states.get(r["o"]), "d_ll": states.get(r["d"])} for r in rows if r["o"] in states and r["d"] in states],
            "states": {k: {"lat": v[0], "lng": v[1]} for k, v in states.items()}}


_SC_CACHE: dict = {}


def scorecard(con, ttl: int = 600) -> dict:
    """Network scorecard: quote a deterministic sample of REAL routes with every active carrier; coverage / price / ETA / risk / win-share per carrier."""
    import time
    profiles = carrier_profiles(con)
    key = tuple((p.code, p.price_mult, p.speed_mult, p.reliability, p.coverage, p.active) for p in profiles)
    hit = _SC_CACHE.get("v")
    if hit and hit[0] == key and time.time() - hit[1] < ttl: return hit[2]
    rows = con.execute("SELECT id FROM shipments WHERE deleted_at IS NULL AND id % 250 = 0 LIMIT 500").fetchall() or con.execute("SELECT id FROM shipments WHERE deleted_at IS NULL LIMIT 200").fetchall()
    ads = build_adapters(profiles); agg = {p.code: {"n": 0, "covered": 0, "price": 0.0, "p50": 0.0, "p90": 0.0, "risk": 0.0, "wins": 0, "cheapest": 0, "fastest": 0} for p in profiles}
    total = 0
    for r in rows:
        _, qi = quote_input_for(con, r["id"]); res = optimize(ads, qi, objective="balanced")
        if not res["quotes"]: continue
        total += 1
        for a in agg.values(): a["n"] += 1
        cheap = min(res["quotes"], key=lambda x: x["price"])["carrier_code"]; fast = min(res["quotes"], key=lambda x: x["eta_p90_days"])["carrier_code"]
        for q in res["quotes"]:
            a = agg[q["carrier_code"]]; a["covered"] += 1; a["price"] += q["price"]; a["p50"] += q["eta_p50_days"]; a["p90"] += q["eta_p90_days"]; a["risk"] += q["late_risk"]
        agg[res["recommendation"]["carrier_code"]]["wins"] += 1; agg[cheap]["cheapest"] += 1; agg[fast]["fastest"] += 1
    out = []
    for p in profiles:
        a = agg[p.code]; c = max(a["covered"], 1)
        out.append({"carrier_code": p.code, "carrier": p.name, "service": p.service, "coverage_pct": round(a["covered"] / max(total, 1) * 100, 1), "avg_price": round(a["price"] / c, 2),
                    "avg_eta_p50": round(a["p50"] / c, 1), "avg_eta_p90": round(a["p90"] / c, 1), "avg_late_risk": round(a["risk"] / c, 4),
                    "win_share_pct": round(a["wins"] / max(total, 1) * 100, 1), "cheapest_share_pct": round(a["cheapest"] / max(total, 1) * 100, 1), "fastest_share_pct": round(a["fastest"] / max(total, 1) * 100, 1)})
    res = {"routes_sampled": total, "carriers": out, "note": "Simulated carriers quoted on a deterministic sample of real routes from the shipments table (balanced objective, no deadline)."}
    _SC_CACHE["v"] = (key, time.time(), res)
    return res
