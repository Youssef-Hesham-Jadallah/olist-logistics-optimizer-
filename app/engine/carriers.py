"""Carrier profiles + the unified adapter interface: quote() / book() / track() / cancel() / health().

All adapters shipped here are SIMULATED (calibrated from Olist data). `LiveCarrierAdapter` shows where a real
API (Correios, Jadlog, Melhor Envio aggregator...) plugs in without touching the optimizer.
"""
from __future__ import annotations
import hashlib, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from ..config import settings
from .geo import Place, STATE_REGION, distance_km, zone_for
from . import reference, pricing

ALL = "*"


@dataclass
class CarrierProfile:
    code: str
    name: str
    service: str
    price_mult: float = 1.0
    speed_mult: float = 1.0          # multiplies calibrated transit days
    reliability: float = 1.0         # multiplies historical late rate (<1 = more reliable)
    coverage: str = ALL              # "*" or comma list of UF codes (BOTH origin and destination must be covered)
    fuel_pct: float = 0.06
    insurance_pct: float = 0.005     # of declared value
    volumetric_divisor: int = 6000   # cm3 per kg
    max_weight_kg: float = 70.0
    min_fee: float = 8.0
    handling_days: float = 0.3       # carrier pickup/handoff delay added on top of seller handling
    daily_capacity: int = 2000
    region_adjust: dict = field(default_factory=dict)   # {"N": [price_mult, speed_mult]} applied by destination region
    kind: str = "simulated"
    active: bool = True

    def covers(self, state: str) -> bool:
        return self.coverage == ALL or state in {s.strip() for s in self.coverage.split(",")}

    def to_dict(self):
        return asdict(self)


DEFAULT_PROFILES = [
    CarrierProfile("ALPHA", "Carrier Alpha", "Standard national", 1.00, 1.00, 1.00, ALL, 0.06, 0.005, 6000, 70, 8, 0.3, 2500),
    CarrierProfile("BETA", "Carrier Beta", "Economy national", 0.86, 1.30, 1.25, ALL, 0.05, 0.004, 5000, 50, 7, 0.5, 4000),
    CarrierProfile("GAMMA", "Carrier Gamma", "Express (major corridors)", 1.45, 0.65, 0.60, "SP,RJ,MG,ES,PR,SC,RS,DF,GO,MS,MT,BA,PE,CE", 0.08, 0.008, 6000, 30, 12, 0.2, 1200),
    CarrierProfile("DELTA", "Carrier Delta", "South/Southeast regional", 0.78, 0.95, 0.90, "SP,RJ,MG,ES,PR,SC,RS", 0.05, 0.004, 6000, 30, 6, 0.3, 3000),
    CarrierProfile("EPSILON", "Carrier Epsilon", "North/Northeast specialist", 1.05, 1.05, 1.00, "AM,PA,AC,RO,RR,AP,TO,BA,SE,AL,PE,PB,RN,CE,PI,MA,SP,RJ,MG,DF,GO,ES", 0.07, 0.006, 6000, 60, 9, 0.4, 1500,
                    {"N": [0.88, 0.80], "NE": [0.90, 0.85]}),
]


@dataclass
class QuoteInput:
    origin: Place
    destination: Place
    weight_kg: float
    dims_cm: Optional[tuple] = None          # (L, H, W) of the whole parcel
    declared_value: float = 0.0
    seller_handling_days: Optional[float] = None
    ship_month: Optional[int] = None         # 1-12; drives peak-season ETA factors
    n_items: int = 1


@dataclass
class Quote:
    carrier_code: str
    carrier: str
    service: str
    price: float
    currency: str
    eta_p50_days: float
    eta_p90_days: float
    late_risk: float
    zone: int
    distance_km: float
    chargeable_kg: float
    breakdown: dict
    source: str = "SIMULATED_CALIBRATED"

    def to_dict(self):
        return asdict(self)


@dataclass
class HealthStatus:
    ok: bool
    latency_ms: float = 0.0
    detail: str = "ok"


class CarrierUnavailable(Exception):
    pass


class NotCovered(Exception):
    pass


class CarrierAdapter(ABC):
    def __init__(self, profile: CarrierProfile):
        self.profile = profile

    @property
    def code(self): return self.profile.code

    @abstractmethod
    def quote(self, q: QuoteInput) -> Quote: ...
    @abstractmethod
    def book(self, shipment_ref: str, quote: Quote) -> dict: ...
    @abstractmethod
    def track(self, tracking_code: str, booked_at: Optional[datetime] = None, eta_p50_days: float = 5.0) -> list[dict]: ...
    @abstractmethod
    def cancel(self, tracking_code: str) -> bool: ...
    @abstractmethod
    def health(self) -> HealthStatus: ...


# ---- health tracking / circuit breaker (shared by all adapters) -------------------------------------------
class HealthRegistry:
    def __init__(self):
        self.forced_down: set[str] = set()
        self.failures: dict[str, int] = {}
        self.open_until: dict[str, float] = {}

    def record_failure(self, code: str):
        self.failures[code] = self.failures.get(code, 0) + 1
        if self.failures[code] >= 3:
            self.open_until[code] = time.time() + 60

    def record_success(self, code: str):
        self.failures[code] = 0
        self.open_until.pop(code, None)

    def is_up(self, code: str) -> bool:
        return code not in self.forced_down and self.open_until.get(code, 0) < time.time()


HEALTH = HealthRegistry()


class SimulatedAdapter(CarrierAdapter):
    """Calibrated simulation: price from the fitted zone rate card, ETA from real state-pair statistics."""

    def quote(self, q: QuoteInput) -> Quote:
        p = self.profile
        if not HEALTH.is_up(p.code):
            raise CarrierUnavailable(f"{p.name} is currently unavailable")
        if not p.covers(q.origin.state):
            raise NotCovered(f"origin {q.origin.state} not covered by {p.name}")
        if not p.covers(q.destination.state):
            raise NotCovered(f"destination {q.destination.state} not covered by {p.name}")
        if q.weight_kg > p.max_weight_kg:
            raise NotCovered(f"weight {q.weight_kg:.1f}kg exceeds {p.name} limit of {p.max_weight_kg:.0f}kg")
        cal = reference.calibration()
        km = distance_km(q.origin, q.destination)
        zone = zone_for(km)
        vol_kg = 0.0
        if q.dims_cm and all(q.dims_cm):
            vol_kg = (q.dims_cm[0] * q.dims_cm[1] * q.dims_cm[2]) / p.volumetric_divisor
        chargeable = max(q.weight_kg, vol_kg)
        region = STATE_REGION.get(q.destination.state, "")
        pm, sm = p.region_adjust.get(region, [1.0, 1.0])
        vol_cm3 = (q.dims_cm[0] * q.dims_cm[1] * q.dims_cm[2]) if (q.dims_cm and all(q.dims_cm)) else None
        mdl = reference.models().get("price")
        if mdl:      # log-linear model fitted on pre-2018 Olist freight (distance, weight, volume, value, destination)
            market = pricing.predict(mdl, km, chargeable, vol_cm3, q.declared_value, q.n_items, q.origin.state == q.destination.state, q.destination.state)
        else:        # fallback: zone rate card
            card = cal["rate_cards"][str(zone)]; market = card["base"] + card["per_kg"] * chargeable
        core_price = market * p.price_mult * pm
        fuel = core_price * p.fuel_pct / (1 + p.fuel_pct)      # fuel surcharge is INCLUDED in the market-calibrated price (shown as a share)
        insurance = q.declared_value * p.insurance_pct
        price = max(core_price + insurance, p.min_fee)
        rs = reference.route_stats().get((q.origin.state, q.destination.state))
        z = cal["zone_eta"][str(zone)]
        if rs:
            w = rs["n"] / (rs["n"] + 30.0)   # blend route stats with zone stats when the pair is sparse
            t50 = w * rs["p50"] + (1 - w) * z["p50"]; t90 = w * rs["p90"] + (1 - w) * z["p90"]; late = w * rs["late"] + (1 - w) * z["late"]
        else:
            t50, t90, late = z["p50"], z["p90"], z["late"]
        t50 *= p.speed_mult * sm; t90 *= p.speed_mult * sm
        season = reference.models().get("season", {}).get(str(q.ship_month)) if (q.ship_month and settings.seasonality) else None
        if season: t50 *= season["p50"]; t90 *= season["p90"]
        t90 = max(t90, t50 * 1.15)
        handling = (q.seller_handling_days if q.seller_handling_days is not None else cal["global_handling_days"]) + p.handling_days
        e50 = handling + t50
        e90 = handling * 1.5 + t90            # additive percentiles are intentionally conservative
        return Quote(p.code, p.name, p.service, round(price, 2), cal["currency"], round(e50, 1), round(e90, 1), round(min(late * p.reliability, 0.99), 4), zone, round(km, 1), round(chargeable, 2),
                     {"linehaul": round(core_price - fuel, 2), "fuel": round(fuel, 2), "insurance": round(insurance, 2), "handling_days": round(handling, 2), "season_factor_p90": season["p90"] if season else 1.0})

    def book(self, shipment_ref: str, quote: Quote) -> dict:
        if not HEALTH.is_up(self.code):
            raise CarrierUnavailable(f"{self.profile.name} is currently unavailable")
        digest = hashlib.sha1(f"{self.code}:{shipment_ref}".encode()).hexdigest()[:10].upper()
        return {"tracking_code": f"{self.code[:3]}{digest}", "carrier": self.profile.name, "price": quote.price, "currency": quote.currency, "simulated": True}

    def track(self, tracking_code: str, booked_at: Optional[datetime] = None, eta_p50_days: float = 5.0) -> list[dict]:
        t0 = booked_at or datetime.now(timezone.utc)
        steps = [("booked", 0.0, "Label created"), ("picked_up", 0.15, "Collected from seller"), ("in_transit", 0.35, "In transit between hubs"),
                 ("out_for_delivery", 0.9, "Out for delivery"), ("delivered", 1.0, "Delivered")]
        return [{"event": e, "description": d, "ts": (t0 + timedelta(days=eta_p50_days * f)).isoformat(), "simulated": True} for e, f, d in steps]

    def cancel(self, tracking_code: str) -> bool:
        return True

    def health(self) -> HealthStatus:
        up = HEALTH.is_up(self.code)
        return HealthStatus(up, 12.0 if up else 0.0, "ok (simulated)" if up else "forced down / circuit open")


class LiveCarrierAdapter(CarrierAdapter):
    """Template for a REAL carrier API (Correios, Jadlog, Melhor Envio aggregator). Not implemented on purpose:
    fill quote()/book()/track()/cancel() with HTTP calls, map the response to `Quote`, and register it in
    `build_adapters` with kind='live'. The optimizer needs no change."""

    def _nc(self):
        raise CarrierUnavailable(f"{self.profile.name}: live API not configured (set credentials and implement the HTTP calls)")

    def quote(self, q): self._nc()
    def book(self, shipment_ref, quote): self._nc()
    def track(self, tracking_code, booked_at=None, eta_p50_days=5.0): self._nc()
    def cancel(self, tracking_code): self._nc()
    def health(self): return HealthStatus(False, 0.0, "live API not configured")


def build_adapters(profiles: list[CarrierProfile]) -> dict[str, CarrierAdapter]:
    out = {}
    for p in profiles:
        if p.active:
            out[p.code] = LiveCarrierAdapter(p) if p.kind == "live" else SimulatedAdapter(p)
    return out
