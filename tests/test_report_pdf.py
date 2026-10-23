import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_platform.app.api.report_pdf import router


class ReportPdfTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.snapshot = {
            'start': '2025-10-15', 'end': '2025-10-21',
            'previous_start': '2025-10-08', 'previous_end': '2025-10-14',
            'region': 'All', 'generated_at': '2026-10-23T00:00:00Z', 'report_id': 'test-snapshot',
            'reference': {'orders_now': '2025-10-22', 'tonnage_now': '2025-10-22'},
            'metrics': {'orders': 0, 'previous_orders': 0, 'weight_min': None, 'weight_max': None},
            'regions': [], 'fleet': [], 'orders': [],
        }

    def test_empty_snapshot_with_literal_markup_notes_exports(self):
        response = self.client.post('/api/manager-report/pdf', json={
            'report': self.snapshot, 'notes': 'Review <cargo> & confirm owner.\nNo external links.'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertIn('2025-10-15-2025-10-21.pdf', response.headers['content-disposition'])
        self.assertEqual(response.headers['content-type'], 'application/pdf')

    def test_oversized_notes_rejected(self):
        response = self.client.post('/api/manager-report/pdf', json={
            'report': self.snapshot, 'notes': 'x' * 4001})
        self.assertEqual(response.status_code, 422)
