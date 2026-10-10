"""HTTP routes. Thin layer: validation + auth + calls into services/engine."""
from __future__ import annotations
import csv, hashlib, hmac, io, logging, shutil, tempfile, threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt as pyjwt
from .config import settings
from .db import db, now, audit
from .domain import InvalidTransition, Status
from . import schemas as S
from .engine import geo, reference
from .engine.carriers import HEALTH, QuoteInput
from .engine.geo import Place
from .engine.quoting import Weights, optimize
from .engine.sourcing import rank_sources
from .engine import optimizer as batch_opt
from .security import create_token, decode_token, hash_password, verify_password
from .services import core, importer, notify

log = logging.getLogger("olist.api")
router = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


# ------------------------------------------------------------------ auth dependencies
def current_user(cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> dict:
    if not settings.auth_enabled:
        return {"username": "anonymous", "role": "admin"}
    if cred is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        p = decode_token(cred.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return {"username": p["sub"], "role": p["role"]}


def require(min_role: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if ROLE_RANK.get(user["role"], 0) < ROLE_RANK[min_role]:
            raise HTTPException(403, f"requires role '{min_role}' or higher")
        return user
    return dep


viewer, operator, admin = require("viewer"), require("operator"), require("admin")


def _wrap(fn, *a, **k):
    """Map domain errors to HTTP codes."""
    try:
        return fn(*a, **k)
    except core.NotFound as e:
        raise HTTPException(404, str(e))
    except core.Conflict as e:
        raise HTTPException(409, str(e))
    except InvalidTransition as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


def _place(text: str, zip_: Optional[str] = None) -> Place:
    try:
        return geo.resolve(text, zip=zip_)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _bool_weights(obj, w: Optional[S.Weights3]):
    return w.tup() if (obj.value == "custom" and w) else None


# ------------------------------------------------------------------ public
@router.get("/health")
def health():
    with db() as c:
        c.execute("SELECT 1")
    return {"status": "ok", "database": "sqlite(wal)", "version": settings.version, "timestamp": now()}


@router.post("/auth/login")
def login(body: S.LoginIn):
    with db() as c:
        r = c.execute("SELECT username,password_hash,role FROM users WHERE username=?", (body.username,)).fetchone()
        if not r or not verify_password(body.password, r["password_hash"]):
            audit(c, body.username, "login_failed", "user", body.username)
            raise HTTPException(401, "Wrong username or password")
        audit(c, r["username"], "login", "user", r["username"])
    return {"access_token": create_token(r["username"], r["role"]), "token_type": "bearer", "role": r["role"], "username": r["username"]}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/users", status_code=201)
def create_user(body: S.UserIn, user: dict = Depends(admin)):
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (body.username,)).fetchone():
            raise HTTPException(409, "username already exists")
        c.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)", (body.username, hash_password(body.password), body.role.value, now()))
        audit(c, user["username"], "create", "user", body.username, {"role": body.role.value})
    return {"username": body.username, "role": body.role.value}


# ------------------------------------------------------------------ geo / reference
@router.get("/geo/resolve")
def geo_resolve(q: str = Query(min_length=2, max_length=120), user: dict = Depends(viewer)):
    return _place(q).as_dict()


@router.get("/geo/states")
def geo_states(user: dict = Depends(viewer)):
    return {"states": geo.known_states()}


@router.get("/products/multi-seller")
def multi_seller_products(limit: int = Query(12, ge=1, le=50), user: dict = Depends(viewer)):
    ps = sorted(reference.product_sellers().values(), key=lambda p: -len(p["sellers"]))[:limit]
    return [{"product_id": p["product_id"], "category": p["category"], "weight_kg": p["weight_kg"], "sellers": len(p["sellers"])} for p in ps]


# ------------------------------------------------------------------ carriers
@router.get("/carriers")
def carriers(user: dict = Depends(viewer)):
    with db() as c:
        return core.carriers_with_stats(c)


@router.get("/carriers/health")
def carriers_health(user: dict = Depends(viewer)):
    with db() as c:
        ads = core.adapters(c)
    out = []
    for code, ad in ads.items():
        h = ad.health()
        out.append({"carrier_code": code, "carrier": ad.profile.name, "ok": h.ok, "latency_ms": h.latency_ms, "detail": h.detail, "kind": ad.profile.kind})
    return out


@router.patch("/carriers/{code}")
def patch_carrier(code: str, body: S.CarrierPatch, user: dict = Depends(admin)):
    upd = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump(exclude_none=True).items()}
    if not upd:
        raise HTTPException(400, "No fields to update")
    with db() as c:
        if not c.execute("SELECT 1 FROM carriers WHERE code=?", (code,)).fetchone():
            raise HTTPException(404, "Carrier not found")
        c.execute("UPDATE carriers SET " + ",".join(f"{k}=?" for k in upd) + " WHERE code=?", (*upd.values(), code))
        audit(c, user["username"], "update", "carrier", code, upd)
        return next(x for x in core.carriers_with_stats(c) if x["code"] == code)


@router.post("/carriers/{code}/simulate-outage")
def simulate_outage(code: str, down: bool = True, user: dict = Depends(admin)):
    """Demo / chaos switch: marks a carrier as down so the fallback + re-routing logic can be shown."""
    if down: HEALTH.forced_down.add(code)
    else: HEALTH.forced_down.discard(code); HEALTH.record_success(code)
    with db() as c:
        audit(c, user["username"], "outage" if down else "recover", "carrier", code)
    return {"carrier_code": code, "down": down}


# ------------------------------------------------------------------ quotes / sourcing
@router.post("/quotes")
def quotes(body: S.QuoteIn, user: dict = Depends(viewer)):
    o, d = _place(body.origin, body.origin_zip), _place(body.destination, body.dest_zip)
    dims = (body.length_cm, body.height_cm, body.width_cm) if all((body.length_cm, body.height_cm, body.width_cm)) else None
    with db() as c:
        ads = core.adapters(c); excl = list(body.exclude) + core.capacity_exclusions(c)
    month = body.ship_month or datetime.now().month
    res = optimize(ads, QuoteInput(o, d, body.weight_kg, dims, body.declared_value, ship_month=month), objective=body.objective.value, custom_weights=_bool_weights(body.objective, body.weights),
                   deadline_days=body.deadline_days, deadline_mode=body.deadline_mode, exclude=excl, late_penalty=body.late_penalty)
    res["ship_month"] = month; res["season_applied"] = bool(settings.seasonality and str(month) in reference.models().get("season", {}))
    for e in res["excluded"]:
        if e["reason"] == "excluded by request" and e["carrier_code"] not in body.exclude: e["reason"] = "daily capacity reached"
    return res


@router.post("/sourcing/rank")
def sourcing(body: S.SourcingIn, user: dict = Depends(viewer)):
    dest = _place(body.destination, body.dest_zip)
    with db() as c:
        ads = core.adapters(c)
    try:
        return rank_sources(ads, body.product_id, dest, quantity=body.quantity, objective=body.objective.value if body.objective.value != "custom" else "balanced", deadline_days=body.deadline_days)
    except KeyError:
        raise HTTPException(404, "product not found among multi-seller products (see /api/products/multi-seller)")


# ------------------------------------------------------------------ shipments
@router.get("/shipments")
def list_shipments(q: str = Query("", max_length=80), status: Optional[Status] = None, carrier: Optional[str] = None,
                   limit: int = Query(25, ge=1, le=settings.max_page_size), offset: int = Query(0, ge=0), user: dict = Depends(viewer)):
    with db() as c:
        return core.list_shipments(c, q=q, status=status.value if status else None, carrier=carrier, limit=limit, offset=offset)


@router.get("/shipments/export.csv")
def export_csv(status: Optional[Status] = None, limit: int = Query(5000, ge=1, le=50000), user: dict = Depends(operator)):
    with db() as c:
        items = core.list_shipments(c, status=status.value if status else None, limit=limit)["items"]
    cols = ["order_id", "origin", "destination", "weight_kg", "status", "carrier_code", "price", "currency", "eta_p50", "eta_p90", "tracking_code", "deadline"]
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(cols)
    for it in items: w.writerow([core.sanitize_cell(it.get(k)) for k in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=shipments.csv"})


@router.post("/shipments", status_code=201)
def create_shipment(body: S.ShipmentIn, user: dict = Depends(operator)):
    with db() as c:
        return _wrap(core.create_shipment, c, user["username"], order_id=body.order_id, origin=body.origin, destination=body.destination, weight_kg=body.weight_kg, dims_cm=body.dims(),
                     declared_value=body.declared_value, deadline=body.deadline, origin_zip=body.origin_zip, dest_zip=body.dest_zip, seller_id=body.seller_id, notify_email=body.notify_email)


@router.get("/shipments/{sid}")
def get_shipment(sid: int, user: dict = Depends(viewer)):
    with db() as c:
        return _wrap(core.get_shipment, c, sid)


@router.patch("/shipments/{sid}")
def update_status(sid: int, body: S.StatusIn, user: dict = Depends(operator)):
    with db() as c:
        return _wrap(core.change_status, c, user["username"], sid, body.status)


@router.delete("/shipments/{sid}")
def delete_shipment(sid: int, user: dict = Depends(admin)):
    with db() as c:
        _wrap(core.soft_delete, c, user["username"], sid)
    return {"deleted": True, "soft": True}


@router.post("/shipments/{sid}/restore")
def restore_shipment(sid: int, user: dict = Depends(admin)):
    with db() as c:
        return _wrap(core.restore, c, user["username"], sid)


@router.post("/shipments/{sid}/quotes")
def shipment_quotes(sid: int, body: S.ShipmentQuoteIn = S.ShipmentQuoteIn(), user: dict = Depends(viewer)):
    with db() as c:
        return _wrap(core.quote_shipment, c, sid, objective=body.objective.value, custom_weights=_bool_weights(body.objective, body.weights), deadline_mode=body.deadline_mode, late_penalty=body.late_penalty)


@router.post("/shipments/{sid}/assign")
def assign(sid: int, body: S.AssignIn, user: dict = Depends(operator)):
    with db() as c:
        return _wrap(core.assign, c, user["username"], sid, body.carrier_code.upper(), objective=body.objective.value if body.objective.value != "custom" else "balanced")


@router.get("/shipments/{sid}/reroute-suggestion")
def reroute_suggestion(sid: int, user: dict = Depends(viewer)):
    with db() as c:
        return _wrap(core.reroute_suggestion, c, sid)


@router.post("/shipments/{sid}/reroute")
def reroute(sid: int, body: S.RerouteIn, user: dict = Depends(operator)):
    with db() as c:
        return _wrap(core.apply_reroute, c, user["username"], sid, body.carrier_code.upper())


@router.get("/shipments/{sid}/tracking")
def shipment_tracking(sid: int, user: dict = Depends(viewer)):
    with db() as c:
        return _wrap(core.tracking, c, sid)


@router.get("/shipments/{sid}/audit")
def shipment_audit(sid: int, user: dict = Depends(operator)):
    with db() as c:
        rows = c.execute("SELECT ts,username,action,detail FROM audit_log WHERE entity='shipment' AND entity_id=? ORDER BY id DESC LIMIT 100", (str(sid),)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ tracking webhook (HMAC-signed)
@router.post("/webhooks/tracking/{carrier_code}")
async def tracking_webhook(carrier_code: str, request: Request, x_signature: str = Header(default="")):
    if not settings.webhook_ready():
        raise HTTPException(503, "tracking webhook disabled: set WEBHOOK_SECRET (>=16 chars, not a placeholder)")
    raw = await request.body()
    expect = hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, x_signature):
        raise HTTPException(401, "invalid signature")
    try:
        body = S.TrackingWebhook.model_validate_json(raw)
    except Exception as e:
        raise HTTPException(422, f"invalid payload: {e}")
    with db() as c:
        return _wrap(core.apply_tracking_event, c, body.tracking_code, body.event, body.ts, body.description)


# ------------------------------------------------------------------ batch optimisation / consolidation / what-if
def _batch_orders(c, ids, limit):
    if ids:
        rows = [core.quote_input_for(c, i) for i in ids]
    else:
        pend = c.execute("SELECT id FROM shipments WHERE status='Pending' AND deleted_at IS NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        rows = [core.quote_input_for(c, r["id"]) for r in pend]
    return [{"id": s["id"], "q": qi, "deadline_days": core._deadline_days(s), "order_id": s["order_id"]} for s, qi in rows]


@router.post("/optimize/batch")
def optimize_batch(body: S.BatchIn, user: dict = Depends(operator)):
    with db() as c:
        orders = _batch_orders(c, body.shipment_ids, body.limit)
        if not orders: raise HTTPException(422, "no Pending shipments to optimise")
        ads = core.adapters(c); profiles = {p.code: p for p in core.carrier_profiles(c)}
        load = core.load_today(c)
        cap = {code: max(p.daily_capacity - load.get(code, 0), 0) for code, p in profiles.items()}
        if body.capacity: cap.update(body.capacity)
        opts, diag = batch_opt.build_options_with_diag(ads, orders, body.deadline_mode, body.relax_deadline, body.late_penalty)
        w = Weights.from_objective(body.objective.value, body.weights.tup() if body.weights else None)
        plan = batch_opt.solve_assignment(orders, opts, cap, w, diag=diag)
        plan["capacity_used"] = cap; plan["objective"] = body.objective.value; plan["deadline_mode"] = body.deadline_mode; plan["relax_deadline"] = body.relax_deadline
        if body.apply:
            applied, failed = 0, []
            for a in plan["assignments"]:
                try:
                    core.assign(c, user["username"], a["order_id"], a["code"], deadline_mode=body.deadline_mode, allow_late=bool(a.get("relaxed"))); applied += 1
                except Exception as e:
                    failed.append({"shipment_id": a["order_id"], "error": str(e)})
            plan["applied"] = applied; plan["apply_failures"] = failed[:20]
        order_ref = {o["id"]: o["order_id"] for o in orders}
        for a in plan["assignments"]: a["shipment_id"] = a.pop("order_id"); a["order_id"] = order_ref[a["shipment_id"]]
        detail = plan.pop("unassigned_detail", [])
        plan["unassigned"] = [{"shipment_id": d["id"], "order_id": order_ref[d["id"]], **{k: v for k, v in d.items() if k != "id"}} for d in detail]
        summary: dict[str, int] = {}
        for d in detail: summary[d["reason_code"]] = summary.get(d["reason_code"], 0) + 1
        plan["unassigned_summary"] = summary
        plan["relaxed_assignments"] = sum(1 for a in plan["assignments"] if a.get("relaxed"))
        return plan


@router.post("/optimize/consolidation")
def consolidation(body: S.ConsolidateIn, user: dict = Depends(viewer)):
    with db() as c:
        orders = _batch_orders(c, body.shipment_ids, body.limit)
        ads = core.adapters(c)
    ships = [{"id": o["id"], "origin": o["q"].origin, "destination": o["q"].destination, "weight_kg": o["q"].weight_kg} for o in orders]
    return batch_opt.consolidate(ships, ads)


@router.post("/simulate/what-if")
def what_if(body: S.WhatIfIn, user: dict = Depends(viewer)):
    """Sweep cost-vs-speed weights over pending shipments: total cost / avg ETA / expected lateness for each weight mix."""
    with db() as c:
        orders = _batch_orders(c, None, body.limit); ads = core.adapters(c)
    if not orders: raise HTTPException(422, "no Pending shipments to simulate")
    opts = batch_opt.build_options(ads, orders)
    def run(w: Weights):
        batch_opt._scores(opts, w); cost = eta = late = 0.0; n = 0
        for row in opts:
            if not row: continue
            b = min(row, key=lambda o: o["score"]); cost += b["price"]; eta += b["eta_p50"]; late += b["risk"]; n += 1
        return {"orders": n, "total_cost": round(cost, 2), "avg_eta_p50_days": round(eta / n, 2) if n else None, "expected_late_orders": round(late, 1)}
    sweep = []
    for i in range(0, 11):
        wc = i / 10; sweep.append({"w_cost": wc, "w_speed": round(1 - wc, 1), **run(Weights(wc, 1 - wc, 0.0))})
    out = {"sweep": sweep, "orders_considered": len(orders), "currency": "BRL"}
    if body.weights:
        wt = body.weights; s = (wt.cost + wt.speed + wt.risk) or 1
        out["custom"] = {"weights": wt.model_dump(), **run(Weights(wt.cost / s, wt.speed / s, wt.risk / s))}
    return out


# ------------------------------------------------------------------ KPIs / analytics / backtest
@router.get("/kpis")
def kpis(user: dict = Depends(viewer)):
    with db() as c:
        return core.kpis(c)


@router.get("/analytics")
def analytics(user: dict = Depends(viewer)):
    with db() as c:
        return core.analytics(c)


@router.get("/backtest")
def backtest(user: dict = Depends(viewer)):
    import json
    p = Path(__file__).parent / "data" / "backtest.json"
    if not p.exists(): raise HTTPException(404, "backtest not generated yet: run scripts/backtest.py")
    return json.loads(p.read_text())


@router.get("/calibration")
def calibration(user: dict = Depends(viewer)):
    return reference.calibration()


# ------------------------------------------------------------------ import (admin, size-limited, background job)
JOBS: dict[str, dict] = {}


def _run_import(job_id: str, directory: Path, limit: Optional[int], username: str, cleanup: Optional[Path]):
    try:
        JOBS[job_id].update(status="running")
        res = importer.import_directory(directory, limit=limit, user=username, progress=lambda i, n: JOBS[job_id].update(progress=round(i / max(n, 1), 3)))
        JOBS[job_id].update(status="done", result=res, progress=1.0)
    except Exception as e:
        log.exception("import failed")
        JOBS[job_id].update(status="failed", error=str(e))
    finally:
        if cleanup: shutil.rmtree(cleanup, ignore_errors=True)


@router.post("/import/olist-server", status_code=202)
def import_server_dir(limit: Optional[int] = Query(None, ge=1), user: dict = Depends(admin)):
    """Import from the server-side OLIST_DATA_DIR (default ./olist_data)."""
    d = settings.olist_data_dir
    if not d.exists(): raise HTTPException(404, f"directory {d} not found; put the Olist CSVs there or upload the archive")
    jid = hashlib.sha1(now().encode()).hexdigest()[:10]; JOBS[jid] = {"status": "queued", "progress": 0.0}
    threading.Thread(target=_run_import, args=(jid, d, limit, user["username"], None), daemon=True).start()
    return {"job_id": jid}


@router.post("/import/olist-archive", status_code=202)
async def import_archive(file: UploadFile = File(...), limit: Optional[int] = Query(None, ge=1), user: dict = Depends(admin)):
    """Upload the Olist archive.zip (size-limited, streamed to disk, extracted with a zip-bomb cap)."""
    if not (file.filename or "").lower().endswith(".zip"): raise HTTPException(400, "Upload the Olist archive as a .zip file")
    cap = settings.max_upload_mb * 1024 * 1024; tmp = Path(tempfile.mkdtemp(prefix="olist_")); zp = tmp / "up.zip"; size = 0
    with open(zp, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > cap:
                shutil.rmtree(tmp, ignore_errors=True); raise HTTPException(413, f"file too large (limit {settings.max_upload_mb} MB)")
            out.write(chunk)
    try:
        d = importer.extract_zip(zp, tmp / "csv", max_bytes=cap * 6)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True); raise HTTPException(422, f"bad archive: {e}")
    jid = hashlib.sha1((now() + str(size)).encode()).hexdigest()[:10]; JOBS[jid] = {"status": "queued", "progress": 0.0}
    threading.Thread(target=_run_import, args=(jid, d, limit, user["username"], tmp), daemon=True).start()
    return {"job_id": jid}


@router.get("/import/jobs/{job_id}")
def import_job(job_id: str, user: dict = Depends(admin)):
    if job_id not in JOBS: raise HTTPException(404, "unknown job")
    return JOBS[job_id]


# ------------------------------------------------------------------ v3: meta, autocomplete, public tracking, scorecard, timeline, flows, notifications
@router.get("/meta")
def meta(user: dict = Depends(viewer)):
    m = reference.models()
    return {"version": settings.version, "currency": "BRL", "default_late_penalty_brl": m.get("default_late_penalty_brl", 40.0), "seasonality": settings.seasonality,
            "season": m.get("season", {}), "price_model": (m.get("price") or {}).get("fit_new_model"), "price_model_previous": (m.get("price") or {}).get("fit_previous_zone_card"),
            "notification_channels": notify.channels()}


@router.get("/geo/suggest")
def geo_suggest(q: str = Query(min_length=2, max_length=60), user: dict = Depends(viewer)):
    return geo.suggest(q)


@router.get("/public/track/{code}")
def public_track(code: str):
    """Customer-facing tracking: no login, minimal fields (no price, no order id, no exact addresses)."""
    if not (6 <= len(code) <= 24) or not code.replace("-", "").isalnum():
        raise HTTPException(404, "tracking code not found")
    with db() as c:
        return _wrap(core.public_tracking, c, code)


@router.get("/analytics/scorecard")
def scorecard(user: dict = Depends(viewer)):
    with db() as c:
        return core.scorecard(c)


@router.get("/analytics/timeline")
def analytics_timeline(user: dict = Depends(viewer)):
    with db() as c:
        return core.timeline(c)


@router.get("/analytics/flows")
def analytics_flows(user: dict = Depends(viewer)):
    with db() as c:
        return core.flows(c)


@router.get("/notifications")
def notifications(limit: int = Query(50, ge=1, le=200), user: dict = Depends(operator)):
    with db() as c:
        rows = c.execute("SELECT id,shipment_id,channel,recipient,event,subject,status,error,created_at,sent_at FROM notifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"channels": notify.channels(), "items": [dict(r) for r in rows]}
