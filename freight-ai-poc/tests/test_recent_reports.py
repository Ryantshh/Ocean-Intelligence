from datetime import date, datetime

from freight_ai.data.models import Order, Provenance
from freight_ai.inference.chat import obvious_data_intent
from freight_ai.matching.query import query


def test_past_week_uses_update_dates_inclusively():
    intent = obvious_data_intent('show me orders from past week', {}, as_of=date(2026, 9, 1))
    assert intent.updated_from == date(2026, 8, 26)
    rows = [Order(record_id=str(day), source=Provenance(file='test', sheet='test', row=day, sha256='test'),
                  update_date=datetime(2026, 8, day), laycan_start=date(2026, 9, 10))
            for day in [25, 26, 31]]
    assert query(rows, intent)['total_count'] == 2
    assert obvious_data_intent('show me orders from past week', {}, as_of=date(2026, 10, 1)).updated_to == date(2026, 10, 1)
