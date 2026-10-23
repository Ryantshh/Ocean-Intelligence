import re
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_platform.app.api import manager_report as module


class ManagerReportTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(module.router)
        self.client = TestClient(app)
        self.clock = {"orders_now": "2025-10-22T12:00:00", "tonnage_now": "2025-10-22T12:00:00"}

    def test_default_completed_week_and_parameterized_region(self):
        run = AsyncMock(side_effect=[[self.clock], [{"orders": 0}], [], [], [], []])
        with patch.object(module, '_run', run):
            response = self.client.get('/api/manager-report', params={'region': "Far East'"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['start'], '2025-10-15')
        self.assertEqual(response.json()['end'], '2025-10-21')
        self.assertEqual(response.json()['previous_start'], '2025-10-08')
        for call in run.call_args_list[1:]:
            self.assertNotIn("Far East'", call.args[0])
            self.assertEqual(call.args[1][-1], "Far East'")
            self.assertEqual({int(n) for n in re.findall(r"\$(\d+)", call.args[0])}, set(range(1, len(call.args[1]) + 1)))

    def test_invalid_ranges_do_not_query_orders(self):
        for start,end in [('2025-10-22','2025-10-22'), ('2025-10-20','2025-10-19'), ('2025-01-01','2025-10-21')]:
            with patch.object(module, '_run', AsyncMock(return_value=[self.clock])) as run:
                self.assertEqual(self.client.get('/api/manager-report', params={'start':start,'end':end}).status_code,422)
                self.assertEqual(run.await_count,1)


    def test_thirty_day_preset(self):
        with patch.object(module, '_run', AsyncMock(side_effect=[[self.clock], [{}], [], [], [], []])):
            response = self.client.get('/api/manager-report?days=30')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['start'], '2025-09-22')
        self.assertEqual(response.json()['previous_end'], '2025-09-21')

    def test_evidence_is_bounded_and_preserves_identifiers(self):
        record = {"order_id": "99158881986728231", "order_seq": 2, "total_count": 31}
        with patch.object(module, '_run', AsyncMock(side_effect=[[self.clock], [record]])) as run:
            response = self.client.get('/api/manager-report/evidence?start=2025-10-15&end=2025-10-21&issue=missing_weight&offset=25')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rows'][0]['order_id'], record['order_id'])
        self.assertEqual(response.json()['total'], 31)
        sql, params = run.call_args.args
        self.assertIn('LIMIT 25 OFFSET $4', sql)
        self.assertIn('cargo_weight_min IS NULL', sql)
        self.assertEqual(params[-1], 25)

    def test_unknown_evidence_filter_rejected_before_query(self):
        with patch.object(module, '_run', AsyncMock()) as run:
            response = self.client.get('/api/manager-report/evidence?start=2025-10-15&end=2025-10-21&issue=drop')
        self.assertEqual(response.status_code, 422)
        run.assert_not_called()
