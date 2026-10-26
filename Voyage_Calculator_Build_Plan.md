# Voyage Calculator Build Plan

How to build what `Voyage_Calculator_Requirements.md` describes. Each phase ends with a check that has to pass before the next phase starts. The checks are scratchpad scripts or build commands; no `tests/test_*.py` files are added.

Before starting, pull `main`, which is one commit ahead (#34, orders are identified by `order_id` plus `order_seq`), and branch from it.

## Phase 1: The calculator

**File:** `ai_platform/backend/voyage.py` (new)

- `VoyageInputs`: a Pydantic model with the 34 fields, using the defaults and units from `Voyage_Calculator_Field_Sources.md`. It sets `extra="forbid"`, uses `gt=0` on rates, speeds and capacities, and `ge=0` on costs and commissions. Commissions are stored as fractions (0.0375), and the panel shows them as percentages.
- `VoyageResult`: `revenue`, `profit`.
- `calculate_voyage(inputs: VoyageInputs) -> VoyageResult`: Steps 1 to 10, as plain arithmetic.
- `VOYAGE_FIELDS`: one entry per field with `name`, `label`, `unit`, `group`, `level` (`"order"` or `"vessel"`) and `default`. The labels and groups match the field-source table.

**Which fields are order fields and which are vessel fields**

| Level | Fields |
|---|---|
| Order | cargo quantity, stowage factor, freight rate, both freight commissions, loading and discharging rates, both turn times, idle days, distance sailing loaded, both fuel prices, both port charges, cables/entertainment/victualling, payment in lieu of hold cleaning, agency fee for taking on fuel |
| Vessel | deadweight, grain capacity, both speeds, all eight fuel burns, daily hire rate, address commission on hire, distance sailing empty, days taking on fuel |

**Check:** `calculate_voyage(VoyageInputs())` gives revenue 1,100,000.00 and profit 456,915.94, to the cent.

## Phase 2: Ports and distances

**Dependency:** add `searoute==1.6.0` to `pyproject.toml`. The version is pinned, so the port list only changes when we upgrade on purpose. It also brings in `geojson` and `networkx`.

**File:** `ai_platform/backend/distances.py` (new)

- `PORTS_BY_NAME: dict[str, list[tuple[float, float]]]`: built once at import from `searoute/data/ports.geojson`, read with `importlib.resources`. Each key is a port name trimmed and in lower case, and each value is every set of coordinates under that name.
- `PORT_NAMES: list[str]`: the port names that belong to exactly one port, sorted, for the panel's suggestions.
- `port_coordinates(place: str | None) -> tuple[float, float] | None`: returns coordinates only when the name matches exactly one port.
- `port_options(place: str | None) -> list[str]`: splits the place on commas and keeps the parts that `port_coordinates` knows. These fill the dropdown.
- `sea_distance(origin: str, destination: str) -> float | None`: nautical miles from `searoute.searoute(..., units="naut")`. It returns None when either end isn't a known port, and is cached with `functools.lru_cache`.

`searoute` does its route search on the processor, not over the network. Callers in async code run it through `asyncio.to_thread`, so the chat isn't blocked while it works.

**Check:** a scratchpad script confirms:
- the match counts are the same as in the requirements (`open_area` 6,330, `load_port` 1,022, `discharge_port` 856);
- `port_options("Dampier, Port Walcott")` returns both ports;
- `port_coordinates("San Nicolas")` is None;
- Richards Bay to Qingdao gives a sensible distance (roughly 6,700 to 7,000 nautical miles).

## Phase 3: Drafts and ranking

**File:** `ai_platform/backend/voyage.py`

- `VoyageDraft`: `order_id`, `vessel_id`, `places` (the original text for the open area, load port and discharge port), `ports` (the port in use, or None), `port_options`, `values`, `sources`, `needed`, `result`.
- `draft_voyage(order: dict, vessel: dict) -> VoyageDraft`:
  - Fills deadweight and grain capacity from the vessel row, and cargo quantity from `cargo_weight_max`, falling back to `cargo_weight_min`.
  - Sets each port when the place resolves to exactly one known port, either on its own or as the only known port in a list.
  - Works out both distances from the ports it has.
  - Fills everything else with its default.
  - Lists every blank field in `needed`, and runs `calculate_voyage` only when `needed` is empty.
- `apply_trader_edits(draft: VoyageDraft, ports: dict, values: dict) -> VoyageDraft`:
  - Applies the trader's port picks, then works the distances out again.
  - Applies the trader's typed values with the source `trader`.
  - Checks everything with `VoyageInputs`, and then calculates.
  - A typed port name that isn't in `PORTS_BY_NAME` stays needed and is reported back.
- `rank_voyages(drafts: Sequence[VoyageDraft]) -> list[VoyageDraft]`: drafts with a result come first, by profit from highest to lowest, then the drafts that need input.

**Check:** a scratchpad script runs `draft_voyage` on real rows from Supabase. It covers four cases:
- a clean pair, which gets a result;
- a "China" discharge port, which needs the discharge port;
- a "Dampier, Port Walcott" load port, which gets a dropdown and no pick;
- an order with no cargo weight, which needs the cargo quantity.

## Phase 4: The matching tool

**File:** `ai_platform/backend/matching.py`

- `tables.py`: add `grain_capacity` to `TONNAGE.display_columns`, so the matcher's vessel rows carry it. It also shows up as a column in the vessels table.
- In `match_orders`, after `asyncio.gather`:
  1. For each order, call `draft_voyage(rows[order_id], vessel)` for every matched vessel, through `asyncio.to_thread`.
  2. Rank each order's drafts with `rank_voyages`.
- The `match_rows` event gains `voyages: {order_id: [ranked drafts]}` and `fields: VOYAGE_FIELDS`.
- `_report` lists the vessels in ranked order with their revenue and profit, then one line saying how many vessels need input. The agent's reply therefore follows the ranking and stays short.

**Check:** run `match_orders` from a scratchpad script against two real orders. Confirm the drafts and the ranking are in the event, and that the report is ranked.

## Phase 5: Chainlit wiring

**File:** `ai_platform/app/cl_app.py`

- `_render_match_event` copies each order's `voyages` and the shared `fields` into that order's panel set, next to the existing `columns` and `rows`.
- New action `@cl.action_callback("calculate_voyages")`:
  - It receives `{order_id, order: {ports, values}, vessels: [{vessel_id, draft, ports, values}]}` and runs `apply_trader_edits` and `rank_voyages`.
  - It **returns** the ranked drafts. Chainlit's `/project/action` hands that return value straight back to the panel, so there's no session lookup and no element update on the server.
  - The language model isn't called.
- New action `@cl.action_callback("port_names")`: returns `PORT_NAMES`. The panel fetches the list once, instead of carrying about 50 KB of names in every saved panel.

**Check:** `uv run ruff check` passes, and the app starts.

## Phase 6: The right panel

**File:** `ai_platform/public/elements/Results.jsx`

When a set has `voyages`, its tab shows the following instead of only the table:

1. **Ranking**: rank, vessel, revenue, profit. Vessels that need input are listed below with a count of missing fields. Clicking a row opens that vessel's tab.
2. **Order fields**: the `level: "order"` fields from `fields`, plus the load port and discharge port boxes. Each box shows the original place from the data, a dropdown when there are `port_options`, and a free box with a `<datalist>` of `PORT_NAMES` otherwise.
3. **Vessel tabs**: styled the same as the order tabs (`oi-t-tab`). A dot marks a vessel that needs input.
4. **Vessel fields**: the `level: "vessel"` fields, plus the open port box.
5. **Calculate**: `await callAction({name: "calculate_voyages", payload})`. The panel then replaces the drafts with the ranked ones it gets back, and saves them into the element with `updateElement({...props, sets})`, so a page reload keeps the trader's edits and the ranking.

Every field shows a source tag: Supabase, Computed, Default or Edited. A needed field that is still blank is outlined in red. The existing vessels table stays under the ranking, collapsed.

**Check:** the element builds without errors. The trader tests the panel in the browser.

## Order of work and size

| Phase | Files | Rough size |
|---|---|---|
| 1 Calculator | `voyage.py` | ~150 lines |
| 2 Distances | `distances.py`, `pyproject.toml` | ~80 lines |
| 3 Drafts and ranking | `voyage.py` | ~150 lines |
| 4 Matching | `matching.py`, `tables.py` | ~40 lines changed |
| 5 Chainlit | `cl_app.py` | ~60 lines |
| 6 Right panel | `Results.jsx` | ~300 lines |

Phases 1 to 3 have no user interface and can be checked on their own. Phase 6 is the largest and the only one that needs the trader to test it.

## Still open

- Ballast leg: should it stop at `open_area`, or should a vessel that is still loaded first add the leg from `open_area` to `destination`? The plan stops at `open_area`.
