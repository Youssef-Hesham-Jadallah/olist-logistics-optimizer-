"""Central configuration (environment variables, with safe defaults)."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"


def _load_dotenv() -> None:
    """Minimal .env loader (so `cp .env.example .env` works with plain uvicorn). Real environment variables win."""
    f = Path(os.getenv("DOTENV_PATH", ROOT.parent / ".env"))
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
PLACEHOLDER_PREFIX = "change-me"


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


class Settings:
    app_name = "Olist Logistics Optimizer"
    version = "3.0.0"
    currency = "BRL"
    db_path = Path(os.getenv("DB_PATH", ROOT.parent / "logistics.db"))
    olist_data_dir = Path(os.getenv("OLIST_DATA_DIR", ROOT.parent / "olist_data"))
    jwt_secret = os.getenv("JWT_SECRET", "")  # generated at first start when empty (see security.py)
    jwt_ttl_minutes = int(os.getenv("JWT_TTL_MINUTES", "480"))
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "")  # generated + logged once when empty
    seed_demo_users = _bool("SEED_DEMO_USERS", False)
    app_env = os.getenv("APP_ENV", "development").lower()          # "production" => refuse to start with weak/missing secrets
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")                # empty/placeholder => tracking webhook is DISABLED (503)
    cors_origins = [o for o in os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if o]
    rate_limit_per_min = int(os.getenv("RATE_LIMIT_PER_MIN", "240"))
    login_rate_limit_per_min = int(os.getenv("LOGIN_RATE_LIMIT_PER_MIN", "10"))
    max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "60"))
    max_page_size = 200
    log_level = os.getenv("LOG_LEVEL", "INFO")
    seasonality = _bool("SEASONALITY", True)   # Nov/Dec peak-season ETA factors (from the training year; unvalidated on hold-out)
    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    smtp_host = os.getenv("NOTIFY_SMTP_HOST", ""); smtp_port = int(os.getenv("NOTIFY_SMTP_PORT", "587")); smtp_user = os.getenv("NOTIFY_SMTP_USER", "")
    smtp_password = os.getenv("NOTIFY_SMTP_PASSWORD", ""); smtp_from = os.getenv("NOTIFY_FROM", "logistics@example.com"); smtp_tls = _bool("NOTIFY_SMTP_TLS", True)
    notify_webhook_url = os.getenv("NOTIFY_WEBHOOK_URL", "")   # generic JSON webhook (Zapier / n8n / a WhatsApp provider)
    auth_enabled = _bool("AUTH_ENABLED", True)


    @staticmethod
    def _weak(v: str, min_len: int) -> bool:
        return (not v) or v.lower().startswith(PLACEHOLDER_PREFIX) or len(v) < min_len

    def webhook_ready(self) -> bool:
        """An empty HMAC key would let anyone forge tracking events, so the endpoint stays off until a real secret is set."""
        return not self._weak(self.webhook_secret, 16)

    def secret_issues(self) -> list[str]:
        out = []
        if self._weak(self.jwt_secret, 32): out.append("JWT_SECRET is missing/placeholder/shorter than 32 chars (a random one is generated per start; tokens die on restart)")
        if self._weak(self.admin_password, 8): out.append("ADMIN_PASSWORD is missing/placeholder (a random admin password is generated and logged once)")
        if not self.webhook_ready(): out.append("WEBHOOK_SECRET is missing/placeholder/shorter than 16 chars (tracking webhook disabled)")
        if self.seed_demo_users: out.append("SEED_DEMO_USERS=true creates users with well-known passwords")
        return out


settings = Settings()
