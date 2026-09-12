from datetime import date, datetime
from freight_ai.data.models import Intent, Order, Provenance
from freight_ai.inference.history_store import HistoryStore
from freight_ai.inference.search_language import parse_search
from freight_ai.matching.query import query, text_matches


def test_received_is_not_updated():
    intent = parse_search('show orders received in the past 7 days', date(2026,9,1))
    record = Order(record_id='1', source=Provenance(file='x',sheet='x',row=1,sha256='x'), date_received=datetime(2026,8,1), update_date=datetime(2026,9,1))
    assert query([record],intent)['total_count'] == 0
    assert query([record],parse_search('show orders updated in the past 7 days',date(2026,9,1)))['total_count'] == 1


def test_local_text_matching_does_not_invent_geography():
    assert text_matches('Tubarão, Brazil', 'Tubarao', 'normalized')
    assert not text_matches('Tubarão, Brazil','Asia','normalized')
    assert not text_matches('Tubarão, Brazil','Tubarao','exact')


def test_local_history_roundtrip(tmp_path):
    store=HistoryStore(tmp_path/'history.db')
    payload={'state':{'cargo_fraction':0.95},'messages':[{'role':'user','content':'hello'}]}
    key=store.save(payload)
    assert store.load(key)==payload
    assert len(store.list())==1


def test_history_intent():
    intent=parse_search('show history for VESSEL 0001',date(2026,9,1))
    assert intent.include_history and intent.text_filters == {'vessel_name':'vessel 0001'}
