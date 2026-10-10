"""FastAPI application: lifespan init, CORS, rate limiting, request logging, error handling, metrics, static UI."""
from __future__ import annotations
import logging, time, uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from .config import ROOT, settings
from .db import init_db
from .security import bootstrap_users
from . import api
from .services import notify

logging.basicConfig(level=settings.log_level, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("olist")
METRICS = {"requests": defaultdict(int), "latency_sum": 0.0, "errors_5xx": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    issues = settings.secret_issues()
    if issues and settings.app_env == "production":
        raise RuntimeError("Refusing to start in production with weak configuration: " + "; ".join(issues))
    for i in issues: log.warning("CONFIG: %s", i)
    init_db(); bootstrap_users(); notify.start_worker()
    log.info("started %s v%s (auth=%s)", settings.app_name, settings.version, settings.auth_enabled)
    yield
    notify.stop_worker()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Signature"], max_age=600)

_hits: dict[str, deque] = defaultdict(deque)


def _limited(key: str, limit: int) -> bool:
    t = time.time(); q = _hits[key]
    while q and q[0] < t - 60: q.popleft()
    if len(q) >= limit: return True
    q.append(t); return False


@app.middleware("http")
async def guard(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]; t0 = time.time(); ip = request.client.host if request.client else "?"
    path = request.url.path
    if path.startswith("/api"):
        kind = 'login' if path == '/api/auth/login' else 'public' if path.startswith('/api/public') else 'api'
        lim = settings.login_rate_limit_per_min if kind == 'login' else 60 if kind == 'public' else settings.rate_limit_per_min
        if _limited(f"{ip}:{kind}", lim):
            return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > (settings.max_upload_mb + 1) * 1024 * 1024 and "/import/" not in path:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("unhandled error rid=%s %s %s", rid, request.method, path)
        METRICS["errors_5xx"] += 1
        return JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    dt = (time.time() - t0) * 1000
    METRICS["requests"][f"{request.method} {resp.status_code}"] += 1; METRICS["latency_sum"] += dt
    if resp.status_code >= 500: METRICS["errors_5xx"] += 1
    resp.headers["X-Request-ID"] = rid
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if path.startswith("/api"): log.info("%s %s %s %.0fms rid=%s", request.method, path, resp.status_code, dt, rid)
    return resp


app.include_router(api.router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    lines = ["# TYPE olist_http_requests_total counter"] + [f'olist_http_requests_total{{key="{k}"}} {v}' for k, v in METRICS["requests"].items()]
    lines += [f"olist_http_latency_ms_sum {METRICS['latency_sum']:.1f}", f"olist_http_errors_5xx_total {METRICS['errors_5xx']}"]
    return "\n".join(lines) + "\n"


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/track", include_in_schema=False)
def track_page():
    return FileResponse(ROOT / "static" / "track.html")
