"""Notification outbox: every important shipment event writes rows (email / webhook). A background worker sends them when a channel is
configured (NOTIFY_SMTP_HOST and/or NOTIFY_WEBHOOK_URL); otherwise rows are kept as 'skipped_unconfigured' so the feature is visible and testable."""
from __future__ import annotations
import hashlib, hmac, json, logging, smtplib, threading, urllib.request
from email.message import EmailMessage
from typing import Callable, Optional
from ..config import settings
from ..db import db, now

log = logging.getLogger("olist.notify")
EVENT_TEXT = {"booked": "has been booked with a carrier", "in_transit": "is on its way", "delivered": "was delivered", "canceled": "was canceled", "pending": "went back to pending (re-routing)"}


def channels() -> dict[str, bool]:
    return {"email": bool(settings.smtp_host), "webhook": bool(settings.notify_webhook_url)}


def _link(shipment: dict) -> str:
    return f"{settings.public_base_url.rstrip('/')}/track?code={shipment['tracking_code']}" if shipment.get("tracking_code") else ""


def enqueue(con, shipment: dict, event: str) -> int:
    """Write outbox rows for this event. Returns the number of rows created."""
    ev = event.lower().replace(" ", "_"); text = EVENT_TEXT.get(ev, ev)
    subject = f"Shipment {shipment['order_id']} {text}"
    body = f"Your shipment {shipment['order_id']} {text}.\nRoute: {shipment.get('origin')} -> {shipment.get('destination')}\n" + (f"Carrier: {shipment['carrier_code']}\n" if shipment.get("carrier_code") else "") + (f"Track: {_link(shipment)}\n" if _link(shipment) else "")
    ch, n = channels(), 0
    targets = []
    if shipment.get("notify_email"): targets.append(("email", shipment["notify_email"], ch["email"]))
    if settings.notify_webhook_url: targets.append(("webhook", settings.notify_webhook_url, True))
    for channel, rcpt, configured in targets:
        con.execute("INSERT INTO notifications(shipment_id,channel,recipient,event,subject,body,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (shipment["id"], channel, rcpt, ev, subject, body, "queued" if configured else "skipped_unconfigured", now())); n += 1
    return n


def _send_email(to: str, subject: str, body: str) -> None:
    m = EmailMessage(); m["From"] = settings.smtp_from; m["To"] = to; m["Subject"] = subject; m.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        if settings.smtp_tls: s.starttls()
        if settings.smtp_user: s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(m)


def _send_webhook(url: str, payload: dict) -> None:
    raw = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json", "X-Signature": hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()})
    urllib.request.urlopen(req, timeout=10).read()


def send_queued(limit: int = 50, send_email: Callable = _send_email, send_webhook: Callable = _send_webhook) -> dict:
    sent = failed = 0
    with db() as c:
        rows = c.execute("SELECT * FROM notifications WHERE status='queued' ORDER BY id LIMIT ?", (limit,)).fetchall()
        for r in rows:
            try:
                if r["channel"] == "email": send_email(r["recipient"], r["subject"], r["body"])
                else: send_webhook(r["recipient"], {"event": r["event"], "subject": r["subject"], "body": r["body"], "shipment_id": r["shipment_id"]})
                c.execute("UPDATE notifications SET status='sent', sent_at=? WHERE id=?", (now(), r["id"])); sent += 1
            except Exception as e:
                c.execute("UPDATE notifications SET status='failed', error=? WHERE id=?", (str(e)[:300], r["id"])); failed += 1
    return {"sent": sent, "failed": failed}


_stop = threading.Event()


def start_worker() -> Optional[threading.Thread]:
    if not any(channels().values()): return None
    def loop():
        while not _stop.wait(15):
            try: send_queued()
            except Exception: log.exception("notification worker error")
    t = threading.Thread(target=loop, daemon=True, name="notify-worker"); t.start(); return t


def stop_worker(): _stop.set()
