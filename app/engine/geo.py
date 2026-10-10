"""Geo layer: zip-prefix / city / state resolution and haversine distance."""
from __future__ import annotations
import csv, math, re, unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional
from ..config import DATA_DIR

ZONE_EDGES = [100, 400, 1000, 2000]  # km; zones 1..5
STATE_REGION = {
    "SP": "SE", "RJ": "SE", "MG": "SE", "ES": "SE", "PR": "S", "SC": "S", "RS": "S",
    "DF": "CO", "GO": "CO", "MT": "CO", "MS": "CO",
    "BA": "NE", "SE": "NE", "AL": "NE", "PE": "NE", "PB": "NE", "RN": "NE", "CE": "NE", "PI": "NE", "MA": "NE",
    "AM": "N", "PA": "N", "AC": "N", "RO": "N", "RR": "N", "AP": "N", "TO": "N",
}


def norm(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


@dataclass(frozen=True)
class Place:
    lat: float
    lng: float
    state: str
    label: str
    zip: Optional[str] = None
    precision: str = "zip"  # zip | city | state | coords

    def as_dict(self):
        return {"lat": round(self.lat, 5), "lng": round(self.lng, 5), "state": self.state, "label": self.label, "zip": self.zip, "precision": self.precision}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def zone_for(km: float) -> int:
    for i, e in enumerate(ZONE_EDGES, 1):
        if km < e:
            return i
    return 5


@lru_cache(maxsize=1)
def _tables():
    zips, cities, states = {}, {}, {}
    with open(DATA_DIR / "geo_zip.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            zips[r["zip"]] = (float(r["lat"]), float(r["lng"]), r["state"], r["city"])
    with open(DATA_DIR / "geo_city.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cities[(r["city"], r["state"])] = (float(r["lat"]), float(r["lng"]))
    with open(DATA_DIR / "geo_state.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            states[r["state"]] = (float(r["lat"]), float(r["lng"]))
    by_city: dict[str, list] = {}
    for (c, s), (la, ln) in cities.items():
        by_city.setdefault(c, []).append((s, la, ln))
    return zips, cities, states, by_city


def known_states() -> list[str]:
    return sorted(_tables()[2])


def resolve(text: Optional[str] = None, *, lat: Optional[float] = None, lng: Optional[float] = None, zip: Optional[str] = None, state_hint: Optional[str] = None) -> Place:
    """Resolve a location from coordinates, zip prefix, 'city, UF', city name or UF. Raises ValueError if unknown."""
    zips, cities, states, by_city = _tables()
    if lat is not None and lng is not None:
        st = _nearest_state(lat, lng)
        return Place(lat, lng, st, f"{lat:.3f},{lng:.3f}", None, "coords")
    t = (zip or text or "").strip()
    if not t:
        raise ValueError("location is empty")
    digits = re.sub(r"\D", "", t)
    if digits and len(digits) >= 5 and len(digits) == len(re.sub(r"[\s-]", "", t)):
        z = digits[:5]
        if z in zips:
            la, ln, st, city = zips[z]
            return Place(la, ln, st, f"{city.title()}/{st} ({z})", z, "zip")
        # nearest known zip prefix numerically (prefixes are geographically ordered)
        zi = int(z)
        best = min(zips, key=lambda k: abs(int(k) - zi))
        la, ln, st, city = zips[best]
        return Place(la, ln, st, f"{city.title()}/{st} (~{best})", best, "zip")
    parts = [p.strip() for p in re.split(r"[,/\-]", t) if p.strip()]
    st = None
    if len(parts) >= 2 and parts[-1].upper() in states:
        st = parts[-1].upper(); city = norm(" ".join(parts[:-1]))
    elif len(parts) == 1 and parts[0].upper() in states and len(parts[0]) == 2:
        s = parts[0].upper(); la, ln = states[s]
        return Place(la, ln, s, f"{s} (state centre)", None, "state")
    else:
        city = norm(parts[0] if parts else t)
    if st and (city, st) in cities:
        la, ln = cities[(city, st)]
        return Place(la, ln, st, f"{city.title()}/{st}", None, "city")
    if city in by_city:
        opts = by_city[city]
        pick = next((o for o in opts if state_hint and o[0] == state_hint), None) or max(opts, key=lambda o: 0)
        return Place(pick[1], pick[2], pick[0], f"{city.title()}/{pick[0]}", None, "city")
    alias = {"sao paulo": "SP", "rio de janeiro": "RJ", "brasilia": "DF", "salvador": "BA", "manaus": "AM", "fortaleza": "CE", "recife": "PE", "curitiba": "PR", "porto alegre": "RS", "belo horizonte": "MG"}
    if city in alias and (city, alias[city]) in cities:
        la, ln = cities[(city, alias[city])]
        return Place(la, ln, alias[city], f"{city.title()}/{alias[city]}", None, "city")
    raise ValueError(f"cannot resolve location '{t}' (use a zip prefix like 01310, 'City, UF' or a state code)")


def _nearest_state(lat: float, lng: float) -> str:
    states = _tables()[2]
    return min(states, key=lambda s: haversine_km(lat, lng, *states[s]))


def distance_km(a: Place, b: Place) -> float:
    d = haversine_km(a.lat, a.lng, b.lat, b.lng)
    # centroid-level precision: never claim 0 km between different cities/states
    return max(d, 3.0)


@lru_cache(maxsize=1)
def _city_pop() -> dict:
    out = {}
    with open(DATA_DIR / "geo_city.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[(r["city"], r["state"])] = int(float(r["n"])) if r.get("n") else 0
    return out


def suggest(q: str, limit: int = 8) -> list[dict]:
    """Typeahead over the local geo tables (no external API): city names (accent-insensitive, most-populated first) or zip prefixes."""
    q = (q or "").strip()
    if len(q) < 2: return []
    zips, cities, states, _ = _tables()
    digits = re.sub(r"\D", "", q)
    out = []
    if digits and len(digits) == len(q.replace("-", "").replace(" ", "")):
        for z in sorted(k for k in zips if k.startswith(digits[:5]))[:limit]:
            la, ln, st, city = zips[z]
            out.append({"label": f"{city.title()}/{st} - {z}", "value": z, "lat": la, "lng": ln, "kind": "zip"})
        return out
    n = norm(q.split(",")[0]); pop = _city_pop()
    st_hint = q.split(",")[1].strip().upper() if "," in q else None
    starts, contains = [], []
    for (c, s), (la, ln) in cities.items():
        if st_hint and s != st_hint: continue
        (starts if c.startswith(n) else contains if n in c else []).append((c, s, la, ln))
    pick = sorted(starts, key=lambda x: -pop.get((x[0], x[1]), 0)) + sorted(contains, key=lambda x: -pop.get((x[0], x[1]), 0))
    for c, s, la, ln in pick[:limit]:
        out.append({"label": f"{c.title()}, {s}", "value": f"{c.title()}, {s}", "lat": la, "lng": ln, "kind": "city"})
    if len(q) == 2 and q.upper() in states and not out:
        la, ln = states[q.upper()]; out.append({"label": f"{q.upper()} (state)", "value": q.upper(), "lat": la, "lng": ln, "kind": "state"})
    return out
