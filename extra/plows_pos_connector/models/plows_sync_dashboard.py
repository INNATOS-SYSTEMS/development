# -*- coding: utf-8 -*-
from odoo import models, api, fields


class PlowsPosSyncDashboard(models.TransientModel):
    _name = 'plows.pos.sync.dashboard'
    _description = 'Plows POS Sync Status Dashboard Helper'

    @api.model
    def get_dashboard_status(self):
        """ Retorna el estado consolidado cualitativo del sistema para el dashboard """
        return {
            'overall_health': self._compute_overall_health(),
            'catalog_verifiers': self._compute_catalog_verifiers(),
            'warnings': self._fetch_active_warnings(),
            'recent_closures': self._fetch_recent_closures(),
        }

    @api.model
    def _compute_overall_health(self):
        """ Evalúa la salud global del sistema en 3 niveles: healthy, degraded, critical """
        failed_jobs = self.env['plows.pos.sync.job'].search_count([('state', '=', 'failed')])
        failed_closures = self.env['plows.pos.closure'].search_count([('state', '=', 'failed')])
        unmapped_taxes = self.env['plows.pos.tax.rule'].search_count([('account_tax_id', '=', False)])
        unmapped_payments = self.env['plows.pos.payment.rule'].search_count([('journal_id', '=', False)])

        retrying_tasks = self.env['plows.pos.sync.task'].search_count([('state', '=', 'retrying')])

        if failed_jobs > 0 or failed_closures > 0:
            health_code = 'critical'
            title = 'Error en Sincronización'
            message = 'Existen trabajos de sincronización o cierres de caja con fallos críticos.'
        elif unmapped_taxes > 0 or unmapped_payments > 0 or retrying_tasks > 0:
            health_code = 'degraded'
            title = 'Atención Requerida'
            reasons = []
            if unmapped_payments > 0:
                reasons.append(f"{unmapped_payments} regla(s) de pago sin diario")
            if unmapped_taxes > 0:
                reasons.append(f"{unmapped_taxes} regla(s) de impuesto sin mapear")
            if retrying_tasks > 0:
                reasons.append(f"{retrying_tasks} tarea(s) en reintento")
            message = f"Inconsistencias detectadas: {', '.join(reasons)}."
        else:
            health_code = 'healthy'
            title = 'Todos los Sistemas Operativos'
            message = 'Sincronizaciones y catálogos al día sin alertas activas.'

        return {
            'health_code': health_code,
            'health_title': title,
            'health_message': message,
            'last_check_time': fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'active_warning_count': unmapped_taxes + unmapped_payments + retrying_tasks,
        }

    @api.model
    def _compute_catalog_verifiers(self):
        """ Genera verificadores de salud para cada catálogo de datos """
        catalogs = [
            {'key': 'products', 'label': 'Productos', 'action_xml_id': 'plows_pos_connector.action_plows_pos_sync_job'},
            {'key': 'customers', 'label': 'Clientes', 'action_xml_id': 'plows_pos_connector.action_plows_pos_sync_job'},
            {'key': 'taxes', 'label': 'Impuestos', 'action_xml_id': 'plows_pos_connector.action_plows_pos_tax_rule'},
            {'key': 'payment_methods', 'label': 'Métodos de pago', 'action_xml_id': 'plows_pos_connector.action_plows_pos_payment_rule'},
        ]

        result = []
        for cat in catalogs:
            key = cat['key']
            tasks = self.env['plows.pos.sync.task'].search([('catalog_name', '=', key)], order='id desc', limit=1)
            if not tasks:
                status_code = 'up_to_date'
                status_label = 'Al día'
                last_time = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            else:
                last_task = tasks[0]
                last_time = last_task.create_date.strftime('%Y-%m-%d %H:%M:%S') if last_task.create_date else False
                if last_task.state == 'failed':
                    status_code = 'failed'
                    status_label = 'Fallido'
                elif last_task.state in ['retrying', 'queued']:
                    status_code = 'warning'
                    status_label = 'En Reintento / Pendiente'
                elif last_task.state == 'in_progress':
                    status_code = 'syncing'
                    status_label = 'Sincronizando'
                else:
                    status_code = 'up_to_date'
                    status_label = 'Al día'

            result.append({
                'catalog_key': key,
                'catalog_label': cat['label'],
                'status_code': status_code,
                'status_label': status_label,
                'last_sync_time': last_time,
                'action_xml_id': cat['action_xml_id'],
            })
        return result

    @api.model
    def _fetch_active_warnings(self):
        """ Retorna lista de alertas y advertencias de configuración """
        warnings = []
        unmapped_payments = self.env['plows.pos.payment.rule'].search([('journal_id', '=', False)])
        for r in unmapped_payments:
            warnings.append({
                'id': f"PAY_{r.id}",
                'severity': 'medium',
                'category': 'configuration',
                'message': f"Regla de Pago '{r.name}' sin Diario Contable asignado.",
                'action_label': 'Configurar Regla',
                'target_model': 'plows.pos.payment.rule',
                'target_res_id': r.id,
            })

        unmapped_taxes = self.env['plows.pos.tax.rule'].search([('account_tax_id', '=', False)])
        for t in unmapped_taxes:
            warnings.append({
                'id': f"TAX_{t.id}",
                'severity': 'medium',
                'category': 'configuration',
                'message': f"Regla de Impuesto '{t.name}' sin Impuesto Odoo asignado.",
                'action_label': 'Configurar Impuesto',
                'target_model': 'plows.pos.tax.rule',
                'target_res_id': t.id,
            })

        return warnings

    @api.model
    def _fetch_recent_closures(self):
        """ Retorna últimos 10 cierres de caja con su estado de sincronización (sin conteos crudos) """
        closures = self.env['plows.pos.closure'].search([], order='closing_date desc, id desc', limit=10)
        res = []
        for c in closures:
            if c.state == 'synced':
                st_code, st_label = 'synced', 'Sincronizado'
            elif c.state == 'failed':
                st_code, st_label = 'error_posting', 'Error de Procesamiento'
            else:
                st_code, st_label = 'processing', 'En Proceso'

            date_str = str(c.closing_date) if c.closing_date else ''
            time_str = f" {c.closing_time}" if c.closing_time else ''
            closure_time_display = f"{date_str}{time_str}".strip() or False

            res.append({
                'closure_id': c.id,
                'reference': c.name or f"CORTE/{c.id}",
                'location_name': c.location_id.name if c.location_id else 'Caja Principal',
                'closure_time': closure_time_display,
                'status_code': st_code,
                'status_label': st_label,
            })
        return res
