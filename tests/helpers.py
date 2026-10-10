import os, tempfile
from contextlib import contextmanager
from pathlib import Path
from app.config import settings
from app import db as dbm


@contextmanager
def temp_db():
    old = settings.db_path
    d = tempfile.mkdtemp()
    settings.db_path = Path(d) / "t.db"
    try:
        dbm.init_db()
        yield
    finally:
        settings.db_path = old
