# -*- coding: utf-8 -*-
from odoo import models, fields


class PlowsPosSyncLog(models.Model):
    _name = 'plows.pos.sync.log'
    _description = 'Log de Sincronización Plows POS'
    _order = 'timestamp asc'

    job_id = fields.Many2one(
        'plows.pos.sync.job',
        string='Job de Sincronización',
        required=True,
        ondelete='cascade',
        index=True,
    )
    timestamp = fields.Datetime(
        string='Fecha/Hora',
        default=fields.Datetime.now,
        required=True,
    )
    level = fields.Selection([
        ('info', 'Info'),
        ('warning', 'Advertencia'),
        ('error', 'Error'),
    ], string='Nivel', required=True, default='info')

    phase = fields.Selection([
        ('catalogs', 'Catálogos'),
        ('closures', 'Cortes de Caja'),
        ('movements_tickets', 'Movimientos y Tickets'),
    ], string='Fase')

    message = fields.Text(string='Mensaje', required=True)
    record_ref = fields.Char(string='Referencia de Registro')
