# -*- coding: utf-8 -*-
import logging
import requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class PlowsReceivableCxc(models.Model):
    _name = 'plows.receivable.cxc'
    _description = 'Cuenta por Cobrar Pendiente Plows POS'
    _order = 'fecha_vencimiento asc, id desc'

    cliente_plows_id = fields.Char(string="ID Cliente PLOWS", index=True)
    cliente_plows_nombre = fields.Char(string="Nombre Cliente PLOWS", index=True)
    cliente_asociado_id = fields.Integer(string="ID Cliente Asociado")
    cliente_asociado_nombre = fields.Char(string="Nombre Cliente Asociado")
    
    almacen_mov_id = fields.Char(string="ID Mov. Almacén / Ticket", index=True)
    factura_id_plows = fields.Char(string="ID Factura PLOWS", index=True)
    no_remision = fields.Char(string="No. Remisión", index=True)
    serie_folio = fields.Char(string="Serie / Folio", index=True)
    
    fecha_factura = fields.Datetime(string="Fecha Factura")
    fecha_vencimiento = fields.Datetime(string="Fecha Vencimiento", index=True)
    
    currency_id = fields.Many2one('res.currency', string="Moneda", default=lambda self: self.env.company.currency_id)
    total_factura = fields.Monetary(string="Total Factura", currency_field='currency_id')
    total_nc = fields.Monetary(string="Total Nota de Crédito", currency_field='currency_id')
    saldo_pendiente = fields.Monetary(string="Saldo Pendiente", currency_field='currency_id')
    total_pagado = fields.Monetary(string="Total Pagado", currency_field='currency_id')
    no_pago = fields.Char(string="Parcialidad / No. Pago")
    
    dias_atraso = fields.Integer(string="Días de Atraso")
    tipo_vencimiento = fields.Selection([
        ('por_vencer', 'Por Vencer'),
        ('vencido', 'Vencido'),
    ], string="Tipo de Vencimiento", default='por_vencer', index=True)
    

    bucket_antiguedad = fields.Selection([
        ('hoy', 'Al día / Hoy'),
        ('vencer_1_7', 'Por Vencer (1-7 días)'),
        ('vencer_8_15', 'Por Vencer (8-15 días)'),
        ('vencer_16_30', 'Por Vencer (16-30 días)'),
        ('vencer_31_60', 'Por Vencer (31-60 días)'),
        ('vencer_60_plus', 'Por Vencer (+60 días)'),
        ('vencido_1_7', 'Vencido (1-7 días)'),
        ('vencido_8_15', 'Vencido (8-15 días)'),
        ('vencido_16_30', 'Vencido (16-30 días)'),
        ('vencido_31_60', 'Vencido (31-60 días)'),
        ('vencido_30_mas', 'Vencido (+30 días)'),
        ('vencido_60_plus', 'Vencido (+60 días)'),
    ], string="Bucket de Antigüedad", default='hoy', index=True)
    
    partner_id = fields.Many2one('res.partner', string="Contacto en Odoo", help="Contacto de Odoo vinculado por ID de PLOWS")
    pos_order_id = fields.Many2one('pos.order', string="Orden de POS en Odoo", help="Ticket de POS en Odoo vinculado")
    move_id = fields.Many2one('account.move', string="Factura en Odoo")

    # Medidas Monetarias Computadas para la Vista Pivot (Proyección PLOWS)
    total_vencido = fields.Monetary(string="Total Vencido", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencido_60_plus = fields.Monetary(string="+60 (Vencido)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencido_31_60 = fields.Monetary(string="31-60 (Vencido)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencido_16_30 = fields.Monetary(string="16-30 (Vencido)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencido_8_15 = fields.Monetary(string="8-15 (Vencido)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencido_1_7 = fields.Monetary(string="1-7 (Vencido)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    
    total_por_vencer = fields.Monetary(string="Total Por Vencer", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    hoy = fields.Monetary(string="Hoy", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencer_1_7 = fields.Monetary(string="1-7 (Por Vencer)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencer_8_15 = fields.Monetary(string="8-15 (Por Vencer)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencer_16_30 = fields.Monetary(string="16-30 (Por Vencer)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencer_31_60 = fields.Monetary(string="31-60 (Por Vencer)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)
    vencer_60_plus = fields.Monetary(string="+60 (Por Vencer)", currency_field='currency_id', compute='_compute_bucket_measures', store=True)

    @api.depends('saldo_pendiente', 'bucket_antiguedad', 'tipo_vencimiento', 'dias_atraso')
    def _compute_bucket_measures(self):
        for rec in self:
            saldo = rec.saldo_pendiente or 0.0
            b_ant = rec.bucket_antiguedad or ''
            t_venc = rec.tipo_vencimiento or ''
            dias = rec.dias_atraso or 0

            rec.total_vencido = saldo if t_venc == 'vencido' else 0.0
            rec.total_por_vencer = saldo if t_venc == 'por_vencer' else 0.0

            rec.hoy = saldo if b_ant == 'hoy' else 0.0
            rec.vencer_1_7 = saldo if b_ant == 'vencer_1_7' else 0.0
            rec.vencer_8_15 = saldo if b_ant == 'vencer_8_15' else 0.0
            rec.vencer_16_30 = saldo if b_ant == 'vencer_16_30' else 0.0
            rec.vencer_31_60 = saldo if b_ant == 'vencer_31_60' else 0.0
            rec.vencer_60_plus = saldo if b_ant in ('vencer_60_plus', 'vencer_30_mas') else 0.0

            rec.vencido_1_7 = saldo if b_ant == 'vencido_1_7' else 0.0
            rec.vencido_8_15 = saldo if b_ant == 'vencido_8_15' else 0.0
            rec.vencido_16_30 = saldo if b_ant == 'vencido_16_30' else 0.0

            # Manejo de vencido_31_60 y vencido_60_plus incluyendo clave legacy 'vencido_30_mas'
            if b_ant == 'vencido_31_60':
                rec.vencido_31_60 = saldo
            elif b_ant == 'vencido_30_mas' and dias <= 60:
                rec.vencido_31_60 = saldo
            else:
                rec.vencido_31_60 = 0.0

            if b_ant in ('vencido_60_plus', 'vencido_60_mas'):
                rec.vencido_60_plus = saldo
            elif b_ant == 'vencido_30_mas' and dias > 60:
                rec.vencido_60_plus = saldo
            else:
                rec.vencido_60_plus = 0.0

    @api.model
    def _get_api_config(self):
        ICPSudo = self.env['ir.config_parameter'].sudo()
        url_base = ICPSudo.get_param('plows_pos_connector.api_url') or 'http://host.docker.internal:5109/api/v1'
        token = ICPSudo.get_param('plows_pos_connector.api_token') or ''
        tenant_id = ICPSudo.get_param('plows_pos_connector.tenant_id') or ''
        return url_base.rstrip('/'), token, tenant_id

    @api.model
    def _parse_selection(self, field_name, value, default_val):
        if not value:
            return default_val
        field = self._fields.get(field_name)
        if field and hasattr(field, 'selection'):
            allowed_keys = [k for k, _ in (field.selection if isinstance(field.selection, list) else [])]
            if value in allowed_keys:
                return value
        return default_val

    @api.model
    def _parse_datetime(self, dt_val):
        if not dt_val:
            return False
        dt_str = str(dt_val).strip()
        if 'T' in dt_str:
            dt_str = dt_str.replace('T', ' ').split('.')[0].rstrip('Z')
        elif len(dt_str) == 10:  # YYYY-MM-DD
            dt_str = f"{dt_str} 00:00:00"
        return dt_str

    @api.model
    def _parse_int(self, val):
        if val is None or val is False or str(val).strip() in ('', 'null', 'None'):
            return False
        try:
            return int(val)
        except (ValueError, TypeError):
            return False

    @api.model
    def _parse_float(self, val):
        if val is None or val is False or str(val).strip() in ('', 'null', 'None'):
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @api.model
    def action_sync_receivables(self):
        """ Método invocado manualmente por el usuario para sincronizar CxC vía consulta HTTP directa a PLOWS API """
        _logger.info("[PlowsCxC] Iniciando sincronización manual directa de Cuentas por Cobrar...")
        url_base, token, tenant_id = self._get_api_config()
        endpoint_url = f"{url_base}/processes/receivables"

        headers = {
            'Authorization': f"Bearer {token}",
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        try:
            response = requests.get(endpoint_url, headers=headers, timeout=30)
            
            # Auto-renovación de token si 401 Unauthorized
            if response.status_code == 401 and tenant_id:
                _logger.info("[PlowsCxC] Token expirado (401). Intentando renovación directa de token...")
                auth_url = f"{url_base}/auth/generate"
                auth_resp = requests.post(auth_url, params={'tenant_id': tenant_id}, timeout=15)
                if auth_resp.status_code == 200:
                    auth_data = auth_resp.json()
                    new_token = auth_data.get('token') or auth_data.get('access_token')
                    if new_token:
                        self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.api_token', new_token)
                        headers['Authorization'] = f"Bearer {new_token}"
                        response = requests.get(endpoint_url, headers=headers, timeout=30)

            if response.status_code != 200:
                _logger.warning(f"[PlowsCxC] Error HTTP {response.status_code} al consultar {endpoint_url}: {response.text}")
                return False

            data = response.json()
            response_data = data.get('payload', {}).get('data', data) if isinstance(data, dict) else data
            if not isinstance(response_data, list):
                _logger.warning("[PlowsCxC] Formato inesperado de respuesta API.")
                return False

            # Preparar valores en memoria
            new_records_vals = []
            for item in response_data:
                cust_id = str(item.get('pos_customer_id') or '') if item.get('pos_customer_id') is not None else ''
                tkt_id = str(item.get('pos_ticket_id')) if item.get('pos_ticket_id') is not None else ''
                
                partner = self.env['res.partner'].search([('x_id_pos', '=', cust_id)], limit=1) if cust_id else False
                pos_order = self.env['pos.order'].search([('x_id_pos', '=', tkt_id)], limit=1) if tkt_id else False

                assoc_cust_id = self._parse_int(item.get('associated_customer_id'))
                total_paid_val = self._parse_float(item.get('total_paid') if item.get('total_paid') is not None else item.get('total_pagado'))

                vals = {
                    'cliente_plows_id': cust_id,
                    'cliente_plows_nombre': item.get('pos_costumer_name') or 'Cliente Desconocido',
                    'cliente_asociado_id': assoc_cust_id,
                    'cliente_asociado_nombre': item.get('associated_customer_name') or '',
                    'almacen_mov_id': tkt_id,
                    'factura_id_plows': str(item.get('pos_invoice_id')) if item.get('pos_invoice_id') is not None else '',
                    'no_remision': item.get('no_remision') or '',
                    'serie_folio': item.get('pos_invoice_folio') or '',
                    'fecha_factura': self._parse_datetime(item.get('invoice_date')),
                    'fecha_vencimiento': self._parse_datetime(item.get('invoice_due_date')),
                    'total_factura': self._parse_float(item.get('total')),
                    'total_nc': self._parse_float(item.get('total_nc')),
                    'saldo_pendiente': self._parse_float(item.get('total_balance')),
                    'total_pagado': total_paid_val,
                    'no_pago': str(item.get('total_paid') or item.get('total_pagado') or ''),
                    'dias_atraso': self._parse_int(item.get('days_past_due')),
                    'tipo_vencimiento': self._parse_selection('tipo_vencimiento', item.get('due_type'), 'por_vencer'),
                    'bucket_antiguedad': self._parse_selection('bucket_antiguedad', item.get('due_bucket'), 'hoy'),
                    'partner_id': partner.id if partner else False,
                    'pos_order_id': pos_order.id if pos_order else False,
                }
                new_records_vals.append(vals)

            # Transacción atómica: Reemplazar registros CxC actuales
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
