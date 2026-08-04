# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase

class TestPlowsPosCatalogSync(TransactionCase):

    def setUp(self):
        super(TestPlowsPosCatalogSync, self).setUp()

    def test_tax_mapping_rule(self):
        """ Test plows.pos.tax.rule mapping and description updates """
        tax = self.env['account.tax'].create({
            'name': 'IVA 16% Venta',
            'amount': 16.0,
            'type_tax_use': 'sale'
        })
        
        rule = self.env['plows.pos.tax.rule'].create({
            'name': '1',
            'pos_tax_desc': 'IVA 16%',
            'odoo_tax_id': tax.id
        })
        self.assertEqual(rule.name, '1')
        self.assertEqual(rule.pos_tax_desc, 'IVA 16%')
        self.assertEqual(rule.odoo_tax_id.id, tax.id)
