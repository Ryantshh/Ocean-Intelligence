from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Provenance(StrictModel):
    file: str
    sha256: str
    sheet: str
    row: int


class Record(StrictModel):
    record_id: str
    source: Provenance
    date_received: datetime | None = None
    update_date: datetime | None = None


class Order(Record):
    laycan_start: date | None = None
    laycan_end: date | None = None
    load_port: str | None = None
    discharge_port: str | None = None
    cargo_type: str | None = None
    cargo_description: str | None = None
    load_zone: str | None = None
    discharge_zone: str | None = None
    cargo_weight_min: float | None = Field(default=None, ge=0)
    cargo_weight_max: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def ranges(self):
        for start, end in [
            (self.laycan_start, self.laycan_end),
            (self.cargo_weight_min, self.cargo_weight_max),
        ]:
            if start is not None and end is not None and start > end:
                raise ValueError("Order range is reversed")
        return self


class Tonnage(Record):
    vessel_name: str
    first_date_received: datetime | None = None
    dwt: float | None = Field(default=None, gt=0)
    open_area: str | None = None
    open_date_start: date | None = None
    open_date_end: date | None = None
    eta_date_start: date | None = None
    parent_zone: str | None = None
    commercial_status: str | None = None
    ship_type: str | None = None
    ship_size: str | None = None
    vessel_status: str | None = None
    ballast_laden: str | None = None
    destination: str | None = None

    @model_validator(mode="after")
    def ranges(self):
        if (
            self.open_date_start
            and self.open_date_end
            and self.open_date_start > self.open_date_end
        ):
            raise ValueError("Open date range is reversed")
        return self


class Intent(StrictModel):
    action: Literal["query", "match", "summarize", "qa", "clarify"]
    dataset: Literal["orders", "tonnage"] = "orders"
    record_id: str | None = None
    text_filters: dict[str, str] = Field(default_factory=dict)
    min_tonnes: float | None = Field(default=None, ge=0)
    max_tonnes: float | None = Field(default=None, ge=0)
    received_from: date | None = None
    received_to: date | None = None
    start_from: date | None = None
    start_to: date | None = None
    end_to: date | None = None
    include_history: bool = False
    include_future: bool = False
    text_match: Literal["exact", "normalized"] = "exact"
    updated_from: date | None = None
    updated_to: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    aggregation: Literal["none", "count", "sum_tonnes"] = "none"
    limit: int = Field(default=10, ge=1, le=100)
    clarification: str | None = None

    @model_validator(mode="after")
    def validate_constraints(self):
        if self.action not in {"query", "summarize"} and any((self.received_from, self.received_to, self.start_from, self.start_to, self.end_to, self.include_history, self.include_future, self.text_match != "exact")):
            raise ValueError("Search options require a query or summary")
        allowed = {
            "orders": {
                "cargo_description",
                "load_port",
                "discharge_port",
                "cargo_type",
                "load_zone",
                "discharge_zone",
            },
            "tonnage": {
                "destination",
                "vessel_status",
                "vessel_name",
                "open_area",
                "parent_zone",
                "commercial_status",
                "ship_type",
                "ship_size",
                "ballast_laden",
            },
        }
        if self.text_filters.keys() - allowed[self.dataset]:
            raise ValueError("Unsupported filter field for dataset")
        for lo, hi in [
            (self.min_tonnes, self.max_tonnes),
            (self.window_start, self.window_end),
            (self.updated_from, self.updated_to),
            (self.received_from, self.received_to),
            (self.start_from, self.start_to),
        ]:
            if lo is not None and hi is not None and lo > hi:
                raise ValueError("Query range is reversed")
        if self.action == "match" and (not self.record_id or self.dataset != "orders"):
            raise ValueError("Match requires an order record_id and dataset=orders")
        if self.action == "clarify" and not self.clarification:
            raise ValueError("Clarify requires a question")
        if self.action in {"match", "qa", "clarify"} and (self.updated_from or self.updated_to):
            raise ValueError("Report-date filters require a query or summary")
        if self.action == "match" and (
            self.text_filters
            or self.min_tonnes is not None
            or self.max_tonnes is not None
            or self.window_start
            or self.window_end
            or self.aggregation != "none"
        ):
            raise ValueError(
                "Match uses the selected order; additional constraints require clarification"
            )
        return self
