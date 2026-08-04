# -*- coding: utf-8 -*-
from odoo import models, fields


class PlowsPosSyncCheckpoint(models.Model):
    _name = 'plows.pos.sync.checkpoint'
    _description = 'Plows POS Sync Page Checkpoint'
    _order = 'page_number desc, id desc'

    task_id = fields.Many2one(
        'plows.pos.sync.task',
        string='Tarea de Catálogo',
        required=True,
        ondelete='cascade',
        index=True,
    )
    page_number = fields.Integer(string='Número de Página', required=True)
    records_count = fields.Integer(string='Registros Procesados en Página', default=0)
    status = fields.Selection([
        ('success', 'Éxito'),
        ('failed', 'Fallido'),
    ], string='Estado de Página', default='success', required=True)

    processed_at = fields.Datetime(string='Fecha de Procesamiento', default=fields.Datetime.now)
    response_summary = fields.Text(string='Resumen de Respuesta / Error')
