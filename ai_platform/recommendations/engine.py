"""Bounded specialist orchestration; source evidence determines eligibility.

These specialists are deterministic agents, not independent language models.
DWT is only an initial screen: it includes fuel/stores, not just cargo capacity.
"""

from __future__ import annotations

import asyncio
from datetime import date

VERSION = "vessel-shortlist-v1"


def day(value):
    return date.fromisoformat(str(value)[:10]) if value else None


async def suitability_agent(order, vessel):
    reasons, blockers = [], []
    weight, dwt = order.get("cargo_weight_min"), vessel.get("dwt")
    if not weight or not dwt:
        blockers.append("Missing cargo weight or vessel DWT")
    elif float(dwt) < float(weight):
        blockers.append("DWT below minimum cargo weight")
    else:
        reasons.append(
            "DWT passes preliminary weight screen; usable capacity needs confirmation"
        )
    start, end = day(order.get("laycan_start")), day(order.get("laycan_end"))
    available, until = (
        day(vessel.get("open_date_start")),
        day(vessel.get("open_date_end")),
    )
    if not all((start, end, available, until)):
        blockers.append("Missing laycan or vessel open window")
    elif start > end or available > until:
        blockers.append("Invalid date window")
    elif available > end or until < start:
        blockers.append("Open window does not overlap laycan")
    else:
        reasons.append(
            "Reported open window overlaps laycan; sailing time is unverified"
        )
    zone = str(order.get("load_zone") or "").strip().casefold()
    same_zone = bool(
        zone and zone == str(vessel.get("parent_zone") or "").strip().casefold()
    )
    if same_zone:
        reasons.append("Reported in the loading region")
    return {
        "agent": "suitability",
        "reasons": reasons,
        "blockers": blockers,
        "same_zone": same_zone,
    }


async def risk_agent(order, vessel, as_of):
    blockers, warnings = (
        [],
        [
            "Freight economics, port restrictions and voyage feasibility require trader review"
        ],
    )
    status = str(vessel.get("commercial_status") or "").upper().strip()
    if status not in ("", "AVAILABLE", "OPEN"):
        blockers.append(f"Commercial status requires clearance: {status}")
    if not status:
        warnings.append("Commercial availability is unknown")
    updated = day(vessel.get("update_date"))
    if updated is None or (as_of - updated).days > 2:
        blockers.append(
            "Position is missing or older than the provisional two-day freshness limit"
        )
    elif updated > as_of:
        blockers.append("Position is dated after the working date")
    if day(order.get("laycan_end")) and day(order["laycan_end"]) < as_of:
        blockers.append("Order laycan has expired")
    return {"agent": "risk", "blockers": blockers, "warnings": warnings}


async def recommend(order, vessels, as_of):
    candidates, rejected = [], []
    for vessel in vessels:
        fit, risk = await asyncio.gather(
            suitability_agent(order, vessel), risk_agent(order, vessel, as_of)
        )
        item = {
            "vessel_id": vessel["vessel_id"],
            "evidence": vessel,
            "agents": [fit, risk],
        }
        if fit["blockers"] or risk["blockers"]:
            rejected.append(item)
        else:
            candidates.append(item)
    candidates.sort(
        key=lambda item: (not item["agents"][0]["same_zone"], item["vessel_id"])
    )
    return {
        "workflow_version": VERSION,
        "as_of": as_of.isoformat(),
        "order": order,
        "candidates": candidates[:10],
        "not_shortlisted": candidates[10:],
        "rejected": rejected,
        "summary": "Advisory shortlist; no vessel assignment or trade execution.",
        "ranking_policy": "Same loading region first, then vessel identifier; no profitability score.",
    }
