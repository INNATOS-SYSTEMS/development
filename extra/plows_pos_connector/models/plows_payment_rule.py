# -*- coding: utf-8 -*-
from odoo import models, fields, api

class PlowsPosPaymentRule(models.Model):
    _name = 'plows.pos.payment.rule'
    _description = 'Regla de Mapeo de Métodos de Pago Plows POS'
    _order = 'pos_payment_method_id asc, id asc'

    pos_payment_method_id = fields.Char(string='ID Método POS', required=True, index=True)
    name = fields.Char(string='Método Pago POS', required=True)
    pos_payment_desc = fields.Char(string='Estatus / Descripción POS')
    odoo_payment_method_id = fields.Many2one('pos.payment.method', string='Método Pago Odoo')
    odoo_journal_id = fields.Many2one('account.journal', string='Diario Contable Odoo')
    is_active = fields.Boolean(string='Activo', default=True)

    _sql_constraints = [
        ('pos_payment_method_id_unique', 'unique(pos_payment_method_id)', 'El ID del Método de Pago POS debe ser único.')
    ]
