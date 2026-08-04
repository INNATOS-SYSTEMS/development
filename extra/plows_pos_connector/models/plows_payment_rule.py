# -*- coding: utf-8 -*-
from odoo import models, fields

class PlowsPosPaymentRule(models.Model):
    _name = 'plows.pos.payment.rule'
    _description = 'Regla de Mapeo de Métodos de Pago Plows POS'

    name = fields.Char(string='Método Pago POS', required=True)
    pos_payment_desc = fields.Char(string='Nombre Método POS')
    odoo_journal_id = fields.Many2one('account.journal', string='Diario Contable Odoo')
