"""CLI import of the Olist CSVs into the app database (bulk insert, multi-seller orders split into sub-shipments).

Usage:  python scripts/import_olist.py --data ./olist_data [--limit 20000]
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import init_db
from app.services import importer


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    init_db()
    res = importer.import_directory(Path(a.data), limit=a.limit, progress=lambda i, n: print(f"\r{i}/{n}", end="", flush=True))
    print("\n", res)


if __name__ == "__main__":
    main()
