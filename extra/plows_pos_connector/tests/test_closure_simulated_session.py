# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

class TestClosureSimulatedSession(TransactionCase):

    def setUp(self):
        super(TestClosureSimulatedSession, self).setUp()
        self.location = self.env['stock.location'].create({
            'name': 'Agua prieta',
            'x_id_pos': '45',
            'usage': 'internal'
        })
        self.employee = self.env['hr.employee'].create({
            'name': 'Responsable Test',
            'x_id_pos': '10'
        })
        self.sync_job = self.env['plows.pos.sync.job'].create({
            'name': 'Job Test Closure',
        })

    def test_simulated_session_creation(self):
        """ Test simulated session creation for closures without cash control """
        closure = self.env['plows.pos.closure'].create({
            'x_id_pos': '102608',
            'session_number': 'CLR-102608',
            'location_id': self.location.id,
            'closing_date': '2026-01-02',
            'closing_time': '21:30:00',
            'total_sales': 15420.50,
            'responsible_id': self.employee.id,
        })

        pos_config = self.sync_job._get_or_create_warehouse_pos_config(self.location)
        self.assertEqual(pos_config.name, 'Agua prieta')
        self.assertEqual(pos_config.x_id_pos, 'POS-CONFIG-WH-45')

        simulated_session = self.sync_job._create_simulated_pos_session(pos_config, closure)
        self.assertEqual(simulated_session.config_id.id, pos_config.id)
        self.assertEqual(simulated_session.x_id_pos, 'POS-SESSION-CLOSURE-102608')
        self.assertEqual(simulated_session.cash_register_balance_start, 0.0)
        self.assertEqual(simulated_session.cash_register_balance_end_real, 15420.50)
