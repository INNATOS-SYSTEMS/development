# -*- coding: utf-8 -*-
from odoo import models, fields

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    x_id_pos = fields.Char(string='ID Transacción Plows POS', index=True, copy=False)
