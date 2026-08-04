# -*- coding: utf-8 -*-
from odoo import models, fields

class StockLocation(models.Model):
    _inherit = 'stock.location'

    x_id_pos = fields.Char(string='ID Sucursal Plows POS', index=True, copy=False)
    x_warehouse_code = fields.Char(string='Código de Almacén POS')
    x_notes = fields.Text(string='Notas / Dirección POS')
    x_analytic_account_id = fields.Many2one(
        'account.analytic.account', 
        string='Cuenta Analítica Asociada'
    )
