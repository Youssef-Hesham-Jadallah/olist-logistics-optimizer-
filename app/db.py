"""SQLite access (WAL, foreign keys, indexes) + versioned migrations. init_db() is called from the app lifespan, never at import."""
from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional
from .config import settings
from .engine.carriers import DEFAULT_PROFILES

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')), created_at TEXT NOT NULL);
    CREATE TABLE carriers (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, name TEXT UNIQUE NOT NULL, service TEXT NOT NULL,
        price_mult REAL NOT NULL, speed_mult REAL NOT NULL, reliability REAL NOT NULL, coverage TEXT NOT NULL DEFAULT '*',
        fuel_pct REAL NOT NULL, insurance_pct REAL NOT NULL, volumetric_divisor INTEGER NOT NULL, max_weight_kg REAL NOT NULL,
        min_fee REAL NOT NULL, handling_days REAL NOT NULL, daily_capacity INTEGER NOT NULL, region_adjust TEXT NOT NULL DEFAULT '{}',
        kind TEXT NOT NULL DEFAULT 'simulated', active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE shipments (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE NOT NULL, parent_order_id TEXT, seller_id TEXT,
        origin_label TEXT NOT NULL, origin_state TEXT NOT NULL, origin_zip TEXT, origin_lat REAL NOT NULL, origin_lng REAL NOT NULL,
        dest_label TEXT NOT NULL, dest_state TEXT NOT NULL, dest_zip TEXT, dest_lat REAL NOT NULL, dest_lng REAL NOT NULL,
        weight_kg REAL NOT NULL CHECK(weight_kg > 0), vol_cm3 REAL, declared_value REAL NOT NULL DEFAULT 0,
        deadline TEXT, ref_ts TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Booked','In Transit','Delivered','Canceled')),
        carrier_code TEXT REFERENCES carriers(code), price REAL, currency TEXT NOT NULL DEFAULT 'BRL',
        eta_p50 REAL, eta_p90 REAL, late_risk REAL, tracking_code TEXT, booked_at TEXT, delivered_at TEXT,
        hist_freight REAL, hist_transit_days REAL, hist_delivered_ts TEXT, source TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
    CREATE INDEX ix_ship_status ON shipments(status) WHERE deleted_at IS NULL;
    CREATE INDEX ix_ship_carrier ON shipments(carrier_code);
    CREATE INDEX ix_ship_created ON shipments(created_at);
    CREATE INDEX ix_ship_parent ON shipments(parent_order_id);
    CREATE INDEX ix_ship_route ON shipments(origin_state, dest_state);
    CREATE INDEX ix_ship_tracking ON shipments(tracking_code);
    CREATE TABLE tracking_events (id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES shipments(id), event TEXT NOT NULL,
        description TEXT, ts TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'webhook');
    CREATE INDEX ix_track_ship ON tracking_events(shipment_id);
    CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, username TEXT, action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT, detail TEXT);
    CREATE INDEX ix_audit_entity ON audit_log(entity, entity_id);
    """),
    (2, """
    ALTER TABLE shipments ADD COLUMN notify_email TEXT;
    CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES shipments(id), channel TEXT NOT NULL, recipient TEXT,
        event TEXT NOT NULL, subject TEXT, body TEXT, status TEXT NOT NULL DEFAULT 'queued', error TEXT, created_at TEXT NOT NULL, sent_at TEXT);
    CREATE INDEX ix_notif_status ON notifications(status);
    """),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(settings.db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def migrate(con: sqlite3.Connection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    cur = con.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
    for v, sql in MIGRATIONS:
        if v > cur:
            con.executescript(sql)
            con.execute("INSERT INTO schema_version(version, applied_at) VALUES(?,?)", (v, now()))
            con.commit()
            cur = v
    return cur


def seed_carriers(con: sqlite3.Connection) -> None:
    if con.execute("SELECT COUNT(*) FROM carriers").fetchone()[0]:
        return
    for p in DEFAULT_PROFILES:
        con.execute("""INSERT INTO carriers(code,name,service,price_mult,speed_mult,reliability,coverage,fuel_pct,insurance_pct,volumetric_divisor,max_weight_kg,min_fee,handling_days,daily_capacity,region_adjust,kind,active)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (p.code, p.name, p.service, p.price_mult, p.speed_mult, p.reliability, p.coverage, p.fuel_pct, p.insurance_pct, p.volumetric_divisor, p.max_weight_kg, p.min_fee,
                     p.handling_days, p.daily_capacity, json.dumps(p.region_adjust), p.kind))


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        migrate(con)
        seed_carriers(con)


def audit(con: sqlite3.Connection, username: Optional[str], action: str, entity: str, entity_id, detail: Optional[dict] = None) -> None:
    con.execute("INSERT INTO audit_log(ts,username,action,entity,entity_id,detail) VALUES(?,?,?,?,?,?)",
                (now(), username, action, entity, str(entity_id) if entity_id is not None else None, json.dumps(detail or {}, default=str)))
