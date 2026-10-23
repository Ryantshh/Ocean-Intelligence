"""Report region filters match daily breakdown semantics without changing defaults."""
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ai_platform.backend import dashboard_queries as dq
from ai_platform.app.api import dashboard


class ReportScopeTests(unittest.TestCase):
    def test_status_counts_exact_bound_region(self):
        self.assertEqual(dq.status_counts_sql(), ('SELECT * FROM vessel_status_counts', []))
        sql, params = dq.status_counts_sql("East' OR TRUE --")
        self.assertEqual(params, ["East' OR TRUE --"])
        self.assertNotIn(params[0], sql)
        self.assertIn('trim(zone) = $1', sql)
        self.assertIn('unnest(v.parent_zones)', sql)

    def test_evidence_uses_correct_region_attribution(self):
        day = date(2025, 10, 22)
        for metric, expression in [('new_vessels', 'unnest(v.parent_zones)'), ('status_changes', "COALESCE(parent_zone, '')")]:
            with self.subTest(metric=metric):
                sql, params = dq.vessels_on_day_sql(metric, day, direction='down', region='Far East')
                self.assertEqual(params[-1], 'Far East')
                self.assertIn(expression, sql)
                self.assertIn('trim(zone) = $3', sql)
                if metric == 'status_changes':
                    self.assertIn("direction = 'down'", sql)
                    self.assertNotIn('unnest(v.parent_zones)', sql)
                default, params = dq.vessels_on_day_sql(metric, day)
                self.assertNotIn('$3', default)
                self.assertEqual(len(params), 2)
        sql, params = dq.orders_on_day_sql(day, region='Far East')
        self.assertIn("COALESCE(load_zone, '')", sql)
        self.assertIn('trim(zone) = $3', sql)
        self.assertEqual(params[-1], 'Far East')

    def test_routes_forward_exact_region_and_keep_count_defaults(self):
        app = FastAPI(); app.include_router(dashboard.router)
        client = TestClient(app)
        cases = [
            ('/api/dashboard/vessels/status-counts?region=Far%20East', [{'dashboard_status':'OPEN','vessel_count':4}]),
            ('/api/dashboard/vessels/by-day?metric=status_changes&day=2025-10-22&direction=up&region=Far%20East', []),
            ('/api/dashboard/orders/by-day?day=2025-10-22&region=Far%20East', []),
        ]
        for url, rows in cases:
            with self.subTest(url=url), patch.object(dashboard, '_run', AsyncMock(return_value=rows)) as run:
                response=client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertEqual(run.call_args.args[1][-1],'Far East')
                if 'status-counts' in url:
                    self.assertEqual(response.json(), {'OPEN':4,'ON SUBS':0,'FIXED':0,'LIKELY FIXED':0})

if __name__ == '__main__':
    unittest.main()
