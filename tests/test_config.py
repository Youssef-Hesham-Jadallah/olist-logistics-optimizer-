from app.config import Settings


def test_secret_issues_flag_placeholders_and_short_values():
    s = Settings(); s.jwt_secret = "change-me-to-a-long-random-string"; s.admin_password = ""; s.webhook_secret = "short"; s.seed_demo_users = True
    assert len(s.secret_issues()) == 4 and not s.webhook_ready()
    s.jwt_secret = "j" * 40; s.admin_password = "A-real-password-1"; s.webhook_secret = "w" * 24; s.seed_demo_users = False
    assert s.secret_issues() == [] and s.webhook_ready()


def test_production_refuses_weak_config(monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    monkeypatch.setattr(settings, "app_env", "production"); monkeypatch.setattr(settings, "jwt_secret", "")
    with pytest.raises(RuntimeError, match="weak configuration"):
        with TestClient(app):
            pass


def test_placeholder_secrets_are_never_used(monkeypatch):
    from app import security
    from app.config import settings
    monkeypatch.setattr(settings, "jwt_secret", "change-me-to-a-long-random-string"); monkeypatch.setattr(security, "_secret", None)
    assert security.jwt_secret() != "change-me-to-a-long-random-string" and len(security.jwt_secret()) >= 32
