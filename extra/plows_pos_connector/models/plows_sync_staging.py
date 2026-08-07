# -*- coding: utf-8 -*-
import hashlib
import json
from odoo import models, fields, api


class PlowsPosStagingRaw(models.Model):
    _name = 'plows.pos.staging.raw'
    _description = 'Plows POS Raw Staging Buffer'
    _order = 'id asc'

    job_id = fields.Many2one(
        'plows.pos.sync.job',
        string='Trabajo de Sincronización',
        required=True,
        ondelete='cascade',
        index=True,
    )
    task_id = fields.Many2one(
        'plows.pos.sync.task',
        string='Tarea de Catálogo',
        required=True,
        ondelete='cascade',
        index=True,
    )
    catalog_name = fields.Selection([
        ('products', 'Productos'),
        ('customers', 'Clientes'),
        ('suppliers', 'Proveedores'),
        ('locations', 'Almacenes / Sucursales'),
        ('employees', 'Personal'),
        ('categories', 'Categorías'),
        ('taxes', 'Impuestos'),
        ('payment_methods', 'Métodos de pago'),
    ], string='Catálogo', required=True, index=True)

    pos_record_id = fields.Char(string='ID Registro POS', index=True)
    page_number = fields.Integer(string='Número de Página', default=1)
    payload_hash = fields.Char(string='Hash MD5', size=32, index=True)
    raw_payload = fields.Text(string='Payload JSON Crudo', required=True)

    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('processed', 'Procesado'),
        ('skipped', 'Omitido (Sin Cambios)'),
        ('failed', 'Fallido'),
    ], string='Estado de Staging', default='pending', required=True, index=True)

    error_message = fields.Text(string='Mensaje de Error')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'raw_payload' in vals and not vals.get('payload_hash'):
                raw_val = vals['raw_payload']
                raw_str = raw_val if isinstance(raw_val, str) else json.dumps(raw_val)
                vals['payload_hash'] = hashlib.md5(raw_str.encode('utf-8')).hexdigest()
        return super().create(vals_list)
