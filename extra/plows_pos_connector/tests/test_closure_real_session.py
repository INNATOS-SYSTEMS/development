# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

class TestClosureRealSession(TransactionCase):

    def setUp(self):
        super(TestClosureRealSession, self).setUp()
        self.location = self.env['stock.location'].create({
            'name': 'Hermosillo Central',
            'x_id_pos': '60',
            'usage': 'internal'
        })
        self.sync_job = self.env['plows.pos.sync.job'].create({
            'name': 'Job Test Real Session',
        })

    def test_real_session_pos_config_creation(self):
        """ Test POS config creation for closures with cash control """
        pos_config = self.sync_job._get_or_create_register_pos_config(
            location=self.location,
            pos_control_id='101',
            register_name='Caja Principal'
        )
        self.assertIn('Hermosillo Central', pos_config.name)
        self.assertEqual(pos_config.x_id_pos, 'POS-CONFIG-WH-60-CTRL-101')
