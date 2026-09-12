from datetime import date
from freight_ai.data.models import Intent
from freight_ai.data.sql_search import compile_search


def test_database_bounds_are_bound_and_latest_precedes_filter():
    sql, params = compile_search(Intent(action='query', dataset='tonnage', min_tonnes=180000),date(2026,9,1))
    assert '180000' not in sql and 180000 in params
    assert 'dense_rank()' in sql and 'latest_rank = 1' in sql
    assert sql.startswith('SELECT ') and 'public.tonnage_test' in sql


def test_history_and_future_are_explicit():
    sql,params=compile_search(Intent(action='query',dataset='tonnage',include_history=True,include_future=True),date(2026,9,1))
    assert 'dense_rank' not in sql and not params
    assert 'IS NOT NULL' in sql
