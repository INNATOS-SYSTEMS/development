# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

class TestClosureSyncIdempotency(TransactionCase):

    def setUp(self):
        super(TestClosureSyncIdempotency, self).setUp()
        self.location = self.env['stock.location'].create({
            'name': 'Nogales',
            'x_id_pos': '70',
            'usage': 'internal'
        })
        self.sync_job = self.env['plows.pos.sync.job'].create({
            'name': 'Job Test Idempotency',
        })

    def test_idempotent_session_updates(self):
        """ Test that multiple calls to create simulated session update instead of duplicating """
        closure = self.env['plows.pos.closure'].create({
            'x_id_pos': '99001',
            'session_number': 'CLR-99001',
            'location_id': self.location.id,
            'closing_date': '2026-01-02',
            'total_sales': 5000.00,
        })

        config1 = self.sync_job._get_or_create_warehouse_pos_config(self.location)
        config2 = self.sync_job._get_or_create_warehouse_pos_config(self.location)
        self.assertEqual(config1.id, config2.id)

        session1 = self.sync_job._create_simulated_pos_session(config1, closure)
        
        # Update closure sales and re-run
        closure.total_sales = 7500.00
        session2 = self.sync_job._create_simulated_pos_session(config1, closure)

        self.assertEqual(session1.id, session2.id)
        self.assertEqual(session2.cash_register_balance_end_real, 7500.00)
