# -*- coding: utf-8 -*-
from odoo import models, fields

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    x_id_pos = fields.Char(string='ID Transacción Plows POS', index=True, copy=False)
    x_closure_id = fields.Many2one('plows.pos.closure', string='Sesión de Cierre POS', index=True, ondelete='set null')

