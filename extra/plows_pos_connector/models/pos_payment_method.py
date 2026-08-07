# -*- coding: utf-8 -*-
from odoo import models, fields

class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    x_id_pos = fields.Char(string='ID Método de Pago Plows POS', index=True, copy=False)
