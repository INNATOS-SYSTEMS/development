# -*- coding: utf-8 -*-
from odoo import models, fields

class ProductProduct(models.Model):
    _inherit = 'product.product'

    x_id_pos = fields.Char(string='ID Plows POS', index=True, copy=False)
    x_last_sync_date = fields.Datetime(string='Última Sincronización POS')
    x_sync_status = fields.Selection([
        ('synced', 'Sincronizado'),
        ('desynced', 'Desactualizado')
    ], string='Estatus Sincronización POS', default='synced')
