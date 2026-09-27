"""One query definition for generation and decision-time evidence checks."""

ORDER_SQL = """SELECT order_id::text AS order_id, load_port, load_zone,
 cargo_weight_min, laycan_start, laycan_end FROM public.order_test
 WHERE order_id::text=$1 AND update_date::date <= $2"""
VESSELS_SQL = """SELECT DISTINCT ON (vessel_id) vessel_id, tonnage_row_key, dwt,
 commercial_status, parent_zone, open_area, open_date_start, open_date_end, update_date
 FROM public.tonnage_test WHERE update_date::date <= $1
 ORDER BY vessel_id, update_date DESC, tonnage_row_key DESC"""
VESSEL_SQL = """SELECT vessel_id, tonnage_row_key, dwt, commercial_status,
 parent_zone, open_area, open_date_start, open_date_end, update_date
 FROM public.tonnage_test WHERE vessel_id=$1 AND update_date::date <= $2
 ORDER BY update_date DESC, tonnage_row_key DESC LIMIT 1"""
