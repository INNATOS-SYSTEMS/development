# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from unittest.mock import patch


class TestPaginatedSync(TransactionCase):

    def setUp(self):
        super().setUp()
        self.SyncJob = self.env['plows.pos.sync.job']
        self.job = self.SyncJob.create({
            'name': 'Test Paginated Sync Job'
        })

    @patch.object(SyncJob, '_call_api')
    def test_call_api_paginated_multi_page(self, mock_api):
        """ Prueba que _call_api_paginated itere a través de múltiples páginas consecutivas """
        page_responses = {
            1: [{'id': 'P-01', 'name': 'Prod 1'}, {'id': 'P-02', 'name': 'Prod 2'}],
            2: [{'id': 'P-03', 'name': 'Prod 3'}],
        }

        mock_api.side_effect = lambda endpoint, method='GET', params=None: page_responses.get(params.get('page', 1), [])

        batches = list(self.job._call_api_paginated('catalogs/products', limit=2))

        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0][0], 1)
        self.assertEqual(len(batches[0][1]), 2)
        self.assertEqual(batches[1][0], 2)
        self.assertEqual(len(batches[1][1]), 1)

    @patch.object(SyncJob, '_call_api')
    def test_sync_catalogs_paginated(self, mock_api):
        """ Prueba que la fase de catálogos consuma y registre múltiples páginas """
        mock_api.side_effect = lambda endpoint, method='GET', params=None: {
            ('catalogs/taxes', 1): [],
            ('catalogs/warehouses', 1): [{'id': 'WH-P1', 'name': 'Sucursal 1'}],
            ('catalogs/products', 1): [{'id': f'PRD-{i}', 'sku': f'SKU-{i}', 'name': f'Prod {i}'} for i in range(1, 3)],
            ('catalogs/products', 2): [{'id': 'PRD-3', 'sku': 'SKU-3', 'name': 'Prod 3'}],
            ('catalogs/customers', 1): [],
            ('catalogs/suppliers', 1): [],
            ('catalogs/employees', 1): [],
        }.get((endpoint, params.get('page', 1) if params else 1), [])

        processed, failed = self.job._sync_catalogs_phase()

        self.assertGreater(processed, 0)
        self.assertEqual(failed, 0)

        # Verificar que los 3 productos de ambas páginas se crearon en Odoo
        prod_count = self.env['product.product'].search_count([('x_id_pos', 'in', ['PRD-1', 'PRD-2', 'PRD-3'])])
        self.assertEqual(prod_count, 3)

        # Verificar que existan logs indicando el avance por página
        page_logs = self.job.log_ids.filtered(lambda l: 'Página' in l.message)
        self.assertTrue(len(page_logs) >= 1)
