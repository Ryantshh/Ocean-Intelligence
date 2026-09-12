"""Fixed identifiers and bound values only. SQL performs candidate filtering."""
from freight_ai.data.supabase import ORDER_FIELDS, VESSEL_FIELDS


def compile_search(intent, as_of):
    vessel = intent.dataset == 'tonnage'
    fields = VESSEL_FIELDS if vessel else ORDER_FIELDS
    table = 'tonnage_test' if vessel else 'order_test'
    received = 'first_date_received' if vessel else 'date_received'
    start, end = ('open_date_start','open_date_end') if vessel else ('laycan_start','laycan_end')
    params, horizon, clauses = [], [], []
    def bind(value):
        params.append(value)
        return f'${len(params)}'
    if not intent.include_future:
        horizon.append(f'update_date::date <= {bind(as_of)}')
        horizon.append(f'{received}::date <= {bind(as_of)}')
    horizon.extend(['update_date IS NOT NULL', f'{received} IS NOT NULL'])
    source = f'SELECT {fields} FROM public.{table} WHERE ' + ' AND '.join(horizon)
    if vessel and not intent.include_history:
        # Keep every tied latest report so the canonical engine can flag conflicts.
        source = f'SELECT *, dense_rank() OVER (PARTITION BY lower(trim(vessel_id)) ORDER BY update_date DESC, first_date_received DESC) AS latest_rank FROM ({source}) eligible'
        clauses.append('latest_rank = 1')
    for field, column, op in [
        ('received_from',received,'>='),('received_to',received,'<='),
        ('updated_from','update_date','>='),('updated_to','update_date','<='),
        ('start_from',start,'>='),('start_to',start,'<='),('end_to',end,'<='),
        ('window_start',end,'>='),('window_end',start,'<=')]:
        value=getattr(intent,field)
        if value is not None:
            clauses.append(f'{column}::date {op} {bind(value)}')
    for field,column,op in [('min_tonnes','dwt' if vessel else 'cargo_weight_max','>='),('max_tonnes','dwt' if vessel else 'cargo_weight_min','<=')]:
        value=getattr(intent,field)
        if value is not None:
            clauses.append(f'{column} {op} {bind(value)}')
    # Text/ID comparisons stay in the canonical engine for identical Unicode/null semantics.
    sql=f'SELECT {fields} FROM ({source}) candidates'
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    return sql + ' ORDER BY 1 LIMIT 100001', params
