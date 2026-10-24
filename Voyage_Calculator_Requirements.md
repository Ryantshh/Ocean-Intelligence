# Voyage Calculator Requirements

## Goal

When `match_orders` matches vessels to orders, it should also work out the voyage profit for every matched vessel. The trader should be able to check and edit the inputs for each vessel in the right panel, and then get back a ranked list of vessels for each order.

Every formula comes from `Voyage_Calculator_Field_Sources.md`, Steps 1 to 10. Where each field comes from is in the table in that same file.

## What the data in Supabase supports (checked 2026-10-20)

| Column | Filled | Problem rows | What happens to the problem rows |
|---|---|---|---|
| `tonnage_test.dwt` | 11,105 of 11,105 | 0 | — |
| `tonnage_test.grain_capacity` | 11,087 of 11,105 | 18 empty | Trader types it in |
| `tonnage_test.open_area` | 11,105 of 11,105 | 197 hold a list of places (contain a comma) | Trader types the open port name, distance sailing empty is then computed |
| `order_test.load_port` | 1,864 of 1,864 | 206 hold a list of places | Trader types the load port name, both distances are then computed |
| `order_test.discharge_port` | 1,810 of 1,864 | 54 empty, 333 hold a list of places | Trader types the discharge port name, distance sailing loaded is then computed |
| `order_test.cargo_weight_min` / `cargo_weight_max` | 1,814 of 1,864 | 50 have both empty | Trader types the cargo quantity in |

Some names are areas, not ports, for example `China`, `Far East`, `Japan` and `Guinea`. They are not in the port list, so the trader types a port name and the distance is computed from it.

The build works with the data currently in Supabase. The pipeline is not changed.

## Rules

1. **Cargo quantity** is `cargo_weight_max`. If that is empty, use `cargo_weight_min`. If both are empty, the trader types it in.
2. **Distances** are only worked out when both ends are a single name found in the port list. Otherwise the port name box is left blank and marked as needed. The trader types a port name from the list, and the distance is computed from it. The agent never guesses a port. The trader can still overwrite a computed distance.
3. **A list of places** (for example "Dampier, Port Walcott") comes from the broker and means the port is not decided yet. The list is split on the commas, and each part is checked against the port list. The port box becomes a dropdown of only the parts that are known ports. The trader must pick one first, and nothing is picked by default. The only exception is a list with exactly one known port, which is filled in. Parts that are not ports (for example "Japan") are left out, and the trader can still type any port from the full list.
4. **Several stops in one order** (loading in parts at more than one port) are not modelled. The calculator has one load port and one discharge port, so the trader adds the extra stop's time and charges through idle days and port charges.
5. **Stowage factor** is prefilled with 1.33, and the trader can edit it. There is no lookup by cargo type.
6. **Speeds and all other fields not in Supabase** are prefilled with the calculator defaults, and the trader can edit them.
7. **A vessel with any needed field still blank** has no profit. It is listed below the ranked vessels as "Needs input".
8. **Ranking** is by profit, highest first. Only revenue and profit are shown.

## Backend

### New module `ai_platform/backend/voyage.py`

```python
class VoyageInputs(BaseModel):
    """The 34 calculator inputs, each with its calculator default."""
    model_config = ConfigDict(extra="forbid")
    deadweight: float = Field(62_000, gt=0)
    grain_capacity: float = Field(70_000, gt=0)
    speed_empty: float = Field(14, gt=0)
    ...
    agency_fee_taking_on_fuel: float = Field(1_500, ge=0)


class VoyageResult(BaseModel):
    """What one voyage earns."""
    revenue: float
    profit: float


class VoyageDraft(BaseModel):
    """One order-vessel pair's inputs, where each came from, and what is still needed."""
    order_id: str
    vessel_id: str
    ports: dict[str, str | None]
    port_options: dict[str, list[str]]
    values: dict[str, float | None]
    sources: dict[str, Literal["supabase", "computed", "default", "trader"]]
    needed: list[str]
    result: VoyageResult | None


def calculate_voyage(inputs: VoyageInputs) -> VoyageResult: ...
def draft_voyage(order: dict[str, Any], vessel: dict[str, Any]) -> VoyageDraft: ...
def rank_voyages(drafts: Sequence[VoyageDraft]) -> list[VoyageDraft]: ...

VOYAGE_FIELDS: tuple[FieldInfo, ...]
"""Per field: name, label, unit, group, level ("order" or "vessel"), default."""
```

- `calculate_voyage` is a pure function that follows Steps 1 to 10. It does no database or network calls.
- `draft_voyage` fills the values from the order row and vessel row (rules 1 to 6), records the source of every field, and sets `ports` to the open, load and discharge port names that matched the port list (None where not or where a list has several known ports). `port_options` holds, for each of the three, the known ports found in a list, for the dropdown. It works out the two distances from those ports. If nothing is needed, it also runs `calculate_voyage`.
- `rank_voyages` sorts the drafts that have a result by profit, and puts the "Needs input" drafts last.
- `VOYAGE_FIELDS` is sent to the panel, so the JavaScript holds no field names, labels or defaults of its own. The existing `results_props` follows the same pattern.

### Distances: `ai_platform/backend/distances.py`

```python
def sea_distance(origin: str | None, destination: str | None) -> float | None:
    """Nautical miles by sea between two port names, or None when either is not one known port."""
```

- Port coordinates come from the port list inside the `searoute` package (`searoute/data/ports.geojson`, 3,962 ports, Apache 2.0 licence), read at startup. There is no separate port file and no typed coordinates. A name that belongs to ports in several countries, such as `San Nicolas` or `Newcastle`, counts as not one known port.
- Rows where a distance can be worked out: `open_area` 6,330 of 11,105, `load_port` 1,022 of 1,864, `discharge_port` 856 of 1,864. The rest are areas, lists of places, or ports missing from the list (for example `Saldanha Bay`, `COGH`, `Dhamra`). For those the trader types a port name from the list.
- A name matches only when it is equal to a port name, ignoring capital letters. There is no fuzzy matching, because a wrong port is worse than asking the trader.
- `PORT_NAMES`, the sorted list of port names, is sent to the panel once for the port name suggestions.
- The route comes from the `searoute` package (`searoute(origin, destination, units="naut")`), which is a new dependency.
- The results are cached with `functools.lru_cache`, because the same port pairs come up again and again.

### Changes to `ai_platform/backend/matching.py`

- Add `grain_capacity` to the vessel rows the matcher reads. Today `TONNAGE.display_columns` doesn't include it.
- Inside `match_orders`, after `asyncio.gather`, call `draft_voyage` for each order and vessel pair, then `rank_voyages` for each order.
- The `match_rows` stream event gains a `voyages` list for each order, holding the ranked drafts, plus `VOYAGE_FIELDS` once.
- `_report` lists the vessels in ranked order with the revenue and profit, so the agent's reply follows the ranking. Vessels that need input are counted, not listed one by one.

### New Chainlit action in `ai_platform/app/cl_app.py`

```python
@cl.action_callback("calculate_voyages")
async def calculate_voyages(action: cl.Action) -> None:
    """Recalculate one order's voyages from the trader's edited inputs and update the panel."""
```

- The payload is `{order_id, ports: {load_port, discharge_port}, voyages: [{vessel_id, open_port, values}]}`.
- Port names the trader typed are checked against the port list, and the distances are computed with `sea_distance`. A name not in the list stays marked as needed, and the reply says which name was not found.
- Each set of values is checked with `VoyageInputs`. Fields the trader changed get the source `trader`. The callback then calculates and ranks, and returns the ranked drafts straight to the panel. The panel saves them with `updateElement`, so a reload keeps them.
- The agent and the language model aren't involved, so recalculating costs no tokens and returns straight away.

## Frontend: the right panel (`ai_platform/public/elements/Results.jsx`)

```
┌ Order 1234 │ Order 5678 ┐                       ← existing order tabs
├────────────────────────────────────────────────┤
│ Ranking                                         │
│  1  VESSEL 0887   revenue 1,100,000  profit 456,916 │
│  2  VESSEL 0412   revenue 1,042,000  profit 401,220 │
│  –  VESSEL 0903   Needs input (2 fields)        │
├────────────────────────────────────────────────┤
│ Order fields (same for every vessel)            │
│  Cargo quantity, Stowage factor, Freight rate,  │
│  Commissions, Loading/Discharging rate, Turn    │
│  times, Idle days, Load port [Richards Bay   ], │
│  Discharge port [ China → type a port ▾ ],     │
│  Distance sailing loaded,                       │
│  Fuel prices, Port charges, Other lump sums     │
├────────────────────────────────────────────────┤
│ [VESSEL 0887] [VESSEL 0412] [VESSEL 0903 •]     │ ← vessel tabs, dot = needs input
│ Vessel fields                                   │
│  Deadweight, Grain capacity, Speeds, Fuel burn, │
│  Daily hire rate, Address commission on hire,   │
│  Open port [ COGH → type a port ▾ ],            │
│  Distance sailing empty, Days taking on fuel    │
├────────────────────────────────────────────────┤
│                         [ Calculate ]           │
└────────────────────────────────────────────────┘
```

- **Two levels of fields.** Fields that are the same for every vessel on an order (freight, cargo, port, prices) are typed once per order tab. Only vessel fields change when tabbing between vessels. Without this split, the trader would retype freight rate and fuel prices for every vessel.
- **Vessel tabs** look and behave the same as the order tabs. A dot marks a vessel that still needs input. Clicking a row in the ranking opens that vessel's tab.
- **Each field shows its source** as a small tag: "Supabase", "Computed", "Default" or "Edited". A needed field that is still blank is outlined in red.
- **Port name boxes** show the place from the data (for example "China") next to the box. When the place is a list with known ports in it, the box is a dropdown of those ports and stays blank until the trader picks one. Suggestions come from `PORT_NAMES` through a plain `<datalist>`, so no extra library is needed. The load port and discharge port boxes are order fields, typed once per order. The open port box is a vessel field, because each vessel opens somewhere different.
- **Calculate** sends every vessel of the current order through `callAction({name: "calculate_voyages", payload})`. The ranking refreshes when the updated props arrive.
- **Edits stay** when switching vessel or order tabs, because they are held in component state keyed by order and vessel. They also survive a page reload, because the callback writes them into the element's props.
- **The existing vessel table** stays available in each order tab, collapsed under the ranking.

## Build order

1. `voyage.py` with `VoyageInputs`, `VoyageResult` and `calculate_voyage`. Check it with a scratchpad script that the defaults give a profit of 456,915.94.
2. `distances.py`.
3. `draft_voyage`, `rank_voyages` and `VOYAGE_FIELDS`.
4. `matching.py`: add `grain_capacity` to the vessel rows, build the drafts and ranking, extend the `match_rows` event, and rank the report.
5. `cl_app.py`: the `calculate_voyages` action and storing the panel element.
6. `Results.jsx`: the ranking, the order fields, the vessel tabs, the vessel fields and the Calculate button. Run a build check only; the trader tests the user interface.

## Open questions

- Should the ballast leg stop at `open_area`, or should vessels that are still loaded first add the leg from `open_area` to `destination`? The plan uses `open_area` only.
