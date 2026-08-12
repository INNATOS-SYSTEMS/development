# -*- coding: utf-8 -*-
import requests
from odoo import models, fields, api
from odoo.exceptions import UserError

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    plows_pos_api_url = fields.Char(
        string='URL Base de Plows POS', 
        config_parameter='plows_pos_connector.api_url',
        default='http://host.docker.internal:5109/api/v1'
    )

    plows_pos_api_token = fields.Char(
        string='API Token', 
        config_parameter='plows_pos_connector.api_token'
    )
    
    plows_pos_tenant_id = fields.Char(
        string='Tenant ID (ID de Empresa)',
        config_parameter='plows_pos_connector.tenant_id',
        default='1'
    )

    plows_sync_batch_size = fields.Integer(
        string='Registros por Lote',
        config_parameter='plows_pos_connector.sync_batch_size',
        default=100
    )
    
    plows_pos_token_status = fields.Selection([
        ('not_generated', 'No Generado'),
        ('valid', 'Válido'),
        ('expired', 'Expirado'),
        ('invalid', 'Inválido / Error de Conexión')
    ], string='Estado del Token', readonly=True)

    plows_pos_token_generated_at = fields.Datetime(
        string='Fecha de Generación', readonly=True
    )
    
    plows_pos_sync_catalogs_daily = fields.Boolean(
        string='Sincronizar Catálogos Diariamente',
        config_parameter='plows_pos_connector.sync_catalogs_daily'
    )
    plows_pos_sync_transactions_hourly = fields.Boolean(
        string='Sincronizar Transacciones por Hora',
        config_parameter='plows_pos_connector.sync_transactions_hourly'
    )
    plows_pos_sync_inventory_nightly = fields.Boolean(
        string='Sincronizar Cierre de Inventario Nocturno',
        config_parameter='plows_pos_connector.sync_inventory_nightly'
    )
    
    plows_pos_default_customer_id = fields.Many2one(
        'res.partner',
        string='Cliente Genérico (Público General)'
    )

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        params = self.env['ir.config_parameter'].sudo()
        
        customer_id = params.get_param('plows_pos_connector.default_customer_id')
        token_status = params.get_param('plows_pos_connector.token_status', default='not_generated')
        token_generated_at = params.get_param('plows_pos_connector.token_generated_at')
        
        res.update(
            plows_pos_default_customer_id=int(customer_id) if customer_id else False,
            plows_pos_token_status=token_status,
            plows_pos_token_generated_at=fields.Datetime.to_datetime(token_generated_at) if token_generated_at else False,
        )
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        params = self.env['ir.config_parameter'].sudo()
        
        params.set_param('plows_pos_connector.default_customer_id', self.plows_pos_default_customer_id.id or '')
        params.set_param('plows_pos_connector.token_status', self.plows_pos_token_status or 'not_generated')
        params.set_param('plows_pos_connector.token_generated_at', str(self.plows_pos_token_generated_at) if self.plows_pos_token_generated_at else '')

    def action_generate_token(self):
        self.ensure_one()
        url_base = self.plows_pos_api_url
        tenant_id = self.plows_pos_tenant_id or '1'
        if not url_base:
            raise UserError("La URL Base de Plows POS no está configurada.")
            
        if url_base.endswith('/'):
            url_base = url_base[:-1]
            
        url = f"{url_base}/auth/generate"
        
        try:
            response = requests.post(url, params={'tenant_id': tenant_id}, timeout=10)
            if response.status_code != 200:
                self.write({
                    'plows_pos_token_status': 'invalid',
                })
                self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'invalid')
                raise UserError(f"Error al generar token ({response.status_code}): {response.text}")
                
            res_json = response.json()
            if res_json.get('code') != 200:
                self.write({
                    'plows_pos_token_status': 'invalid',
                })
                self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'invalid')
                raise UserError(f"API retornó código de fallo: {res_json.get('message')}")
                
            payload = res_json.get('payload', {})
            data = payload.get('data', {})
            token = data.get('token')
            
            if not token:
                raise UserError("El servidor no retornó un token en el payload.")
                
            now = fields.Datetime.now()
            self.write({
                'plows_pos_api_token': token,
                'plows_pos_token_status': 'valid',
                'plows_pos_token_generated_at': now,
            })
            
            params = self.env['ir.config_parameter'].sudo()
            params.set_param('plows_pos_connector.api_token', token)
            params.set_param('plows_pos_connector.token_status', 'valid')
            params.set_param('plows_pos_connector.token_generated_at', str(now))
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Éxito',
                    'message': 'Token generado e instalado con éxito.',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            self.write({
                'plows_pos_token_status': 'invalid',
            })
            self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'invalid')
            raise UserError(f"Error de conexión al generar token: {str(e)}")

    def action_test_token_connection(self):
        self.ensure_one()
        url_base = self.plows_pos_api_url
        token = self.plows_pos_api_token
        if not url_base:
            raise UserError("La URL Base de Plows POS no está configurada.")
        if not token:
            raise UserError("No hay ningún token configurado para probar.")
            
        if url_base.endswith('/'):
            url_base = url_base[:-1]
            
        url = f"{url_base}/catalogs/taxes"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                self.write({'plows_pos_token_status': 'valid'})
                self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'valid')
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Conexión Exitosa',
                        'message': 'La conexión con la API de Plows POS es correcta y el token es válido.',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            elif response.status_code == 401:
                self.write({'plows_pos_token_status': 'expired'})
                self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'expired')
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error de Conexión',
                        'message': 'El token ha expirado o no es válido (HTTP 401 Unauthorized).',
                        'type': 'danger',
                        'sticky': True,
                    }
                }
            else:
                self.write({'plows_pos_token_status': 'invalid'})
                self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'invalid')
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error de Conexión',
                        'message': f'La API retornó un error HTTP {response.status_code}.',
                        'type': 'warning',
                        'sticky': True,
                    }
                }
        except Exception as e:
            self.write({'plows_pos_token_status': 'invalid'})
            self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'invalid')
            raise UserError(f"Error de conexión con la API: {str(e)}")

    def action_load_default_field_mappings(self):
        return self.env['plows.pos.field.mapping'].action_load_default_mappings()

    def action_reset_test_data(self):
        """ Método de prueba ultra-rápido con SQL directo para reiniciar catálogos, transacciones, POS, secuencias e históricos. """
        import logging
        _logger = logging.getLogger(__name__)
        _logger.info("[PlowsSyncEngine] Iniciando reinicio ultra-rápido SQL de datos de prueba...")

        try:
            cr = self.env.cr
            with cr.savepoint():
                # 1. Logs, checkpoints y staging
                cr.execute("DELETE FROM plows_pos_sync_log")
                cr.execute("DELETE FROM plows_pos_sync_checkpoint")

                # 2. Entidades de Punto de Venta (pagos, líneas, órdenes, sesiones y configuraciones POS)
                cr.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pos_payment')")
                if cr.fetchone()[0]:
                    cr.execute("DELETE FROM pos_payment")

                cr.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pos_order_line')")
                if cr.fetchone()[0]:
                    cr.execute("DELETE FROM pos_order_line")

                cr.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pos_order')")
                if cr.fetchone()[0]:
                    cr.execute("DELETE FROM pos_order")

                cr.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pos_session')")
                if cr.fetchone()[0]:
                    cr.execute("UPDATE pos_session SET state = 'closed' WHERE state != 'closed'")
                    cr.execute("DELETE FROM pos_session")

                cr.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pos_config')")
                if cr.fetchone()[0]:
                    cr.execute("DELETE FROM pos_config WHERE x_id_pos IS NOT NULL OR name ILIKE '%Plows%'")

                # 3. Movimientos y cierres de caja, egresos e inventarios
                cr.execute("DELETE FROM plows_pos_closure_movement")
                cr.execute("DELETE FROM plows_pos_closure")
                cr.execute("DELETE FROM plows_pos_expense")
                cr.execute("DELETE FROM plows_pos_inventory")

                # 4. Desvincular metadatos de Productos en lugar de DELETE truncado (previene error de FK stock_move_product_id_fkey)
                cr.execute("UPDATE product_product SET x_id_pos = NULL, x_sync_status = NULL, x_last_sync_date = NULL WHERE x_id_pos IS NOT NULL")
                cr.execute("UPDATE product_template SET x_id_pos = NULL WHERE x_id_pos IS NOT NULL")

                # 5. Entidades maestras sincronizadas (res.partner, stock.location, hr.employee)
                cr.execute("UPDATE res_partner SET x_id_pos = NULL WHERE x_id_pos IS NOT NULL")
                cr.execute("UPDATE stock_location SET x_id_pos = NULL, x_warehouse_code = NULL WHERE x_id_pos IS NOT NULL")
                cr.execute("UPDATE hr_employee SET x_id_pos = NULL WHERE x_id_pos IS NOT NULL")

                # 6. Restablecimiento de Secuencias Odoo (ir.sequence -> number_next = 1)
                cr.execute("UPDATE ir_sequence SET number_next = 1 WHERE code IN ('plows.pos.closure', 'plows.pos.expense', 'plows.pos.inventory', 'plows.pos.sync.job')")

                # 7. Reset Tareas y Jobs a estado inicial en cola
                cr.execute("UPDATE plows_pos_sync_job SET state = 'draft', start_date = NULL, end_date = NULL")
                cr.execute("UPDATE plows_pos_sync_task SET state = 'queued', current_page = 1, processed_records = 0, progress_percentage = 0.0, total_records = 0, error_log = NULL")

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Reinicio de Pruebas Exitoso',
                    'message': 'Todos los datos de POS, configuraciones, sesiones, órdenes y secuencias han sido limpiados e instantáneamente reiniciados.',
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(f"Fallo durante el reinicio de datos de prueba: {str(e)}")

