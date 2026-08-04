# -*- coding: utf-8 -*-
from odoo import models, fields

class PlowsPosClosureMovement(models.Model):
    _name = 'plows.pos.closure.movement'
    _description = 'Movimiento de Caja Chica Plows POS'
    _order = 'date desc'

    closure_id = fields.Many2one('plows.pos.closure', string='Corte de Caja', ondelete='cascade', required=True)
    x_id_pos = fields.Integer(string='ID Movimiento POS', index=True, required=True, copy=False)
    folio = fields.Char(string='Folio POS')
    movement_type = fields.Selection([
        ('income', 'Ingreso (Cash In)'),
        ('expense', 'Egreso (Cash Out)')
    ], string='Tipo de Movimiento', required=True)
    amount = fields.Float(string='Monto', required=True)
    date = fields.Datetime(string='Fecha')
    notes = fields.Text(string='Notas')
    journal_entry_id = fields.Many2one('account.move', string='Póliza Contable', ondelete='set null')
