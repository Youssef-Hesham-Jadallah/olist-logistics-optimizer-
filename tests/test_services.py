from app.domain import Status, InvalidTransition, check_transition
from app.services import core
from app.security import hash_password, verify_password, create_token, decode_token
from .helpers import temp_db
from app.db import db, audit


def mk(c, **k):
    base = dict(order_id=None, origin="Campinas, SP", destination="Recife, PE", weight_kg=2.0, deadline="2099-01-01")
    base.update(k); return core.create_shipment(c, "t", **base)


def test_state_machine_rules():
    check_transition(Status.PENDING, Status.BOOKED); check_transition(Status.BOOKED, Status.IN_TRANSIT); check_transition(Status.IN_TRANSIT, Status.DELIVERED)
    for a, b in ((Status.DELIVERED, Status.PENDING), (Status.IN_TRANSIT, Status.CANCELED), (Status.PENDING, Status.DELIVERED), (Status.CANCELED, Status.PENDING)):
        try: check_transition(a, b); assert False, (a, b)
        except InvalidTransition: pass


def test_full_lifecycle_and_audit():
    with temp_db():
        with db() as c:
            s = mk(c, order_id="A1")
            q = core.quote_shipment(c, s["id"], objective="cost")
            code = q["recommendation"]["carrier_code"]
            try: core.change_status(c, "t", s["id"], Status.BOOKED); assert False
            except InvalidTransition: pass                      # needs a carrier first
            b = core.assign(c, "t", s["id"], code)
            assert b["status"] == "Booked" and b["price"] == q["recommendation"]["price"] and b["tracking_code"] and b["currency"] == "BRL"
            assert core.change_status(c, "t", s["id"], Status.IN_TRANSIT)["status"] == "In Transit"
            assert core.change_status(c, "t", s["id"], Status.DELIVERED)["status"] == "Delivered"
            n = c.execute("SELECT COUNT(*) FROM audit_log WHERE entity='shipment' AND entity_id=?", (str(s["id"]),)).fetchone()[0]
            assert n >= 4


def test_assign_rejects_uncovered_carrier_and_missed_deadline():
    with temp_db():
        with db() as c:
            s = mk(c, destination="Manaus, AM")
            try: core.assign(c, "t", s["id"], "GAMMA"); assert False
            except ValueError as e: assert "cannot serve" in str(e)
            s2 = mk(c, destination="Manaus, AM", order_id="tight", deadline="2000-01-01")
            try: core.assign(c, "t", s2["id"], "ALPHA"); assert False
            except ValueError: pass


def test_duplicate_order_id_and_validation():
    with temp_db():
        with db() as c:
            mk(c, order_id="X"); 
            try: mk(c, order_id="X"); assert False
            except core.Conflict: pass
            try: mk(c, destination="nowhere land"); assert False
            except ValueError: pass
            try: mk(c, deadline="banana"); assert False
            except ValueError: pass


def test_soft_delete_and_restore():
    with temp_db():
        with db() as c:
            s = mk(c); core.soft_delete(c, "t", s["id"])
            assert core.list_shipments(c)["total"] == 0
            try: core.get_shipment(c, s["id"]); assert False
            except core.NotFound: pass
            assert core.restore(c, "t", s["id"])["id"] == s["id"]
            assert core.list_shipments(c)["total"] == 1


def test_pagination_and_kpis_are_independent_of_filters():
    with temp_db():
        with db() as c:
            for i in range(7): mk(c, order_id=f"P{i}")
            page = core.list_shipments(c, limit=3, offset=3)
            assert len(page["items"]) == 3 and page["total"] == 7
            k_all = core.kpis(c); core.list_shipments(c, q="P1", status="Delivered")
            assert core.kpis(c) == k_all and k_all["total"] == 7 and k_all["pending"] == 7


def test_capacity_blocks_assignment():
    with temp_db():
        with db() as c:
            c.execute("UPDATE carriers SET daily_capacity=1 WHERE code='ALPHA'")
            a, b = mk(c, order_id="c1"), mk(c, order_id="c2")
            core.assign(c, "t", a["id"], "ALPHA")
            try: core.assign(c, "t", b["id"], "ALPHA"); assert False
            except core.Conflict: pass
            q = core.quote_shipment(c, b["id"])
            assert "ALPHA" not in {x["carrier_code"] for x in q["quotes"]}


def test_reroute_suggestion_when_carrier_down():
    from app.engine.carriers import HEALTH
    with temp_db():
        with db() as c:
            s = mk(c, destination="Rio de Janeiro"); core.assign(c, "t", s["id"], "ALPHA")
            HEALTH.forced_down.add("ALPHA")
            try:
                sug = core.reroute_suggestion(c, s["id"])
                assert sug["reroute_recommended"] and sug["alternatives"] and all(a["carrier_code"] != "ALPHA" for a in sug["alternatives"])
            finally: HEALTH.forced_down.discard("ALPHA")


def test_tracking_webhook_moves_status():
    with temp_db():
        with db() as c:
            s = mk(c); b = core.assign(c, "t", s["id"], "ALPHA")
            core.apply_tracking_event(c, b["tracking_code"], "in_transit", None, None)
            assert core.get_shipment(c, s["id"])["status"] == "In Transit"
            core.apply_tracking_event(c, b["tracking_code"], "delivered", None, None)
            assert core.get_shipment(c, s["id"])["status"] == "Delivered"


def test_csv_injection_guard():
    assert core.sanitize_cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")" and core.sanitize_cell("@cmd") == "'@cmd" and core.sanitize_cell("normal") == "normal"
    with temp_db():
        with db() as c: assert mk(c, order_id="=1+1")["order_id"].startswith("'")


def test_password_and_jwt():
    h = hash_password("s3cret-pass"); assert verify_password("s3cret-pass", h) and not verify_password("nope", h)
    t = create_token("bob", "operator"); p = decode_token(t); assert p["sub"] == "bob" and p["role"] == "operator"
