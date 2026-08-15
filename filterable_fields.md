# Filterable fields

What the agent can narrow on in SQL, and what it cannot. Anything not filterable still appears
in the results panel, which has a filter dropdown per column.

Date and size fields take two bounds each, so 9 filterable tonnage columns give 13 filter
fields, and 7 filterable orders columns give 9.

## tonnage

9 of 17 columns filterable.

| Column | Filterable | Filter field | Why not |
|---|---|---|---|
| `vessel_id` | Yes | `vessel_ids`, exact, list | |
| `open_date_start` / `open_date_end` | Yes | `open_from` / `open_to` | |
| `eta` | Yes | `eta_from` / `eta_to` | |
| `update_date` | Yes | `updated_from` / `updated_to` | |
| `first_date_received` | Yes | `received_from` / `received_to` | |
| `dwt` | Yes | `dwt_min` / `dwt_max` | |
| `ballast_laden` | Yes | one of LADEN, BALLAST | |
| `commercial_status` | Yes | one of FIXED, ON SUBS, AVAILABLE | |
| `parent_zone` | No | | comma-packed set |
| `open_area` | No | | comma-packed set |
| `destination` | No | | open-ended text |
| `ship_size` | No | | Capesize on every row |
| `ship_type` | No | | Bulk Carrier on 99.8% |
| `vessel_status` | No | | free text |
| `order_id` | No | | matches zero rows in `orders` |
| `assignment` | No | | 100% null |

## orders

7 of 15 columns filterable.

| Column | Filterable | Filter field | Why not |
|---|---|---|---|
| `order_id` | Yes | `order_ids`, exact, list | |
| `laycan_start` / `laycan_end` | Yes | `laycan_from` / `laycan_to` | |
| `date_received` | Yes | `received_from` / `received_to` | |
| `update_date` | Yes | `updated_from` / `updated_to` | |
| `cargo_weight_min` / `cargo_weight_max` | Yes | `weight_min` / `weight_max` | |
| `load_zone` | No | | comma-packed set |
| `discharge_parent_zone` | No | | comma-packed set |
| `load_port` | No | | comma-packed set |
| `discharge_port` | No | | comma-packed set |
| `cargo_type` | No | | comma-packed set |
| `cargo_description` | No | | free prose |
| `assigned` | No | | 100% null |
| `assigned_vessel_name` | No | | 100% null |

## Notes

**Ranges compare against the opposite column** so an overlapping window matches rather than
only a contained one. `laycan_from` compares to `laycan_end`, `open_from` to `open_date_end`,
`weight_min` to `cargo_weight_max`.

**Comma-packed sets hold several values in one varchar.** Neither `=` nor `LIKE` is correct on
them: `=` drops multi-valued rows, and `LIKE '%Asia%'` matches "South East Asia". They wait for
the hybrid index.

**Every condition ANDs.** There is no OR and no negation, and the two enum fields take a single
value while the id fields take a list.

Definitions live in `RANGES` and `EQUALITIES` in `ai_platform/backend/tables/orders.py` and
`tonnage.py`. Change those and this file goes stale.
