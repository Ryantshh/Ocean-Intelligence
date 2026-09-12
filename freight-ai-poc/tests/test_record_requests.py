from datetime import date
from freight_ai.inference.search_language import parse_search
from freight_ai.inference.record_response import record_response


def test_laycan_request_is_a_start_date_filter():
    intent = parse_search('Show cargo orders whose laycan starts between 1 and 15 January 2025',date(2026,9,1))
    assert intent.dataset == 'orders'
    assert intent.start_from == date(2025,1,1)
    assert intent.start_to == date(2025,1,15)
    assert intent.window_start is None


def test_semantic_request_targets_cargo_description():
    intent = parse_search('Find cargoes similar to steelmaking raw materials',date(2026,9,1))
    assert intent.dataset == 'orders'
    assert intent.text_filters == {'cargo_description':'steelmaking raw materials'}
    assert intent.min_tonnes is None


def test_historical_dwt_query_keeps_both_constraints():
    intent = parse_search('Show vessel reports with at least 180,000 tonnes DWT, including historical reports.',date(2026,9,1))
    assert intent.include_history
    assert intent.min_tonnes == 180000
    assert intent.dataset == 'tonnage'


def test_table_uses_only_returned_fields_and_escapes_cells():
    intent = parse_search('Find cargoes similar to steelmaking raw materials',date(2026,9,1))
    result = {'records':[{'record_id':'order-1','cargo_type':'Ore | test'}], 'total_count':1,'as_of':'2026-09-01'}
    text = record_response(result,intent)
    assert '| Order ID |' in text and 'Ore \\| test' in text
    assert 'VESSEL' not in text and 'Not provided' in text
