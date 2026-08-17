# -*- coding: utf-8 -*-
from odoo import models, fields, api


class PlowsPosWebhookLog(models.Model):
    _name = 'plows.pos.webhook.log'
    _description = 'Plows POS Webhook Incoming Audit Log'
    _order = 'received_at desc, id desc'

    name = fields.Char(string='Folio Log', required=True, copy=False, default='WH-LOG-NEW')
    event_id = fields.Char(string='ID Evento (UUID)', index=True, required=True, copy=False)
    tenant_id = fields.Integer(string='ID Tenant', default=1, index=True)

    entity_type = fields.Selection([
        ('product', 'Producto'),
        ('customer', 'Cliente'),
        ('supplier', 'Proveedor'),
        ('warehouse', 'Almacén / Sucursal'),
        ('employee', 'Empleado / Cajero'),
        ('tax', 'Impuesto'),
        ('payment_method', 'Método de Pago'),
        ('closure', 'Corte de Caja'),
        ('ticket', 'Venta / Ticket'),
        ('ping', 'Test Ping'),
    ], string='Tipo de Entidad', required=True, index=True)

    entity_id = fields.Char(string='ID Entidad POS', index=True)
    change_type = fields.Selection([
        ('created', 'Creado'),
        ('updated', 'Actualizado'),
        ('deleted', 'Eliminado / Desactivado'),
    ], string='Tipo de Cambio', default='created', required=True)

    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('processed', 'Procesado Exitosamente'),
        ('skipped', 'Omitido (Duplicado)'),
        ('failed', 'Error al Procesar'),
    ], string='Estado', default='pending', required=True, index=True)

    error_message = fields.Text(string='Mensaje de Error')
    raw_payload = fields.Text(string='Payload JSON Crudo')
    received_at = fields.Datetime(string='Fecha Recepción', default=fields.Datetime.now, required=True)
    processed_at = fields.Datetime(string='Fecha Procesamiento')

    _sql_constraints = [
        ('event_id_unique', 'unique(event_id)', 'El ID del evento de Webhook ya fue registrado anteriormente (idempotencia).')
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'WH-LOG-NEW') == 'WH-LOG-NEW':
                event = vals.get('event_id')
                vals['name'] = f"WH-{event[:8]}" if event else self.env['ir.sequence'].next_by_code('plows.pos.webhook.log') or 'WH-LOG-NEW'
        return super(PlowsPosWebhookLog, self).create(vals_list)
