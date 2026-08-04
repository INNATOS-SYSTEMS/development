# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

class TestPlowsPosTransactionSync(TransactionCase):

    def setUp(self):
        super(TestPlowsPosTransactionSync, self).setUp()
        self.location = self.env['stock.location'].create({
            'name': 'Ubicación Central',
            'x_id_pos': '3',
            'usage': 'internal'
        })
        self.product = self.env['product.product'].create({
            'name': 'Coca-Cola 600ml',
            'x_id_pos': '45',
            'type': 'consu',
            'base_unit_count': 1.0


        })
        self.partner = self.env['res.partner'].create({
            'name': 'Público General',
            'x_id_pos': '18'
        })
        self.closure = self.env['plows.pos.closure'].create({
            'x_id_pos': 12,
            'session_number': 482,
            'location_id': self.location.id
        })

    def test_import_ticket_order(self):
        """ Test manually creating a sale order representing a POS ticket linked to a closure """
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'x_id_pos': '10243',
            'x_closure_id': self.closure.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 2.0,
                'price_unit': 20.0
            })]
        })
        self.assertEqual(order.x_id_pos, '10243')
        self.assertEqual(order.x_closure_id.id, self.closure.id)
        self.assertEqual(len(order.order_line), 1)
