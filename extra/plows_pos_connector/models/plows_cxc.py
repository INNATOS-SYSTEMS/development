# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class PlowsReceivableCxc(models.Model):
    _name = 'plows.receivable.cxc'
    _description = 'Cuenta por Cobrar Pendiente Plows POS'
    _order = 'fecha_vencimiento asc, id desc'

    # Campos Identificadores y Nombres provenientes de PLOWS
    cliente_plows_id = fields.Char(string='ID Cliente PLOWS', index=True)
    cliente_plows_nombre = fields.Char(string='Cliente', index=True)
    cliente_asociado_id = fields.Char(string='ID Cliente Asociado')
    cliente_asociado_nombre = fields.Char(string='Cliente Asociado')

    almacen_mov_id = fields.Char(string='ID Ticket POS', index=True)
    factura_id_plows = fields.Char(string='ID Factura PLOWS', index=True)
    no_remision = fields.Char(string='No. Remisión', index=True)
    serie_folio = fields.Char(string='Serie / Folio', index=True)
    no_pago = fields.Char(string='No. Pago')

    # Fechas y Moneda
    fecha_factura = fields.Date(string='Fecha Factura')
    fecha_vencimiento = fields.Date(string='Fecha Límite Pago', index=True)
    dias_atraso = fields.Integer(string='Días Atraso')
    currency_id = fields.Many2one('res.currency', string='Moneda', default=lambda self: self.env.company.currency_id)

    # Importes Financieros (Calculados por PLOWS - Fuente de Verdad)
    total_factura = fields.Monetary(string='Total Factura', currency_field='currency_id')
    total_nc = fields.Monetary(string='Total N.C.', currency_field='currency_id')
    saldo_pendiente = fields.Monetary(string='Saldo Pendiente', currency_field='currency_id')

    # Clasificación Financiera Recibida de PLOWS (Sin Compute / Sin @api.depends)
    tipo_vencimiento = fields.Selection([
        ('por_vencer', 'Total Por Vencer'),
        ('vencido', 'Total Vencido'),
    ], string='Tipo Vencimiento', index=True)

    bucket_antiguedad = fields.Selection([
        ('hoy', 'Hoy'),
        ('vencer_1_7', '01 a 7 Días'),
        ('vencer_8_15', '08 a 15 Días'),
        ('vencer_16_30', '16 a 30 Días'),
        ('vencer_31_60', '31 a 60 Días'),
        ('vencer_60_plus', '+ 60 Días'),
        ('vencido_1_7', '-01 a -7 Días (Vencido)'),
        ('vencido_8_15', '-08 a -15 Días (Vencido)'),
        ('vencido_16_30', '-16 a -30 Días (Vencido)'),
        ('vencido_31_60', '-31 a -60 Días (Vencido)'),
        ('vencido_60_plus', '+ 60 Días Atraso (Vencido)'),
    ], string='Rango Antigüedad', index=True)

    # Relaciones Técnicas Odoo (Match si existen en Odoo)
    partner_id = fields.Many2one('res.partner', string='Contacto Odoo', index=True)
    pos_order_id = fields.Many2one('pos.order', string='Ticket Odoo', index=True)
    move_id = fields.Many2one('account.move', string='Factura Odoo', index=True)

    @api.model
    def action_sync_receivables(self):
        """ Método invocado manualmente por el usuario para sincronizar CxC desde PLOWS API """
        _logger.info("[PlowsCxC] Iniciando sincronización manual de Cuentas por Cobrar...")
        sync_engine = self.env['plows.sync.job'].search([], limit=1)
        if not sync_engine:
            sync_engine = self.env['plows.sync.job'].create({'name': 'Motor de Sincronización CxC'})

        try:
            # 1. Petición HTTP a la API REST de PLOWS
            response_data = sync_engine._call_api('processes/receivables')
            if not isinstance(response_data, list):
                _logger.warning("[PlowsCxC] Formato inesperado de respuesta API.")
                return False

            # 2. Preparar valores en memoria
            new_records_vals = []
            for item in response_data:
                cust_id = str(item.get('pos_customer_id') or '')
                tkt_id = str(item.get('pos_ticket_id') or '')
                
                partner = self.env['res.partner'].search([('x_id_pos', '=', cust_id)], limit=1) if cust_id else False
                pos_order = self.env['pos.order'].search([('x_id_pos', '=', tkt_id)], limit=1) if tkt_id else False

                vals = {
                    'cliente_plows_id': cust_id,
                    'cliente_plows_nombre': item.get('customer_name') or 'Cliente Desconocido',
                    'cliente_asociado_id': str(item.get('associated_customer_id') or ''),
                    'cliente_asociado_nombre': item.get('associated_customer_name') or '',
                    'almacen_mov_id': tkt_id,
                    'factura_id_plows': str(item.get('factura_id') or ''),
                    'no_remision': item.get('no_remision') or '',
                    'serie_folio': item.get('serie_folio') or '',
                    'fecha_factura': item.get('fecha_factura') or False,
                    'fecha_vencimiento': item.get('fecha_vencimiento') or False,
                    'total_factura': float(item.get('total_factura') or 0.0),
                    'total_nc': float(item.get('total_nc') or 0.0),
                    'saldo_pendiente': float(item.get('saldo_pendiente') or 0.0),
                    'no_pago': str(item.get('no_pago') or ''),
                    'dias_atraso': int(item.get('dias_atraso') or 0),
                    'tipo_vencimiento': item.get('tipo_vencimiento') or 'por_vencer',
                    'bucket_antiguedad': item.get('bucket_antiguedad') or 'hoy',
                    'partner_id': partner.id if partner else False,
                    'pos_order_id': pos_order.id if pos_order else False,
                }
                new_records_vals.append(vals)

            # 3. Transacción atómica: Reemplazar registros CxC actuales
            self.search([]).unlink()
            if new_records_vals:
                self.create(new_records_vals)

            _logger.info(f"[PlowsCxC] Sincronización exitosa: {len(new_records_vals)} registros cargados.")
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sincronización CxC Completada',
                    'message': f'Se actualizaron {len(new_records_vals)} cuentas por cobrar desde PLOWS.',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            _logger.error(f"[PlowsCxC] Error durante sincronización manual: {str(e)}")
            raise
