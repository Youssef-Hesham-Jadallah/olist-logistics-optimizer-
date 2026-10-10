"""Freight price model shared by training (scripts/train_models.py) and runtime.
log(freight) = X·beta with distance/weight/volume/value/same-state features and destination-state dummies;
price = exp(X·beta) * smearing. Pure numpy: no sklearn at runtime."""
from __future__ import annotations
import numpy as np

DEST_DUMMIES = ["AM", "PA", "AC", "RO", "RR", "AP", "TO", "BA", "PE", "CE", "MA", "PI", "RN", "PB", "AL", "SE", "RS", "SC", "PR", "MG", "RJ", "ES", "GO", "DF", "MT", "MS"]
BASE_FEATURES = ["one", "ld", "ld2", "lk", "lk2", "lkd", "lv", "lval", "ln", "same"]
FEATURE_NAMES = BASE_FEATURES + ["c_" + s for s in DEST_DUMMIES]


def feature_matrix(dist, chg_kg, vol_cm3, value, n_items, same_state, dest_state) -> np.ndarray:
    dist = np.maximum(np.asarray(dist, float), 3.0); chg = np.maximum(np.asarray(chg_kg, float), 0.05)
    vol = np.maximum(np.asarray(vol_cm3, float), 10.0); val = np.maximum(np.asarray(value, float), 0.0)
    n = np.maximum(np.asarray(n_items, float), 1.0); same = np.asarray(same_state, float); dest = np.asarray(dest_state)
    ld, lk = np.log(dist), np.log(chg)
    cols = [np.ones_like(ld), ld, ld ** 2, lk, lk ** 2, ld * lk, np.log(vol), np.log1p(val), np.log(n), same]
    cols += [(dest == s).astype(float) for s in DEST_DUMMIES]
    return np.column_stack(cols)


def predict(model: dict, dist: float, chg_kg: float, vol_cm3: float | None, value: float | None, n_items: int, same_state: bool, dest_state: str) -> float:
    v = value if (value and value > 0) else model["median_value"]
    vol = vol_cm3 if vol_cm3 else max(chg_kg * 6000.0 * 0.5, 10.0)     # unknown volume: assume half the volumetric allowance of the weight
    X = feature_matrix([dist], [chg_kg], [vol], [v], [n_items], [same_state], [dest_state])
    return float(np.exp(X @ np.asarray(model["coef"]))[0] * model["smear"])
