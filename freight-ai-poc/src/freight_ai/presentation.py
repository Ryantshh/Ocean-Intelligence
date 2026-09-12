"""Business-facing views of authoritative results; no model-generated facts."""

from datetime import date

LABELS = {
    "load_port": "Load port",
    "discharge_port": "Discharge port",
    "cargo_type": "Cargo type",
    "cargo_description": "Cargo description",
    "cargo_weight_min": "Minimum cargo (tonnes)",
    "cargo_weight_max": "Maximum cargo (tonnes)",
    "laycan_start": "Loading window starts",
    "laycan_end": "Loading window ends",
    "vessel_name": "Vessel",
    "dwt": "Deadweight (tonnes)",
    "open_area": "Open location",
    "open_date_start": "Available from",
    "open_date_end": "Available until",
    "commercial_status": "Commercial status",
    "load_zone": "Load region",
    "parent_zone": "Region",
    "ballast_laden": "Ballast or laden",
    "ship_type": "Vessel type",
    "ship_size": "Vessel size",
}
REASONS = {
    "required_cargo_quantity_unknown": "Cargo quantity is missing.",
    "cargo_exceeds_total_deadweight": "Cargo quantity exceeds the vessel's total deadweight.",
    "usable_cargo_capacity_unknown": "Usable cargo capacity has not been confirmed.",
    "cargo_exceeds_assumed_capacity": "Cargo quantity exceeds capacity under the selected allowance.",
    "commercial_status_blocked": "Commercial status is excluded under the screening rules.",
    "commercial_status_unknown_or_unmapped": "Commercial availability needs confirmation.",
    "conflicting_latest_vessel_reports": "The latest vessel reports contain conflicting information.",
    "vessel_report_stale": "The vessel report is older than the permitted age.",
    "no_remaining_window_overlap": "The remaining availability and loading windows do not overlap.",
    "date_window_unknown": "Loading or availability dates are missing.",
    "sea_route_and_arrival_feasibility_unverified": "Arrival at the load port has not been verified.",
    "different_zone": "The vessel and cargo are in different regions.",
    "zone_unknown": "The vessel or cargo region is missing.",
}


def display(value):
    if value is None:
        return "Not provided"
    if isinstance(value, (int, float)):
        return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"
    try:
        return date.fromisoformat(str(value)).strftime("%d %b %Y")
    except ValueError:
        return str(value)


def record_table(records):
    if not records:
        return []
    fields = (
        (
            "load_port",
            "discharge_port",
            "cargo_type",
            "cargo_weight_min",
            "cargo_weight_max",
            "laycan_start",
            "laycan_end",
        )
        if "cargo_weight_min" in records[0]
        else (
            "vessel_name",
            "dwt",
            "open_area",
            "open_date_start",
            "open_date_end",
            "commercial_status",
            "ballast_laden",
        )
    )
    return [{LABELS[f]: display(r.get(f)) for f in fields} for r in records]


def screening_table(rows):
    return [
        {
            "Vessel": r["vessel_name"],
            "Outcome": {
                "excluded": "Excluded",
                "needs_review": "Needs review",
                "screened_candidate": "Passed preliminary checks",
            }[r["status"]],
            "Screening score / 100": r["score"],
            "Required cargo (tonnes)": display(r["required_tonnes"]),
            "Assumed capacity (tonnes)": display(r["assumed_capacity_tonnes"]),
            "Reason / follow-up": " ".join(
                REASONS.get(k, k.replace("_", " ").capitalize() + ".")
                for k in r["exclusions"] + r["unknowns"]
            )
            or "No unresolved checks under the selected rules.",
        }
        for r in rows
    ]


def intent_description(intent):
    action = intent.get("action")
    if action == "match":
        return "Screen vessels for the selected cargo order."
    if action == "qa":
        return "Explain a freight term using the available reference information."
    if action == "clarify":
        return "More information is needed before proceeding."
    noun = "cargo orders" if intent.get("dataset", "orders") == "orders" else "vessels"
    parts = [f"Search {noun}"]
    if intent.get("include_history"):
        parts.append("Include historical vessel reports")
    if intent.get("include_future"):
        parts.append("Include future-dated reports")
    if intent.get("text_match") == "normalized":
        parts.append("Accent-insensitive whole-word text matching")
    for key, value in intent.get("text_filters", {}).items():
        parts.append(f"{LABELS.get(key, key.replace('_', ' '))}: {value}")
    for key, label in [
        ("min_tonnes", "Minimum quantity"),
        ("max_tonnes", "Maximum quantity"),
        ("received_from", "Received on or after"),
        ("received_to", "Received on or before"),
        ("updated_from", "Updated on or after"),
        ("updated_to", "Updated on or before"),
        ("start_from", "First loading/open date on or after"),
        ("start_to", "First loading/open date on or before"),
        ("end_to", "Last loading/open date on or before"),
        ("window_start", "Window starts"),
        ("window_end", "Window ends"),
    ]:
        if intent.get(key) is not None:
            parts.append(
                f"{label}: {display(intent[key])}"
                + (" tonnes" if "tonnes" in key else "")
            )
    aggregation = intent.get("aggregation", "none")
    if aggregation != "none":
        parts.append("Show a count" if aggregation == "count" else "Show total tonnes")
    return "; ".join(parts) + "."


def render_result(result):
    import streamlit as st

    if "clarification" in result:
        st.info(result["clarification"])
        return
    if "glossary" in result:
        for term, definition in result["glossary"].items():
            if term != "provenance":
                st.write(f"**{term}** — {definition}")
        st.caption(result.get("limitation", ""))
        return
    if "candidate_count" in result:
        count = result["candidate_count"]
        st.subheader(f"{count} vessel{'s' if count != 1 else ''} for consideration")
        st.caption(
            f"Screening date: {display(result['as_of'])}. These are preliminary results, not confirmed fixtures."
        )
        if result["candidates"]:
            st.dataframe(screening_table(result["candidates"]), hide_index=True)
            if count > len(result["candidates"]):
                st.caption(
                    f"Showing the first {len(result['candidates'])} of {count} candidates."
                )
        else:
            st.info(
                "No vessels passed the selected screening rules. Review the exclusions below or adjust the assumptions."
            )
        if result["excluded"]:
            st.subheader("Excluded vessels")
            st.dataframe(screening_table(result["excluded"]), hide_index=True)
        with st.expander("How to read these results"):
            st.write(
                "A higher score indicates a better fit under the selected rules. It is not a probability of a successful fixture. Missing information requires review."
            )
            st.write(
                "Sailing time, draft, gear, cargo compatibility and port restrictions have not been checked."
            )
        return
    count = result.get("total_count", 0)
    st.subheader(f"{count} matching record{'s' if count != 1 else ''}")
    if result.get("as_of"):
        st.caption(f"Based on information available by {display(result['as_of'])}.")
    if result.get("records"):
        st.dataframe(record_table(result["records"]), hide_index=True)
    else:
        st.info(
            "No records match this search. Try a different location or broader criteria."
        )
    if result.get("truncated"):
        st.caption(f"Showing {len(result['records'])} of {count} matching records.")
    aggregation = result.get("aggregation")
    if aggregation and "count" in aggregation:
        st.write(f"**Total count: {aggregation['count']:,}**")
    elif aggregation:
        st.subheader("Quantity totals")
        for field, value in aggregation.items():
            if isinstance(value, dict):
                st.write(
                    f"**{LABELS.get(field, field)}:** {display(value['known_sum'])} tonnes across records with a known value."
                )
                if value["missing_count"]:
                    st.warning(
                        f"{value['missing_count']} record(s) have no value for this quantity. The total is incomplete."
                    )
        st.caption(
            "Vessel deadweight includes fuel and stores; it is not usable cargo capacity."
        )
    if result.get("conflicting_vessel_reports"):
        st.warning(
            "Some latest vessel reports conflict. Confirm the details before making a decision."
        )
