"""Validation shared by the order and tonnage override endpoints."""
from datetime import date

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator


class OverrideBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def validate_dates(cls, value, info):
        if info.field_name in {"laycan_start", "laycan_end", "open_date_start", "open_date_end"}:
            if value in (None, ""):
                return None
            try:
                return date.fromisoformat(value).isoformat()
            except (ValueError, TypeError):
                raise ValueError("Use a valid date in YYYY-MM-DD format") from None
        return value


def validate_range(body, snapshot, start, end, *, dates=False):
    """Validate the effective pair, including the stored half of a partial edit."""
    values = [getattr(body, key) if getattr(body, key) is not None else snapshot.get(key)
              for key in (start, end)]
    if None in values:
        return
    if dates:
        values = [str(value)[:10] for value in values]
    if values[1] < values[0]:
        raise HTTPException(status_code=422, detail=f"{end} cannot be less than {start}.")
