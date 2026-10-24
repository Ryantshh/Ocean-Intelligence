"""Voyage estimate for one cargo on one vessel: revenue and profit.

``calculate_voyage`` is the calculator: plain arithmetic over ``VoyageInputs``.
``draft_voyage`` fills those inputs for a matched order and vessel from their rows,
the port list and the calculator defaults, and records what is still needed from
the trader. ``apply_trader_edits`` folds the trader's panel input back in, and
``rank_voyages`` orders an order's drafts by profit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_platform.backend.distances import find_port, port_options, sea_distance

Source = Literal["supabase", "computed", "default", "saved", "trader"]

Level = Literal["order", "vessel", "settings"]


def _field(default: float, label: str, unit: str, group: str, level: Level, is_positive: bool = True) -> Any:
    """Declare one calculator input with its default and panel metadata.

    Parameters
    ----------
    default : float
        Calculator default.
    label : str
        Name shown in the panel.
    unit : str
        Unit shown after the box.
    group : str
        Panel section the field sits in.
    level : {"order", "vessel", "settings"}
        Shared by every vessel on an order, set per vessel, or set only in the
        trader's saved defaults.
    is_positive : bool
        True requires a value above zero; False allows zero.

    Returns
    -------
    Any
        A pydantic ``Field``.
    """
    return Field(
        default,
        description=label,
        json_schema_extra={"unit": unit, "group": group, "level": level},
        gt=0 if is_positive else None,
        ge=None if is_positive else 0,
    )


class VoyageInputs(BaseModel):
    """The 34 calculator inputs, each defaulting to the calculator's value.

    Commissions are percentages, so 3.75 means 3.75%.
    """

    model_config = ConfigDict(extra="forbid")

    deadweight: float = _field(62_000, "Deadweight", "t", "Vessel", "vessel")
    grain_capacity: float = _field(70_000, "Grain capacity", "m³", "Vessel", "vessel")
    speed_empty: float = _field(14, "Speed when sailing empty", "knots", "Speed", "vessel")
    speed_loaded: float = _field(12, "Speed when sailing loaded", "knots", "Speed", "vessel")
    fuel_oil_sea_empty: float = _field(23, "Intermediate fuel oil at sea, empty", "t/day", "Fuel burned per day", "vessel", False)
    fuel_oil_sea_loaded: float = _field(18.5, "Intermediate fuel oil at sea, loaded", "t/day", "Fuel burned per day", "vessel", False)
    diesel_sea_empty: float = _field(0.1, "Marine diesel oil at sea, empty", "t/day", "Fuel burned per day", "vessel", False)
    diesel_sea_loaded: float = _field(0.1, "Marine diesel oil at sea, loaded", "t/day", "Fuel burned per day", "vessel", False)
    fuel_oil_port_working: float = _field(5.5, "Intermediate fuel oil in port, working", "t/day", "Fuel burned per day", "vessel", False)
    fuel_oil_port_idle: float = _field(5.5, "Intermediate fuel oil in port, idle", "t/day", "Fuel burned per day", "vessel", False)
    diesel_port_working: float = _field(0.1, "Marine diesel oil in port, working", "t/day", "Fuel burned per day", "vessel", False)
    diesel_port_idle: float = _field(0.1, "Marine diesel oil in port, idle", "t/day", "Fuel burned per day", "vessel", False)
    daily_hire: float = _field(12_000, "Daily hire rate", "per day", "Hire", "vessel", False)
    hire_commission: float = _field(3.75, "Address commission on hire", "%", "Hire", "vessel", False)
    distance_empty: float = _field(3_000, "Distance sailing empty to the load port", "nm", "Sailing empty to the load port", "vessel", False)
    days_taking_fuel: float = _field(1, "Days spent taking on fuel", "days", "Sailing empty to the load port", "vessel", False)
    cargo_quantity: float = _field(60_500, "Cargo quantity", "t", "Cargo", "order")
    stowage_factor: float = _field(1.33, "Stowage factor", "m³/t", "Cargo", "order")
    freight_rate: float = _field(22, "Freight rate", "per t", "Freight", "order", False)
    freight_commission: float = _field(3.75, "Address commission on freight", "%", "Freight", "order", False)
    broker_commission: float = _field(1.25, "Broker commission", "%", "Freight", "order", False)
    loading_rate: float = _field(8_000, "Loading rate", "t/day", "Port", "settings")
    discharging_rate: float = _field(11_000, "Discharging rate", "t/day", "Port", "settings")
    load_turn_time: float = _field(0.5, "Turn time at the load port", "days", "Port", "settings", False)
    discharge_turn_time: float = _field(0.5, "Turn time at the discharge port", "days", "Port", "settings", False)
    idle_days: float = _field(0.5, "Idle days in port", "days", "Port", "settings", False)
    distance_loaded: float = _field(3_000, "Distance sailing loaded to the discharge port", "nm", "Sailing loaded to the discharge port", "order", False)
    fuel_oil_price: float = _field(440, "Intermediate fuel oil price", "per t", "Prices and costs", "settings", False)
    diesel_price: float = _field(850, "Marine diesel oil price", "per t", "Prices and costs", "settings", False)
    load_port_charges: float = _field(20_000, "Load port charges", "lump sum", "Prices and costs", "settings", False)
    discharge_port_charges: float = _field(20_000, "Discharge port charges", "lump sum", "Prices and costs", "settings", False)
    cables_entertainment_victualling: float = _field(1_500, "Cables, entertainment and victualling", "lump sum", "Prices and costs", "settings", False)
    hold_cleaning_payment: float = _field(5_000, "Payment in lieu of hold cleaning", "lump sum", "Prices and costs", "settings", False)
    fuel_agency_fee: float = _field(1_500, "Agency fee for taking on fuel", "lump sum", "Prices and costs", "settings", False)


class VoyageResult(BaseModel):
    """What one voyage earns.

    Attributes
    ----------
    revenue : float
        Gross freight less freight and broker commissions.
    profit : float
        Revenue less net hire, fuel cost and other costs.
    """

    revenue: float
    profit: float


def calculate_voyage(inputs: VoyageInputs) -> VoyageResult:
    """Run the voyage estimate, Steps 1 to 10 of the field-source sheet.

    Parameters
    ----------
    inputs : VoyageInputs
        Every calculator input.

    Returns
    -------
    VoyageResult
        Revenue and profit.
    """
    space_limit = inputs.grain_capacity / inputs.stowage_factor
    quantity_loaded = min(inputs.cargo_quantity, space_limit, inputs.deadweight)

    empty_leg_days = inputs.distance_empty / (inputs.speed_empty * 24)
    loaded_leg_days = inputs.distance_loaded / (inputs.speed_loaded * 24)

    load_port_working_days = quantity_loaded / inputs.loading_rate + inputs.load_turn_time
    discharge_port_days = quantity_loaded / inputs.discharging_rate + inputs.discharge_turn_time
    working_port_days = load_port_working_days + discharge_port_days
    total_days = (
        empty_leg_days
        + loaded_leg_days
        + inputs.days_taking_fuel
        + load_port_working_days
        + inputs.idle_days
        + discharge_port_days
    )

    net_hire = inputs.daily_hire * total_days * (1 - inputs.hire_commission / 100)

    idle_burn_days = inputs.idle_days + inputs.days_taking_fuel
    fuel_oil_tonnes = (
        empty_leg_days * inputs.fuel_oil_sea_empty
        + loaded_leg_days * inputs.fuel_oil_sea_loaded
        + working_port_days * inputs.fuel_oil_port_working
        + idle_burn_days * inputs.fuel_oil_port_idle
    )
    diesel_tonnes = (
        empty_leg_days * inputs.diesel_sea_empty
        + loaded_leg_days * inputs.diesel_sea_loaded
        + working_port_days * inputs.diesel_port_working
        + idle_burn_days * inputs.diesel_port_idle
    )
    fuel_cost = fuel_oil_tonnes * inputs.fuel_oil_price + diesel_tonnes * inputs.diesel_price

    other_costs = (
        inputs.cables_entertainment_victualling
        + inputs.hold_cleaning_payment
        + inputs.fuel_agency_fee
        + inputs.load_port_charges
        + inputs.discharge_port_charges
    )

    gross_freight = quantity_loaded * inputs.freight_rate
    commission_share = (inputs.freight_commission + inputs.broker_commission) / 100
    revenue = gross_freight * (1 - commission_share)
    profit = revenue - net_hire - fuel_cost - other_costs
    return VoyageResult(revenue=round(revenue, 2), profit=round(profit, 2))


PORT_FIELDS: dict[str, dict[str, str]] = {
    "open_port": {"label": "Open port", "group": "Sailing empty to the load port", "level": "vessel", "place": "open_area"},
    "load_port": {"label": "Load port", "group": "Sailing loaded to the discharge port", "level": "order", "place": "load_port"},
    "discharge_port": {"label": "Discharge port", "group": "Sailing loaded to the discharge port", "level": "order", "place": "discharge_port"},
}
"""The three port boxes, and the row column each one's place comes from."""

DISTANCE_PORTS: dict[str, tuple[str, str]] = {
    "distance_empty": ("open_port", "load_port"),
    "distance_loaded": ("load_port", "discharge_port"),
}
"""Each distance input and the ports its leg runs between."""

VOYAGE_FIELDS: list[dict[str, Any]] = [
    *(
        {
            "name": name,
            "kind": "number",
            "label": info.description,
            "default": info.default,
            **(info.json_schema_extra or {}),
        }
        for name, info in VoyageInputs.model_fields.items()
    ),
    *(
        {"name": name, "kind": "port", "label": spec["label"], "group": spec["group"], "level": spec["level"]}
        for name, spec in PORT_FIELDS.items()
    ),
]
"""Every panel field: the 34 inputs in calculator order, then the three port boxes."""

DATA_FIELDS = frozenset({"deadweight", "grain_capacity", "cargo_quantity", "distance_empty", "distance_loaded"})
"""Inputs filled from the rows or the port list, never from a saved default."""

DEFAULT_FIELDS: list[dict[str, Any]] = [
    field for field in VOYAGE_FIELDS if field["kind"] == "number" and field["name"] not in DATA_FIELDS
]
"""Inputs a trader can save their own default for."""


class VoyageDraft(BaseModel):
    """One order and vessel pair: its inputs, where each came from, and what is missing.

    Attributes
    ----------
    order_id : str
        The order.
    vessel_id : str
        The vessel matched to it.
    places : dict of str to str or None
        Per port box, the place as written in the data.
    ports : dict of str to str or None
        Per port box, the port in use; None until one is known.
    port_options : dict of str to list of str
        Per port box, the known ports a listed place names, for the dropdown.
    values : dict of str to float or None
        Every calculator input; None where nothing is known yet.
    sources : dict of str to Source
        Where each input and port came from.
    needed : list of str
        Port boxes and inputs the trader must fill before a result exists.
    problems : list of str
        Messages about the trader's last input, such as an unknown port name.
    result : VoyageResult or None
        Revenue and profit, once nothing is needed.
    """

    order_id: str
    vessel_id: str
    places: dict[str, str | None]
    ports: dict[str, str | None]
    port_options: dict[str, list[str]]
    values: dict[str, float | None]
    sources: dict[str, Source]
    needed: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    result: VoyageResult | None = None


def _number(value: Any) -> float | None:
    """Read a row value as a float.

    Parameters
    ----------
    value : Any
        A JSON-safe value from a row.

    Returns
    -------
    float or None
        The number, or None when empty or not numeric.
    """
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _refresh_distances(draft: VoyageDraft) -> None:
    """Recompute each distance the trader did not type, from the ports in use.

    Parameters
    ----------
    draft : VoyageDraft
        Updated in place.
    """
    for distance_name, (start_key, end_key) in DISTANCE_PORTS.items():
        if draft.sources.get(distance_name) == "trader":
            continue
        start, end = draft.ports[start_key], draft.ports[end_key]
        draft.values[distance_name] = sea_distance(start, end) if start and end else None
        draft.sources[distance_name] = "computed"


def _finish(draft: VoyageDraft, is_calculating: bool = True) -> VoyageDraft:
    """List what is still needed and calculate when nothing is.

    A missing distance is reported as the port boxes it waits on, or as the
    distance itself when both ports are known and no route came back.

    Parameters
    ----------
    draft : VoyageDraft
        Updated in place.
    is_calculating : bool
        False lists what is needed and clears the result without calculating.

    Returns
    -------
    VoyageDraft
        The same draft.
    """
    needed: list[str] = []
    for name, value in draft.values.items():
        if value is not None:
            continue
        missing_ports = [key for key in DISTANCE_PORTS.get(name, ()) if draft.ports[key] is None]
        needed.extend(missing_ports or [name])
    draft.needed = list(dict.fromkeys(needed))
    draft.result = None
    if draft.needed or not is_calculating:
        return draft
    try:
        draft.result = calculate_voyage(VoyageInputs(**draft.values))
    except ValidationError as error:
        labels = {field["name"]: field["label"] for field in VOYAGE_FIELDS}
        draft.problems.extend(
            f"{labels.get(str(detail['loc'][0]), detail['loc'][0])}: {detail['msg']}" for detail in error.errors()
        )
    return draft


def draft_voyage(
    order: dict[str, Any], vessel: dict[str, Any], saved: dict[str, float] | None = None
) -> VoyageDraft:
    """Fill the calculator inputs for one matched order and vessel.

    Deadweight, grain capacity and cargo quantity come from the rows; a port is
    set only when its place names exactly one known port; distances are computed
    from the ports; every other input takes the trader's saved default, else the
    calculator default.

    Parameters
    ----------
    order : dict
        The order's row, with ``order_id``, ``load_port``, ``discharge_port`` and
        the cargo weights.
    vessel : dict
        The vessel's row, with ``vessel_id``, ``dwt``, ``grain_capacity`` and
        ``open_area``.
    saved : dict of str to float, optional
        The trader's saved defaults, keyed by input name.

    Returns
    -------
    VoyageDraft
        The pair's draft, with a result when nothing is needed.
    """
    rows = {"open_area": vessel, "load_port": order, "discharge_port": order}
    places = {key: rows[spec["place"]].get(spec["place"]) for key, spec in PORT_FIELDS.items()}
    options = {key: port_options(place) for key, place in places.items()}
    ports = {key: found[0] if len(found) == 1 else None for key, found in options.items()}

    values: dict[str, float | None] = {name: info.default for name, info in VoyageInputs.model_fields.items()}
    sources: dict[str, Source] = {name: "default" for name in values}
    for name, value in (saved or {}).items():
        if name in values and name not in DATA_FIELDS:
            values[name] = value
            sources[name] = "saved"
    from_rows = {
        "deadweight": _number(vessel.get("dwt")),
        "grain_capacity": _number(vessel.get("grain_capacity")),
        "cargo_quantity": _number(order.get("cargo_weight_max")) or _number(order.get("cargo_weight_min")),
    }
    for name, value in from_rows.items():
        values[name] = value
        sources[name] = "supabase"
    sources.update({key: "supabase" for key, port in ports.items() if port})

    draft = VoyageDraft(
        order_id=str(order.get("order_id")),
        vessel_id=str(vessel.get("vessel_id")),
        places=places,
        ports=ports,
        port_options={key: found for key, found in options.items() if len(found) > 1},
        values=values,
        sources=sources,
    )
    _refresh_distances(draft)
    return _finish(draft)


def apply_trader_edits(
    draft: VoyageDraft,
    ports: dict[str, str | None],
    values: dict[str, float | None],
    is_calculating: bool = True,
) -> VoyageDraft:
    """Fold the trader's port picks and typed values into a draft and recalculate.

    Only entries that differ from the draft count as edits. A port name that is
    not one known port leaves that box needed and adds a problem message.

    Parameters
    ----------
    draft : VoyageDraft
        The draft as last sent to the panel.
    ports : dict of str to str or None
        Port box to the name now in it.
    values : dict of str to float or None
        Input name to the value now in its box.
    is_calculating : bool
        False applies the edits and recomputes distances without calculating,
        leaving the result empty until the trader asks for it.

    Returns
    -------
    VoyageDraft
        A new draft with the edits applied.
    """
    updated = draft.model_copy(deep=True)
    updated.problems = []
    for key, typed in ports.items():
        typed_name = (typed or "").strip()
        if key not in updated.ports or typed_name == (updated.ports[key] or ""):
            continue
        port = find_port(typed_name) if typed_name else None
        if typed_name and port is None:
            updated.problems.append(f'{PORT_FIELDS[key]["label"]}: "{typed_name}" is not in the port list.')
        updated.ports[key] = port[0] if port else None
        updated.sources[key] = "trader"
    for name, value in values.items():
        if name in updated.values and value != updated.values[name]:
            updated.values[name] = value
            updated.sources[name] = "trader"
    _refresh_distances(updated)
    return _finish(updated, is_calculating)


def rank_voyages(drafts: Sequence[VoyageDraft]) -> list[VoyageDraft]:
    """Order one order's drafts: highest profit first, then those needing input.

    Parameters
    ----------
    drafts : Sequence of VoyageDraft
        The drafts of one order.

    Returns
    -------
    list of VoyageDraft
        Drafts with a result by profit, descending, followed by the rest.
    """
    return sorted(
        drafts,
        key=lambda draft: (draft.result is None, -(draft.result.profit if draft.result else 0.0)),
    )
