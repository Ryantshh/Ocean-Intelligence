"""Sea distances between named ports, using the port list that ships with searoute.

A place resolves to a port only when its name matches exactly one entry in
searoute's ``data/ports.geojson``, ignoring case and surrounding spaces. A name
shared by ports in several countries resolves to nothing, so no port is guessed.
searoute itself takes coordinates and always returns some route, which is why the
name check happens here first.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

import searoute

PortEntry = tuple[str, float, float]
"""A port's display name, longitude and latitude."""


def _load_ports() -> dict[str, list[PortEntry]]:
    """Read searoute's port list, grouped by lower-case name.

    Returns
    -------
    dict of str to list of PortEntry
        Every port under each name; a name with more than one entry is ambiguous.
    """
    collection = json.loads((files("searoute") / "data" / "ports.geojson").read_text(encoding="utf-8"))
    ports_by_name: dict[str, list[PortEntry]] = {}
    for feature in collection["features"]:
        name = feature["properties"]["name"].strip()
        longitude, latitude = feature["geometry"]["coordinates"]
        ports_by_name.setdefault(name.lower(), []).append((name, longitude, latitude))
    return ports_by_name


PORTS_BY_NAME = _load_ports()

PORT_NAMES: list[str] = sorted(
    (entries[0][0] for entries in PORTS_BY_NAME.values() if len(entries) == 1), key=str.lower
)
"""Names that resolve to exactly one port, for the panel's suggestions."""


def find_port(place: str | None) -> PortEntry | None:
    """Resolve one place name to a single known port.

    Parameters
    ----------
    place : str or None
        A place as written in the data or typed by the trader.

    Returns
    -------
    PortEntry or None
        The port, or None when the name is empty, unknown or shared by several ports.
    """
    entries = PORTS_BY_NAME.get((place or "").strip().lower(), [])
    return entries[0] if len(entries) == 1 else None


def port_options(place: str | None) -> list[str]:
    """List the known ports named in a place, which may be a comma-separated list.

    Parameters
    ----------
    place : str or None
        A place as written in the data, such as ``"Dampier, Port Walcott"``.

    Returns
    -------
    list of str
        Display names of the parts that resolve to one port, in the order written.
    """
    return list(dict.fromkeys(port[0] for part in (place or "").split(",") if (port := find_port(part))))


@lru_cache(maxsize=4096)
def sea_distance(origin: str, destination: str) -> float | None:
    """Sea distance in nautical miles between two port names.

    Parameters
    ----------
    origin : str
        Port the leg starts from.
    destination : str
        Port the leg ends at.

    Returns
    -------
    float or None
        Nautical miles, rounded to one decimal; None when either name is not one
        known port.
    """
    start, end = find_port(origin), find_port(destination)
    if start is None or end is None:
        return None
    if start[1:] == end[1:]:
        return 0.0
    route = cast("dict[str, Any]", searoute.searoute([start[1], start[2]], [end[1], end[2]], units="naut"))
    return round(float(route["properties"]["length"]), 1)
