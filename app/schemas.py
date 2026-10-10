"""Pydantic request models (validation -> 422 on bad input)."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from .domain import Objective, Status, Role


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: Role = Role.VIEWER


class Weights3(BaseModel):
    cost: float = Field(ge=0, le=1)
    speed: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _nonzero(self):
        if self.cost + self.speed + self.risk <= 0:
            raise ValueError("at least one weight must be > 0")
        return self

    def tup(self): return (self.cost, self.speed, self.risk)


class ShipmentIn(BaseModel):
    order_id: Optional[str] = Field(default=None, max_length=80)
    origin: str = Field(min_length=2, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    origin_zip: Optional[str] = Field(default=None, max_length=10)
    dest_zip: Optional[str] = Field(default=None, max_length=10)
    weight_kg: float = Field(gt=0, le=1000)
    length_cm: Optional[float] = Field(default=None, gt=0, le=300)
    height_cm: Optional[float] = Field(default=None, gt=0, le=300)
    width_cm: Optional[float] = Field(default=None, gt=0, le=300)
    declared_value: float = Field(default=0, ge=0)
    deadline: Optional[str] = None
    seller_id: Optional[str] = Field(default=None, max_length=64)
    notify_email: Optional[str] = Field(default=None, max_length=160, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def dims(self):
        return (self.length_cm, self.height_cm, self.width_cm) if all((self.length_cm, self.height_cm, self.width_cm)) else None


class StatusIn(BaseModel):
    status: Status


class AssignIn(BaseModel):
    carrier_code: str = Field(min_length=2, max_length=20)
    objective: Objective = Objective.BALANCED


class QuoteIn(BaseModel):
    origin: str = Field(min_length=2, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    origin_zip: Optional[str] = None
    dest_zip: Optional[str] = None
    weight_kg: float = Field(gt=0, le=1000)
    length_cm: Optional[float] = Field(default=None, gt=0, le=300)
    height_cm: Optional[float] = Field(default=None, gt=0, le=300)
    width_cm: Optional[float] = Field(default=None, gt=0, le=300)
    declared_value: float = Field(default=0, ge=0)
    deadline_days: Optional[float] = Field(default=None, gt=0, le=120)
    deadline_mode: str = Field(default="p90", pattern="^(p50|p90)$")
    objective: Objective = Objective.BALANCED
    weights: Optional[Weights3] = None
    exclude: list[str] = []
    late_penalty: Optional[float] = Field(default=None, ge=0, le=5000)   # BRL cost of one late delivery (default from models.json)
    ship_month: Optional[int] = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def _custom(self):
        if self.objective == Objective.CUSTOM and not self.weights:
            raise ValueError("objective 'custom' requires weights")
        return self


class ShipmentQuoteIn(BaseModel):
    objective: Objective = Objective.BALANCED
    weights: Optional[Weights3] = None
    late_penalty: Optional[float] = Field(default=None, ge=0, le=5000)
    deadline_mode: str = Field(default="p90", pattern="^(p50|p90)$")


class SourcingIn(BaseModel):
    product_id: str = Field(min_length=3, max_length=64)
    destination: str = Field(min_length=2, max_length=120)
    dest_zip: Optional[str] = None
    quantity: int = Field(default=1, ge=1, le=500)
    objective: Objective = Objective.BALANCED
    deadline_days: Optional[float] = Field(default=None, gt=0, le=120)


class BatchIn(BaseModel):
    shipment_ids: Optional[list[int]] = Field(default=None, max_length=2000)
    limit: int = Field(default=200, ge=1, le=2000)
    objective: Objective = Objective.BALANCED
    weights: Optional[Weights3] = None
    capacity: Optional[dict[str, int]] = None
    deadline_mode: str = Field(default="p90", pattern="^(p50|p90)$")
    late_penalty: Optional[float] = Field(default=None, ge=0, le=5000)
    relax_deadline: bool = False      # True: orders whose deadline no carrier can meet are still assigned (flagged `relaxed`, deadline at risk)
    apply: bool = False


class WhatIfIn(BaseModel):
    limit: int = Field(default=300, ge=10, le=2000)
    weights: Optional[Weights3] = None


class ConsolidateIn(BaseModel):
    shipment_ids: Optional[list[int]] = Field(default=None, max_length=2000)
    limit: int = Field(default=300, ge=2, le=2000)


class CarrierPatch(BaseModel):
    active: Optional[bool] = None
    coverage: Optional[str] = Field(default=None, max_length=200)
    daily_capacity: Optional[int] = Field(default=None, ge=0)
    price_mult: Optional[float] = Field(default=None, gt=0, le=10)
    speed_mult: Optional[float] = Field(default=None, gt=0, le=10)


class TrackingWebhook(BaseModel):
    tracking_code: str
    event: str = Field(pattern="^(picked_up|in_transit|out_for_delivery|delivered|exception)$")
    ts: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=300)


class RerouteIn(BaseModel):
    carrier_code: str
