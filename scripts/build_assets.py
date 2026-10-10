"""Build app/static/brazil_dots.json: a dot-matrix silhouette of Brazil from the Olist zip-prefix centroids (no map tiles, works offline)."""
import json
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
z = pd.read_csv(ROOT / "app/data/geo_zip.csv"); z = z[(z.lat.between(-34, 6)) & (z.lng.between(-74, -34))]
CELL = 0.55
z["gy"] = np.floor(z.lat / CELL).astype(int); z["gx"] = np.floor(z.lng / CELL).astype(int)
g = z.groupby(["gy", "gx"]).size().reset_index(name="n")
mx = np.log1p(g.n.max())
dots = [[round((r.gy + .5) * CELL, 3), round((r.gx + .5) * CELL, 3), round(float(np.log1p(r.n) / mx), 2)] for r in g.itertuples()]
st = pd.read_csv(ROOT / "app/data/geo_state.csv")
out = {"cell": CELL, "bounds": {"lat": [-34, 6], "lng": [-74, -34]}, "dots": dots, "states": {r.state: [round(r.lat, 3), round(r.lng, 3)] for r in st.itertuples()}}
(ROOT / "app/static/brazil_dots.json").write_text(json.dumps(out, separators=(",", ":")))
print(len(dots), "dots", (ROOT / "app/static/brazil_dots.json").stat().st_size, "bytes")
