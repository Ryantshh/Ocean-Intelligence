# Filterable fields

What the agent can narrow on in SQL, and what it cannot. The source of truth is
`ai_platform/backend/tables.py`: the `OrderSearch` and `VesselSearch` models hold the filter
fields, and the `ORDERS` and `TONNAGE` specs map each one to a column. Anything not filterable
still appears in the results panel, which has a filter dropdown per column.

How a field matches:

- **range** — `_from` is on or after, `_to` is on or before; `_min` is at least, `_max` is at most.
- **exact, per element** — the stored cell is split on `", "` and each piece must equal the value,
  so `West Africa, East Coast South America` matches a search for either zone.
- **prefix, per element** — each piece must start with the value, so `IRON ORE` also finds
  `IRON ORE PELLETS`.
- **contains, per element** — each piece must contain the value, case-insensitive, so `Itaguai`
  finds `Itaguai / Sepetiba`.
- **by meaning** — the text is embedded with Cohere and ranked by vector distance. Capped at the
  closest fifty unless `exhaustive` is set.

## order_test

All 13 displayed columns are filterable.

| Column | Filter field | Match |
|---|---|---|
| `order_id` | `order_ids` | exact, list |
| `laycan_start` | `laycan_start_from` / `laycan_start_to` | range |
| `laycan_end` | `laycan_end_from` / `laycan_end_to` | range |
| `date_received` | `received_from` / `received_to` | range |
| `update_date` | `updated_from` / `updated_to` | range |
| `cargo_weight_min` | `weight_min` | range, at least |
| `cargo_weight_max` | `weight_max` | range, at most |
| `load_zone` | `load_zone` | exact, per element |
| `discharge_parent_zone` | `discharge_parent_zone` | exact, per element |
| `cargo_type` | `cargo_type` | prefix, per element |
| `load_port` | `load_port` | contains, per element |
| `discharge_port` | `discharge_port` | contains, per element |
| `cargo_description` | `cargo_description` | by meaning |

Not selected at all: `assigned` (100% null) and the pipeline columns `embedding_source_hash`,
`gold_loaded_at` and the `*_embedding` vectors.

## tonnage_test

11 of 16 displayed columns are filterable.

| Column | Filter field | Match |
|---|---|---|
| `vessel_id` | `vessel_ids` | exact, list |
| `open_date_start` | `open_start_from` / `open_start_to` | range |
| `open_date_end` | `open_end_from` / `open_end_to` | range |
| `update_date` | `updated_from` / `updated_to` | range |
| `first_date_received` | `received_from` / `received_to` | range |
| `dwt` | `dwt_min` / `dwt_max` | range |
| `ballast_laden` | `ballast_laden` | exact, one of LADEN, BALLAST |
| `commercial_status` | `commercial_status` | exact, one of FIXED, ON SUBS, OPEN |
| `parent_zone` | `parent_zone` | exact, per element |
| `vessel_status` | `vessel_status` | exact, per element |
| `open_area` | `open_area` | contains, per element |

`commercial_status` is folded before it is compared or shown: FIXED and ON SUBS pass through,
and anything else, including null, reads as OPEN.

Displayed but not filterable:

| Column | Why not |
|---|---|
| `destination` | AIS free text typed by the crew |
| `eta` | AIS estimate for that destination, not the open window |
| `ship_size` | Capesize on every row |
| `ship_type` | Bulk Carrier on 99.8% |
| `order_id` | synthetic link, see Data caveats in the README |

Nothing on vessels is searched by meaning.

## Flags

| Flag | Table | Effect |
|---|---|---|
| `include_future` | both | lifts the future cutoff |
| `include_history` | vessels | returns every report instead of the newest per vessel |
| `exhaustive` | cargoes | returns every `cargo_description` match instead of the closest fifty |

## Rules applied to every search

- **Future cutoff.** Rows stamped after the working date are hidden: `date_received` for orders,
  `update_date` for tonnage. The working date is the real date minus one calendar year.
- **Newest report per vessel.** Tonnage keeps one row per `vessel_id`, the newest `update_date`
  then the newest `first_date_received`, nulls last. Every vessel filter is judged on that row.
- **Ordering.** Both tables sort by `update_date` descending, except a `cargo_description`
  search, which sorts by distance.
