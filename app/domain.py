"""Domain enums, status mapping and the shipment state machine."""
from __future__ import annotations
from enum import Enum


class Status(str, Enum):
    PENDING = "Pending"
    BOOKED = "Booked"
    IN_TRANSIT = "In Transit"
    DELIVERED = "Delivered"
    CANCELED = "Canceled"


class Objective(str, Enum):
    COST = "cost"
    SPEED = "speed"
    RELIABILITY = "reliability"
    BALANCED = "balanced"
    EXPECTED_COST = "expected_cost"   # minimise price + P(late) x late penalty (BRL)
    CUSTOM = "custom"


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


# Pending -> Booked -> In Transit -> Delivered ; Canceled only from Pending/Booked
TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.BOOKED, Status.CANCELED},
    Status.BOOKED: {Status.IN_TRANSIT, Status.CANCELED, Status.PENDING},   # Booked->Pending = un-assign (carrier cleared)
    Status.IN_TRANSIT: {Status.DELIVERED},
    Status.DELIVERED: set(),
    Status.CANCELED: set(),
}

# Olist order_status -> internal status
OLIST_STATUS_MAP = {
    "created": Status.PENDING, "approved": Status.PENDING, "invoiced": Status.PENDING, "processing": Status.PENDING,
    "shipped": Status.IN_TRANSIT, "delivered": Status.DELIVERED, "canceled": Status.CANCELED, "unavailable": Status.CANCELED,
}


class InvalidTransition(ValueError):
    pass


def check_transition(cur: Status, new: Status) -> None:
    if cur == new:
        raise InvalidTransition(f"shipment is already {cur.value}")
    if new not in TRANSITIONS[cur]:
        allowed = ", ".join(s.value for s in TRANSITIONS[cur]) or "none (terminal state)"
        raise InvalidTransition(f"cannot move {cur.value} -> {new.value}; allowed: {allowed}")
