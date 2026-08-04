# -*- coding: utf-8 -*-
from odoo import models, fields

class PlowsPosTaxRule(models.Model):
    _name = 'plows.pos.tax.rule'
    _description = 'Regla de Mapeo de Impuestos Plows POS'

    name = fields.Char(string='Código Impuesto POS', required=True)
    pos_tax_desc = fields.Char(string='Descripción Impuesto POS')
    odoo_tax_id = fields.Many2one('account.tax', string='Impuesto en Odoo')
