from datetime import date
from math import asin, cos, radians, sin, sqrt
from typing import Literal

from pydantic import Field, model_validator

from freight_ai.data.models import Order, StrictModel

from .query import latest_vessels, norm


class Weights(StrictModel):
    capacity: float = Field(default=0.5, ge=0)
    window: float = Field(default=0.3, ge=0)
    geography: float = Field(default=0.2, ge=0)

    @model_validator(mode="after")
    def nonzero(self):
        if self.capacity + self.window + self.geography <= 0:
            raise ValueError("At least one scoring weight must be positive")
        return self


class Coordinate(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    source: str = Field(min_length=1)


class MatchingConfig(StrictModel):
    cargo_fraction: float | None = Field(default=None, gt=0, le=1)
    required_quantity: Literal["min", "max"] = "max"
    require_same_zone: bool = False
    blocked_statuses: list[str] = Field(default_factory=lambda: ["FIXED", "ON SUBS"])
    allowed_statuses: list[str] = Field(default_factory=lambda: ["OPEN"])
    max_report_age_days: int | None = Field(default=None, ge=0)
    weights: Weights = Field(default_factory=Weights)
    coordinates: dict[str, Coordinate] = Field(default_factory=dict)

    @model_validator(mode="after")
    def disjoint(self):
        if {norm(s) for s in self.blocked_statuses} & {
            norm(s) for s in self.allowed_statuses
        }:
            raise ValueError("Allowed and blocked statuses overlap")
        return self


def haversine_nm(a: Coordinate, b: Coordinate):
    lat1, lat2 = radians(a.latitude), radians(b.latitude)
    dlat, dlon = lat2 - lat1, radians(b.longitude - a.longitude)
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 3440.065 * 2 * asin(sqrt(min(1, max(0, h))))


def match(order: Order, vessels, config: MatchingConfig, as_of: date, limit=10):
    if (
        order.update_date is None
        or order.date_received is None
        or max(order.update_date.date(), order.date_received.date()) > as_of
    ):
        raise ValueError("Order is not known at the requested as-of date")
    candidates, excluded = [], []
    latest, conflicts = latest_vessels(vessels, as_of)
    for v in latest:
        reasons, unknowns = [], []
        components = {"capacity": None, "window": None, "geography": None}
        quantity = getattr(order, f"cargo_weight_{config.required_quantity}")
        capacity = (
            v.dwt * config.cargo_fraction
            if v.dwt and config.cargo_fraction is not None
            else None
        )
        if quantity is None:
            unknowns.append("required_cargo_quantity_unknown")
        elif v.dwt is not None and quantity > v.dwt:
            reasons.append("cargo_exceeds_total_deadweight")
        if capacity is None:
            unknowns.append("usable_cargo_capacity_unknown")
        elif quantity is not None:
            if quantity > capacity:
                reasons.append("cargo_exceeds_assumed_capacity")
            else:
                components["capacity"] = quantity / capacity
        status = norm(v.commercial_status)
        if status in {norm(s) for s in config.blocked_statuses}:
            reasons.append("commercial_status_blocked")
        elif status not in {norm(s) for s in config.allowed_statuses}:
            unknowns.append("commercial_status_unknown_or_unmapped")
        if v.record_id in conflicts:
            unknowns.append("conflicting_latest_vessel_reports")
        if (
            config.max_report_age_days is not None
            and (as_of - v.update_date.date()).days > config.max_report_age_days
        ):
            reasons.append("vessel_report_stale")
        if all(
            (order.laycan_start, order.laycan_end, v.open_date_start, v.open_date_end)
        ):
            # This is only open-window overlap, not sailing-time feasibility.
            start = max(order.laycan_start, v.open_date_start, as_of)
            end = min(order.laycan_end, v.open_date_end)
            if start > end:
                reasons.append("no_remaining_window_overlap")
            else:
                components["window"] = ((end - start).days + 1) / (
                    (order.laycan_end - order.laycan_start).days + 1
                )
        else:
            unknowns.append("date_window_unknown")
        same_port = bool(
            order.load_port
            and v.open_area
            and norm(order.load_port) == norm(v.open_area)
        )
        same_zone = bool(
            order.load_zone
            and v.parent_zone
            and norm(order.load_zone) == norm(v.parent_zone)
        )
        if same_port:
            components["geography"] = 1.0
        elif same_zone:
            components["geography"] = 0.5
        elif order.load_zone and v.parent_zone:
            components["geography"] = 0.0
        if not same_port:
            unknowns.append("sea_route_and_arrival_feasibility_unverified")
        if config.require_same_zone and not same_zone:
            if order.load_zone and v.parent_zone:
                reasons.append("different_zone")
            else:
                unknowns.append("zone_unknown")
        coords = {norm(k): val for k, val in config.coordinates.items()}
        a, b = coords.get(norm(order.load_port)), coords.get(norm(v.open_area))
        distance = haversine_nm(a, b) if a and b else None
        weights = config.weights.model_dump()
        score = (
            100
            * sum(weights[k] * (value or 0) for k, value in components.items())
            / sum(weights.values())
        )
        row = {
            "vessel_record_id": v.record_id,
            "vessel_name": v.vessel_name,
            "status": "excluded"
            if reasons
            else "needs_review"
            if unknowns
            else "screened_candidate",
            "score": round(score, 4),
            "components": components,
            "exclusions": reasons,
            "unknowns": unknowns,
            "required_tonnes": quantity,
            "assumed_capacity_tonnes": capacity,
            "straight_line_distance_nm": round(distance, 2)
            if distance is not None
            else None,
            "source": v.source.model_dump(),
            "confirmed_match": False,
        }
        (excluded if reasons else candidates).append(row)
    candidates.sort(
        key=lambda r: (
            r["status"] != "screened_candidate",
            -r["score"],
            r["vessel_record_id"],
        )
    )
    excluded.sort(key=lambda r: r["vessel_record_id"])
    return {
        "order_record_id": order.record_id,
        "as_of": as_of.isoformat(),
        "candidates": candidates[:limit],
        "candidate_count": len(candidates),
        "excluded": excluded,
        "vessel_snapshots_considered": len(latest),
        "input_vessel_snapshots": len(vessels),
        "assumptions": config.model_dump(mode="json"),
        "limitations": [
            "Provisional screening, never a confirmed fixture.",
            "No sailing-time, draft, gear, cargo-compatibility or port restriction validation.",
            "Missing score components contribute zero; scores are not probabilities.",
            "Coordinates, when configured, provide straight-line distance only.",
        ],
    }
