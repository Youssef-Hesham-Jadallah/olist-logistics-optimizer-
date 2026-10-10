"""v3 features: price model, expected-cost objective, season factors, notifications outbox, public tracking, autocomplete, scorecard."""
from app.engine import geo, reference, pricing
from app.engine.carriers import build_adapters, DEFAULT_PROFILES, QuoteInput
from app.engine.quoting import optimize, Weights
from app.services import core, notify
from app.db import db
from app.domain import Status
from .helpers import temp_db

ADS = build_adapters(DEFAULT_PROFILES)
P = geo.resolve


def test_price_model_is_monotonic_in_weight_and_distance():
    m = reference.models()["price"]
    near = pricing.predict(m, 100, 1.0, None, 100, 1, False, "SP"); far = pricing.predict(m, 2500, 1.0, None, 100, 1, False, "AM")
    heavy = pricing.predict(m, 100, 15.0, None, 100, 1, False, "SP")
    assert far > near and heavy > near and 5 < near < 60


def test_price_model_beats_previous_zone_card_on_holdout():
    m = reference.models()["price"]
    assert m["fit_new_model"]["holdout"]["mae_brl"] < m["fit_previous_zone_card"]["holdout"]["mae_brl"]
    assert m["fit_new_model"]["holdout"]["r2"] > m["fit_previous_zone_card"]["holdout"]["r2"]


def test_peak_season_lengthens_eta_only_in_peak_months():
    q = lambda mth: optimize(ADS, QuoteInput(P("Sao Paulo, SP"), P("Recife, PE"), 2, ship_month=mth), objective="cost")["recommendation"]
    assert q(12)["eta_p90_days"] > q(5)["eta_p90_days"] * 1.15 and q(5)["eta_p90_days"] == q(None)["eta_p90_days"]


def test_expected_cost_adds_late_penalty_and_changes_with_penalty():
    qi = QuoteInput(P("Sao Paulo, SP"), P("Manaus, AM"), 2.5, (30, 20, 15), 150)
    lo = optimize(ADS, qi, objective="expected_cost", deadline_days=36, late_penalty=0)
    hi = optimize(ADS, qi, objective="expected_cost", deadline_days=36, late_penalty=500)
    for r in lo["quotes"]: assert abs(r["expected_cost"] - r["price"]) < 0.011
    assert all(r["expected_cost"] > r["price"] for r in hi["quotes"] if r["late_risk"] > 0)
    assert lo["recommendation"]["expected_cost"] == min(r["expected_cost"] for r in lo["quotes"] if r["eligible"])
    assert Weights.from_objective("expected_cost").mode == "expected_cost"


def test_geo_suggest_orders_by_population_and_handles_zip():
    s = geo.suggest("sao pa")
    assert s[0]["value"] == "Sao Paulo, SP" and geo.resolve(s[0]["value"]).state == "SP"
    assert geo.suggest("0131")[0]["kind"] == "zip" and geo.suggest("x") == []


def test_notifications_outbox_and_sender():
    from app.config import settings
    with temp_db():
        with db() as c:
            s = core.create_shipment(c, "t", order_id="N1", origin="Campinas, SP", destination="Recife, PE", weight_kg=2, notify_email="a@b.co")
            old = settings.smtp_host
            try:
                settings.smtp_host = ""                                  # unconfigured -> visible but not sent
                core.assign(c, "t", s["id"], "ALPHA")
                rows = c.execute("SELECT status, channel FROM notifications").fetchall()
                assert [(r["status"], r["channel"]) for r in rows] == [("skipped_unconfigured", "email")]
                settings.smtp_host = "smtp.test"                         # configured -> queued then sent by the worker
                core.change_status(c, "t", s["id"], Status.IN_TRANSIT)
            finally:
                settings.smtp_host = old
        sent = []
        res = notify.send_queued(send_email=lambda to, subj, body: sent.append((to, subj)))
        assert res == {"sent": 1, "failed": 0} and sent[0][0] == "a@b.co" and "on its way" in sent[0][1]
        bad = notify.send_queued(send_email=lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        assert bad["sent"] == 0


def test_public_tracking_exposes_no_sensitive_fields():
    with temp_db():
        with db() as c:
            s = core.create_shipment(c, "t", order_id="SECRET-ORDER-9", origin="Campinas, SP", destination="Recife, PE", weight_kg=2, deadline="2099-01-01", declared_value=777)
            b = core.assign(c, "t", s["id"], "ALPHA")
            pub = core.public_tracking(c, b["tracking_code"])
            blob = str(pub)
            assert "SECRET-ORDER-9" not in blob and "777" not in blob and "price" not in pub
            assert pub["stage"] == 1 and pub["eta"]["expected"] < pub["eta"]["latest_likely"] and pub["destination"]["label"].startswith("Recife")
            try: core.public_tracking(c, "NOPE1234"); assert False
            except core.NotFound: pass


def test_scorecard_timeline_flows_on_small_dataset():
    with temp_db():
        with db() as c:
            for i in range(30):
                core.create_shipment(c, "t", order_id=f"S{i}", origin="Campinas, SP", destination=["Recife, PE", "Curitiba, PR", "Manaus, AM"][i % 3], weight_kg=1 + i % 5)
            ids = [r[0] for r in c.execute("SELECT id FROM shipments")]
            c.execute("UPDATE shipments SET id = id * 250 WHERE 1=0")     # (scorecard samples id % 250 == 0; falls back to first rows on small DBs)
            sc = core.scorecard(c); assert sc["routes_sampled"] > 0 and {x["carrier_code"] for x in sc["carriers"]} == {p.code for p in DEFAULT_PROFILES}
            assert core.timeline(c)["months"][0]["shipments"] == 30
            fl = core.flows(c); assert fl["flows"][0]["o"] == "SP" and "SP" in fl["states"]
