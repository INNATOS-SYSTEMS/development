# -*- coding: utf-8 -*-
import requests
import logging
import traceback
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SyncAbortError(Exception):
    """Excepción crítica que aborta todas las fases de sincronización posteriores.
    Lanzar únicamente ante fallos irrecuperables (auth, red, API no disponible).
    Para fallos parciales de registro usar self._log('error', ...) y continuar.
    """

class PlowsPosSyncJob(models.Model):
    _name = 'plows.pos.sync.job'
    _description = 'Plows POS Sync Job'
    _order = 'start_date desc'

    name = fields.Char(string='Folio del Job', required=True, copy=False, default='Nuevo')

    start_date = fields.Datetime(string='Fecha Inicio', default=fields.Datetime.now)
    end_date = fields.Datetime(string='Fecha Fin')

    records_processed = fields.Integer(string='Registros Procesados', default=0)
    records_failed = fields.Integer(string='Registros Fallidos', default=0)

    state = fields.Selection([
        ('draft', 'Planificado'),
        ('queued', 'En Cola'),
        ('running', 'En Ejecución'),
        ('done', 'Completado'),
        ('partial_failed', 'Parcialmente Fallido'),
        ('failed', 'Fallo'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', required=True)

    task_ids = fields.One2many(
        'plows.pos.sync.task', 'job_id',
        string='Tareas de Catálogos',
    )
    total_records = fields.Integer(
        string='Total Registros Job',
        compute='_compute_job_progress_totals',
        store=True,
    )
    processed_records = fields.Integer(
        string='Registros Procesados Job',
        compute='_compute_job_progress_totals',
        store=True,
    )
    global_progress = fields.Float(
        string='Progreso Global (%)',
        compute='_compute_job_progress_totals',
        store=True,
        digits=(5, 2),
    )
    error_summary = fields.Text(
        string='Resumen de Errores',
        compute='_compute_error_summary',
        store=True,
    )

    @api.depends('task_ids', 'task_ids.total_records', 'task_ids.processed_records', 'task_ids.state')
    def _compute_job_progress_totals(self):
        for job in self:
            tot = sum(job.task_ids.mapped('total_records'))
            proc = sum(job.task_ids.mapped('processed_records'))
            job.total_records = tot
            job.processed_records = proc
            if tot > 0:
                job.global_progress = min(100.0, round((float(proc) / float(tot)) * 100.0, 2))
            elif any(t.state == 'completed' for t in job.task_ids):
                job.global_progress = 100.0
            else:
                job.global_progress = 0.0

    @api.depends('task_ids.error_log', 'task_ids.state')
    def _compute_error_summary(self):
        for job in self:
            failed_tasks = job.task_ids.filtered(lambda t: t.state in ['failed', 'retrying'] and t.error_log)
            job.error_summary = "\n---\n".join([f"[{t.catalog_name.upper()}] {t.error_log}" for t in failed_tasks]) if failed_tasks else False

    def action_queue_job(self):
        """ Encola el job, realiza el enrolamiento de los 7 catálogos y dispara la sincronización del primer lote (FR-001, FR-016). """
        batch_param = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.sync_batch_size') or 100
        try:
            batch_size = int(batch_param)
            if batch_size <= 0:
                batch_size = 100
        except (ValueError, TypeError):
            batch_size = 100

        for job in self:
            if not job.task_ids:
                default_catalogs = [
                    'products', 'customers', 'suppliers',
                    'locations', 'employees', 'taxes', 'payment_rules',
                ]
                self.env['plows.pos.sync.task'].sudo().create([
                    {
                        'job_id': job.id,
                        'catalog_name': cat,
                        'page_size': batch_size,
                        'state': 'queued',
                    } for cat in default_catalogs
                ])
            job.write({
                'state': 'queued',
                'start_date': fields.Datetime.now() if not job.start_date else job.start_date
            })
            self.env.cr.commit()
            job.action_process_next_batch()
        return True

    def _to_int(self, val):
        """ Convierte enteros, floats o strings numéricos a int. Retorna 0 si es inválido. """
        if val is None or isinstance(val, bool):
            return 0
        try:
            n = int(float(val))
            return n if n > 0 else 0
        except (ValueError, TypeError):
            return 0

    def _fetch_api_page(self, endpoint, page=1, limit=100, extra_params=None):
        """ Realiza una petición paginada a la API POS y retorna (items, total_count, has_next) """
        params = dict(extra_params or {})
        params['page'] = page
        params['limit'] = limit
        params['pageSize'] = limit

        url_base = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.api_url') or 'http://host.docker.internal:5109/api/v1'
        token = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.api_token')

        if url_base.endswith('/'):
            url_base = url_base[:-1]

        url = f"{url_base}/{endpoint}"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

        _logger.info(f"[PlowsSyncEngine] Petición HTTP GET -> {url} (params: {params})")

        try:
            response = requests.request('GET', url, headers=headers, params=params, timeout=30)
            if response.status_code == 401:
                new_token = self._auto_renew_token(url_base)
                if new_token:
                    headers['Authorization'] = f'Bearer {new_token}'
                    response = requests.request('GET', url, headers=headers, params=params, timeout=30)

            if response.status_code != 200:
                raise UserError(f"Error en el servidor API POS ({response.status_code}): {response.text}")

            res_json = response.json()
            if res_json.get('code') and res_json.get('code') != 200:
                raise UserError(f"API retornó código {res_json.get('code')}: {res_json.get('message')}")

            payload = res_json.get('payload', {})
            
            items = []
            total_count = 0
            total_pages = 0
            has_next = None
            
            dicts_to_check = []
            if isinstance(payload, dict):
                for sub in ['header', 'meta', 'pagination', 'paging', 'summary', 'metadata', 'info']:
                    if isinstance(payload.get(sub), dict):
                        dicts_to_check.append(payload.get(sub))
                dicts_to_check.append(payload)
            if isinstance(res_json, dict):
                for sub in ['header', 'meta', 'pagination', 'paging', 'summary', 'metadata', 'info']:
                    if isinstance(res_json.get(sub), dict):
                        dicts_to_check.append(res_json.get(sub))
                dicts_to_check.append(res_json)

            total_count_keys = ['total_count', 'totalCount', 'total_records', 'totalRecords', 'total_items', 'totalItems', 'total_page_records', 'total_rows', 'totalRows']
            total_pages_keys = ['total_pages', 'totalPages', 'total_page', 'totalPage', 'page_count', 'pageCount', 'pages']

            for d in dicts_to_check:
                if not items:
                    items_raw = d.get('data') or d.get('items') or d.get('records') or []
                    if isinstance(items_raw, list):
                        items = items_raw

                if not total_count:
                    for key in total_count_keys:
                        val = self._to_int(d.get(key))
                        if val > 0:
                            total_count = val
                            break

                if not total_pages:
                    for key in total_pages_keys:
                        val = self._to_int(d.get(key))
                        if val > 0:
                            total_pages = val
                            break

                if has_next is None:
                    has_next = d.get('has_next', d.get('hasNext', None))

            if isinstance(payload, list) and not items:
                items = payload

            if total_pages > 0 and (total_count == 0 or total_count == len(items)):
                total_count = total_pages * limit

            if total_count > 0 and total_pages == 0:
                import math
                total_pages = math.ceil(total_count / float(limit))

            if not total_count:
                for d in dicts_to_check:
                    val = self._to_int(d.get('total'))
                    if val > 0:
                        total_count = val
                        break
                if not total_count:
                    total_count = len(items)

            if total_pages > 0:
                has_next = page < total_pages
            elif total_count > 0:
                has_next = (page * limit) < total_count
            elif has_next is None:
                has_next = len(items) >= limit

            _logger.info(
                f"[PlowsSyncEngine] Respuesta API '{endpoint}' (Pág {page}): "
                f"items={len(items)}, total_count={total_count}, total_pages={total_pages}, has_next={has_next}"
            )

            return items, total_count, has_next
        except requests.exceptions.RequestException as e:
            raise UserError(f"Fallo de comunicación con la API de Plows POS en '{url}': {str(e)}")

    def _sync_catalog_page(self, catalog_name, page=1, limit=100):
        """ Ejecuta la sincronización de una sola página para un catálogo dado """
        endpoint_map = {
            'products': 'catalogs/products',
            'customers': 'catalogs/customers',
            'suppliers': 'catalogs/suppliers',
            'locations': 'catalogs/warehouses',
            'employees': 'catalogs/employees',
            'taxes': 'catalogs/taxes',
            'categories': 'catalogs/categories',
            'payment_rules': 'catalogs/payment_rules',
        }
        endpoint = endpoint_map.get(catalog_name)
        if not endpoint:
            _logger.warning(f"Catálogo desconocido o no soportado: {catalog_name}")
            return 0, 0, False

        items, total_count, has_next = self._fetch_api_page(endpoint, page=page, limit=limit)
        if not items:
            return 0, total_count, False

        if catalog_name == 'products':
            processed, failed = self._sync_products_batch(items)
        elif catalog_name == 'customers':
            processed, failed = self._sync_customers_batch(items)
        elif catalog_name == 'suppliers':
            processed, failed = self._sync_suppliers_batch(items)
        elif catalog_name == 'locations':
            processed, failed = self._sync_locations_batch(items)
        elif catalog_name == 'employees':
            processed, failed = self._sync_employees_batch(items)
        elif catalog_name == 'taxes':
            processed, failed = self._sync_taxes_batch(items)
        elif catalog_name == 'categories':
            processed, failed = self._sync_categories_batch(items)
        elif catalog_name == 'payment_rules':
            processed, failed = self._sync_payment_rules_batch(items)
        else:
            processed = len(items)

        return processed, total_count, has_next

    def action_process_next_batch(self):
        """ Método no bloqueante invocado por cron/botón para procesar lotes de páginas de forma continua (FR-001, FR-002, FR-006, FR-012, FR-013, FR-014, FR-015, FR-016). """
        import time
        import psycopg2

        for job in self:
            if job.state not in ['queued', 'running']:
                continue

            # Bloqueo de fila para evitar que otro worker procese simultáneamente el mismo Job
            try:
                self.env.cr.execute("SELECT id FROM plows_pos_sync_job WHERE id = %s FOR UPDATE NOWAIT", (job.id,))
            except psycopg2.OperationalError:
                _logger.info(f"[PlowsSyncEngine] Job {job.id} bloqueado por otro proceso en ejecución. Omitiendo concurrencia.")
                continue

            if job.state == 'queued':
                job.write({
                    'state': 'running',
                    'start_date': fields.Datetime.now() if not job.start_date else job.start_date
                })
                self.env.cr.commit()

            start_time = time.time()
            max_duration = 15.0  # Procesar lotes de forma continua durante hasta 15 segundos por invocación

            while (time.time() - start_time) < max_duration:
                active_tasks = job.task_ids.filtered(lambda t: t.state in ['queued', 'in_progress', 'retrying'])
                if not active_tasks:
                    if any(t.state == 'failed' for t in job.task_ids):
                        new_job_state = 'partial_failed' if any(t.state == 'completed' for t in job.task_ids) else 'failed'
                    else:
                        new_job_state = 'done'
                    job.write({'state': new_job_state, 'end_date': fields.Datetime.now()})
                    self.env.cr.commit()
                    job._notify_bus_update(job)
                    break

                target_task = active_tasks[0]
                _logger.info(f"[PlowsSyncEngine] Procesando lote para tarea '{target_task.catalog_name}' (Pág {target_task.current_page})")

                try:
                    processed_count, total_count, has_next = job._sync_catalog_page(
                        target_task.catalog_name,
                        page=target_task.current_page,
                        limit=target_task.page_size
                    )
                    
                    new_total = max(target_task.total_records, total_count)
                    if new_total > 0 and target_task.total_records != new_total:
                        target_task.write({'total_records': new_total})

                    if processed_count == 0 and not has_next:
                        target_task.write({'state': 'completed'})
                    else:
                        target_task._process_page_checkpoint(
                            page_num=target_task.current_page,
                            records_count=processed_count,
                            status='success',
                            summary=f"Lote de página {target_task.current_page} procesado exitosamente ({processed_count} reg)."
                        )
                        if has_next and (target_task.total_records == 0 or target_task.processed_records < target_task.total_records):
                            if target_task.state != 'in_progress':
                                target_task.write({'state': 'in_progress'})
                        elif not has_next or (target_task.total_records > 0 and target_task.processed_records >= target_task.total_records):
                            target_task.write({'state': 'completed'})

                    self.env.cr.commit()
                    job._notify_bus_update(job)

                except Exception as e:
                    self.env.cr.rollback()
                    _logger.error(f"[PlowsSyncEngine] Error procesando tarea {target_task.catalog_name} pág {target_task.current_page}: {str(e)}")
                    target_task._handle_page_error(str(e))
                    self.env.cr.commit()
                    job._notify_bus_update(job)
                    break

        # Al finalizar la ráfaga de 15s, si aún quedan tareas activas, re-disparar el cron de inmediato
        remaining_jobs = self.search([('state', 'in', ['queued', 'running'])])
        if remaining_jobs:
            remaining_tasks = remaining_jobs.mapped('task_ids').filtered(lambda t: t.state in ['queued', 'in_progress', 'retrying'])
            if remaining_tasks:
                _logger.info(f"[PlowsSyncEngine] Quedan {len(remaining_tasks)} tareas activas. Re-disparando cron de inmediato...")
                cron = self.env.ref('plows_pos_connector.cron_plows_pos_sync_catalogs', raise_if_not_found=False)
                if cron:
                    if hasattr(cron, '_trigger'):
                        cron._trigger()
                    else:
                        cron.write({'nextcall': fields.Datetime.now()})

        return True

    def _notify_bus_update(self, job):
        """ Emite notificación Odoo Bus para refresco dinámico de UI sin recargar página (FR-015). """
        try:
            channel = f"plows_sync_job_{job.id}"
            message_data = {
                'job_id': job.id,
                'state': job.state,
                'records_processed': job.records_processed,
                'total_records': job.total_records,
                'global_progress': job.global_progress,
            }
            self.env['bus.bus']._sendone(channel, 'plows_sync_update', message_data)
        except Exception as e:
            _logger.debug(f"Bus notification skipped: {str(e)}")

    @api.model
    def get_dashboard_status(self):
        """ Delega al helper de dashboard para retornar el estado del sistema """
        return self.env['plows.pos.sync.dashboard'].get_dashboard_status()

    log_ids = fields.One2many(
        'plows.pos.sync.log', 'job_id',
        string='Logs de Sincronización',
    )

    # Campo heredado para compatibilidad con vistas existentes — renderizado desde log_ids
    log_details = fields.Html(
        string='Detalles del Log (Legacy)',
        compute='_compute_log_details',
        store=False,
    )

    @api.depends('log_ids', 'log_ids.level', 'log_ids.message', 'log_ids.phase', 'log_ids.timestamp')
    def _compute_log_details(self):
        level_colors = {'info': 'black', 'warning': 'orange', 'error': 'red'}
        for job in self:
            lines = []
            for log in job.log_ids:
                color = level_colors.get(log.level, 'black')
                phase_label = f'[{log.phase}] ' if log.phase else ''
                ref_label = f' ({log.record_ref})' if log.record_ref else ''
                lines.append(
                    f"<p style='color:{color};'><b>{log.timestamp} {phase_label}</b>{log.message}{ref_label}</p>"
                )
            job.log_details = ''.join(lines) if lines else '<p>Sin registros de log.</p>'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('plows.pos.sync.job') or 'Nuevo'
        return super(PlowsPosSyncJob, self).create(vals_list)

    def action_start_sync(self):
        """ Inicia el proceso de sincronización integral """
        for job in self:
            if job.state not in ['draft', 'failed']:
                continue
            job._run_full_sync()
        return True

    def action_retry(self):
        """ Reintenta la sincronización fallida """
        return self.action_start_sync()

    def _log(self, level, phase, message, record_ref=None):
        """ Crea un registro de log estructurado vinculado a este job.
        No lanza excepciones — fallos de log se registran solo en _logger.
        """
        try:
            self.env['plows.pos.sync.log'].sudo().create({
                'job_id': self.id,
                'level': level,
                'phase': phase,
                'message': message,
                'record_ref': record_ref or False,
            })
        except Exception as log_err:
            _logger.error(f"No se pudo persistir log de sync job {self.name}: {log_err}")

    def _auto_renew_token(self, url_base):
        """ Regenera el token automáticamente desde la API usando el tenant_id configurado """
        tenant_id = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.tenant_id') or '1'
        url = f"{url_base}/auth/generate"
        try:
            response = requests.post(url, params={'tenant_id': tenant_id}, timeout=15)
            if response.status_code == 200:
                res_json = response.json()
                if res_json.get('code') == 200:
                    payload = res_json.get('payload', {})
                    data = payload.get('data', {})
                    token = data.get('token')
                    if token:
                        params = self.env['ir.config_parameter'].sudo()
                        params.set_param('plows_pos_connector.api_token', token)
                        params.set_param('plows_pos_connector.token_status', 'valid')
                        params.set_param('plows_pos_connector.token_generated_at', str(fields.Datetime.now()))
                        return token
        except Exception:
            pass
        return False

    def _call_api(self, endpoint, method='GET', params=None, json_data=None):
        """ Helper para realizar peticiones HTTP a la API POS externa """
        url_base = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.api_url') or 'http://host.docker.internal:5109/api/v1'
        token = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.api_token')

        
        # Eliminar barra diagonal final si existe
        if url_base.endswith('/'):
            url_base = url_base[:-1]
            
        url = f"{url_base}/{endpoint}"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.request(method, url, headers=headers, params=params, json=json_data, timeout=30)
            
            # Auto-recuperación de token si da 401 Unauthorized
            if response.status_code == 401:
                new_token = self._auto_renew_token(url_base)
                if new_token:
                    headers['Authorization'] = f'Bearer {new_token}'
                    response = requests.request(method, url, headers=headers, params=params, json=json_data, timeout=30)
                    
            if response.status_code == 401:
                self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.token_status', 'expired')
                raise UserError("Error de autenticación Bearer: El token de la API no es válido o ha expirado.")
            if response.status_code != 200:
                raise UserError(f"Error en el servidor API POS ({response.status_code}): {response.text}")
            
            res_json = response.json()
            if res_json.get('code') != 200:
                raise UserError(f"API retornó código de fallo {res_json.get('code')}: {res_json.get('message')}")
            
            return res_json.get('payload', {}).get('data', [])
        except requests.exceptions.RequestException as e:
            raise UserError(f"Fallo de comunicación con la API de Plows POS en '{url}': {str(e)}")

    def _call_api_paginated(self, endpoint, limit=500, extra_params=None):
        """ Generador que realiza llamadas paginadas consecutivas a la API
        hasta agotar todos los registros disponibles.
        
        Yields:
            (page, batch_list) por cada lote obtenido.
        """
        page = 1
        params = dict(extra_params or {})
        params['limit'] = limit
        params['pageSize'] = limit

        while True:
            params['page'] = page
            data = self._call_api(endpoint, method='GET', params=params)

            # Manejar respuesta si viene envuelta en dict con datos paginados
            if isinstance(data, dict):
                items = data.get('data') or data.get('items') or []
                has_next = data.get('has_next', data.get('hasNext', None))
                total_pages = data.get('total_pages', data.get('totalPages', None))
            elif isinstance(data, list):
                items = data
                has_next = None
                total_pages = None
            else:
                items = []
                has_next = False
                total_pages = 1

            if not items:
                break

            yield page, items

            # Criterios de finalización de paginación
            if has_next is False:
                break
            if total_pages and page >= total_pages:
                break
            if len(items) < limit:
                break

            page += 1


    def _run_full_sync(self):
        """ Orquestador de sincronización integral: Catálogos → Cortes → Movimientos/Tickets """
        self.ensure_one()
        self.write({
            'state': 'running',
            'start_date': fields.Datetime.now(),
            'records_processed': 0,
            'records_failed': 0,
        })
        self.env.cr.commit()

        total_processed = 0
        total_failed = 0

        try:
            # === FASE 1: Catálogos Maestros ===
            self._log('info', 'catalogs', 'Iniciando sincronización de Catálogos Maestros...')
            p1, f1 = self._sync_catalogs_phase()
            total_processed += p1
            total_failed += f1
            self._log('info', 'catalogs',
                      f'Catálogos completados. Procesados: {p1}, Fallidos: {f1}')

            # === FASE 2: Cortes de Caja ===
            self._log('info', 'closures', 'Iniciando sincronización de Cortes de Caja...')
            p2, f2 = self._sync_closures_phase()
            total_processed += p2
            total_failed += f2
            self._log('info', 'closures',
                      f'Cortes de Caja completados. Procesados: {p2}, Fallidos: {f2}')

            # === FASE 3: Movimientos y Tickets ===
            self._log('info', 'movements_tickets',
                      'Iniciando sincronización de Movimientos y Tickets...')
            p3, f3 = self._sync_movements_tickets_phase()
            total_processed += p3
            total_failed += f3
            self._log('info', 'movements_tickets',
                      f'Movimientos/Tickets completados. Procesados: {p3}, Fallidos: {f3}')

            state = 'done' if total_failed == 0 else 'failed'
            self.write({
                'state': state,
                'end_date': fields.Datetime.now(),
                'records_processed': total_processed,
                'records_failed': total_failed,
            })
            self._log('info' if state == 'done' else 'warning', 'catalogs',
                      f'Sincronización integral finalizada con estado: {state.upper()}. '
                      f'Total procesados: {total_processed}, Total fallidos: {total_failed}')

        except SyncAbortError as abort_err:
            error_trace = traceback.format_exc()
            _logger.error(f"SyncAbortError en job {self.name}: {abort_err}\n{error_trace}")
            self._log('error', 'catalogs',
                      f'ABORTO CRÍTICO: {str(abort_err)}. Fases posteriores canceladas.')
            self.write({
                'state': 'failed',
                'end_date': fields.Datetime.now(),
                'records_processed': total_processed,
                'records_failed': total_failed + 1,
            })

        except Exception as e:
            error_trace = traceback.format_exc()
            _logger.error(f"Error general en Plows Sync Job {self.name}: {str(e)}\n{error_trace}")
            self._log('error', 'catalogs',
                      f'Error general inesperado: {str(e)}')
            self.write({
                'state': 'failed',
                'end_date': fields.Datetime.now(),
                'records_processed': total_processed,
                'records_failed': total_failed + 1,
            })

        self.env.cr.commit()

    def _sync_closures_phase(self):
        """ FASE 2: Cortes de Caja — stub pendiente de implementación en fase 2. """
        self._log('info', 'closures',
                  'Fase de Cortes de Caja: pendiente de implementación en fase 2. '
                  'Reutiliza la lógica de _sync_incomes() en la siguiente fase.')
        return 0, 0

    def _sync_movements_tickets_phase(self):
        """ FASE 3: Movimientos y Tickets — stub pendiente de implementación en fase 2. """
        self._log('info', 'movements_tickets',
                  'Fase de Movimientos/Tickets: pendiente de implementación en fase 2. '
                  'Reutiliza la lógica de _sync_expenses() y la parte de tickets de _sync_incomes().')
    def _get_or_create_product_category(self, name):
        """ Busca o crea una categoría de producto por nombre """
        if not name:
            return False
        cat = self.env['product.category'].search([('name', '=ilike', name)], limit=1)
        if not cat:
            cat = self.env['product.category'].create({'name': name})
        return cat

    def _get_uom_by_name(self, name):
        """ Busca una unidad de medida por nombre o abreviación """
        if not name:
            return False
        uom = self.env['uom.uom'].search([('name', '=ilike', name)], limit=1)
        if not uom:
            uom = self.env['uom.uom'].search([('symbol', '=ilike', name)], limit=1)
        return uom

    def _sync_taxes_batch(self, taxes):
        processed = 0
        failed = 0
        tax_ids = [str(t.get('pos_tax_id') or t.get('posTaxId')) for t in taxes if (t.get('pos_tax_id') or t.get('posTaxId'))]
        existing_rules = self.env['plows.pos.tax.rule'].search([('name', 'in', tax_ids)])
        rules_map = {r.name: r for r in existing_rules}

        for tax in taxes:
            tax_id = tax.get('pos_tax_id') or tax.get('posTaxId')
            if not tax_id:
                continue
            tax_name = tax.get('pos_tax_name') or tax.get('posTaxName') or f"Impuesto {tax_id}"
            percentage = tax.get('rate')
            if percentage is None:
                percentage = (tax.get('percentage') or 0.0) * 100.0 if tax.get('percentage') else 0.0

            try:
                with self.env.cr.savepoint():
                    rule = rules_map.get(str(tax_id))
                    if not rule:
                        odoo_tax = self.env['account.tax'].search([
                            ('amount', '=', percentage),
                            ('type_tax_use', '=', 'sale'),
                            ('company_id', '=', self.env.company.id)
                        ], limit=1)

                        self.env['plows.pos.tax.rule'].create({
                            'name': str(tax_id),
                            'pos_tax_desc': tax_name,
                            'odoo_tax_id': odoo_tax.id if odoo_tax else False
                        })
                        self._log('info', 'catalogs',
                                  f"Regla creada para impuesto POS '{tax_name}' (ID: {tax_id}) -> Odoo: {odoo_tax.name if odoo_tax else 'No asignado'}",
                                  f'plows.pos.tax.rule:{tax_id}')
                    else:
                        if rule.pos_tax_desc != tax_name:
                            rule.write({'pos_tax_desc': tax_name})
                            self._log('info', 'catalogs',
                                      f"Impuesto POS '{tax_name}' (ID: {tax_id}) actualizado.")
                    processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al guardar regla impuesto {tax_id}: {str(e)}',
                          f'plows.pos.tax.rule:{tax_id}')
        return processed, failed

    def _sync_locations_batch(self, warehouses):
        processed = 0
        failed = 0
        wh_ids = [str(wh.get('pos_warehouse_id') or wh.get('posWarehouseId') or wh.get('id')) for wh in warehouses if (wh.get('pos_warehouse_id') or wh.get('posWarehouseId') or wh.get('id'))]
        existing_locs = self.env['stock.location'].search([('x_id_pos', 'in', wh_ids)])
        locs_map = {l.x_id_pos: l for l in existing_locs}

        for wh in warehouses:
            wh_id = wh.get('pos_warehouse_id') or wh.get('posWarehouseId') or wh.get('id')
            if not wh_id:
                continue
            wh_name = wh.get('pos_warehouse_name') or wh.get('posWarehouseName') or wh.get('name') or f"Almacén {wh_id}"
            wh_code = wh.get('usage') or wh.get('code') or wh.get('warehouse_code') or ''
            wh_comment = wh.get('address') or wh.get('notes') or ''

            try:
                with self.env.cr.savepoint():
                    loc = locs_map.get(str(wh_id))
                    vals = {
                        'name': wh_name,
                        'x_id_pos': str(wh_id),
                        'x_warehouse_code': wh_code,
                    }
                    if wh_comment:
                        vals['x_notes'] = wh_comment

                    if not loc:
                        parent_loc = self.env.ref('stock.stock_location_locations', raise_if_not_found=False)
                        vals.update({
                            'location_id': parent_loc.id if parent_loc else False,
                            'usage': 'internal'
                        })
                        self.env['stock.location'].create(vals)
                        self._log('info', 'catalogs',
                                  f"Ubicación creada: '{wh_name}' (ID POS: {wh_id})",
                                  f'stock.location:{wh_id}')
                    else:
                        changed = any(getattr(loc, k) != v for k, v in vals.items())
                        if changed:
                            loc.write(vals)
                            self._log('info', 'catalogs', f"Ubicación actualizada: '{wh_name}'")
                    processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al guardar ubicación {wh_id}: {str(e)}',
                          f'stock.location:{wh_id}')
        return processed, failed

    def _sync_products_batch(self, products):
        processed = 0
        failed = 0
        prod_ids = [str(prod.get('pos_product_id') or prod.get('posProductId') or prod.get('id')) for prod in products if (prod.get('pos_product_id') or prod.get('posProductId') or prod.get('id'))]
        skus = [prod.get('sku') or prod.get('posProductSku') or prod.get('default_code') or prod.get('code') for prod in products if (prod.get('sku') or prod.get('posProductSku') or prod.get('default_code') or prod.get('code'))]

        existing_map = {}
        for i in range(0, len(prod_ids), 1000):
            chunk = prod_ids[i:i+1000]
            found = self.env['product.product'].search([('x_id_pos', 'in', chunk)])
            for p in found:
                existing_map[p.x_id_pos] = p

        sku_map = {}
        for i in range(0, len(skus), 1000):
            chunk = skus[i:i+1000]
            found = self.env['product.product'].search([('default_code', 'in', chunk)])
            for p in found:
                if p.default_code:
                    sku_map[p.default_code] = p

        to_create_vals = []

        for prod in products:
            prod_id = prod.get('pos_product_id') or prod.get('posProductId') or prod.get('id')
            if not prod_id:
                continue
            prod_sku = prod.get('sku') or prod.get('posProductSku') or prod.get('default_code') or prod.get('code') or ''
            prod_name = prod.get('pos_product_name') or prod.get('posProductName') or prod.get('name') or f"Producto {prod_id}"
            is_service = prod.get('is_service') or prod.get('isService') or 0

            price = float(prod.get('price') or prod.get('sale_price') or prod.get('salePrice') or 0.0)
            cost = float(prod.get('cost') or prod.get('cost_price') or prod.get('costPrice') or 0.0)
            barcode = prod.get('barcode') or prod.get('upc') or False
            description = prod.get('description') or prod.get('description_sale') or False
            active = bool(prod.get('active', True) if prod.get('status') is None else (prod.get('status') in [1, True, 'active']))

            category_name = prod.get('category_name') or prod.get('category') or False
            uom_name = prod.get('uom_name') or prod.get('uom') or False

            categ_id = self._get_or_create_product_category(category_name) if category_name else False
            uom_id = self._get_uom_by_name(uom_name) if uom_name else False

            product_type = 'service' if (is_service == 1 or is_service is True) else 'consu'

            try:
                p = existing_map.get(str(prod_id))
                if not p and prod_sku:
                    p = sku_map.get(prod_sku)

                vals = {
                    'name': prod_name,
                    'default_code': prod_sku,
                    'type': product_type,
                    'list_price': price if price else (p.list_price if p else 0.0),
                    'standard_price': cost if cost else (p.standard_price if p else 0.0),
                    'x_id_pos': str(prod_id),
                    'x_sync_status': 'synced',
                    'x_last_sync_date': fields.Datetime.now(),
                    'base_unit_count': 1.0
                }
                if barcode:
                    vals['barcode'] = barcode
                if description:
                    vals['description_sale'] = description
                if categ_id:
                    vals['categ_id'] = categ_id.id
                if uom_id:
                    vals['uom_id'] = uom_id.id
                    vals['uom_po_id'] = uom_id.id
                if active is not None:
                    vals['active'] = active

                if not p:
                    to_create_vals.append(vals)
                else:
                    changed = False
                    for k, v in vals.items():
                        if k in ['x_last_sync_date']:
                            continue
                        if getattr(p, k) != v:
                            if hasattr(getattr(p, k), 'id') and getattr(getattr(p, k), 'id') == v:
                                continue
                            changed = True
                            break
                    if changed:
                        p.write(vals)
                processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al preparar producto {prod_id}: {str(e)}', f'product.product:{prod_id}')

        if to_create_vals:
            try:
                self.env['product.product'].create(to_create_vals)
                self._log('info', 'catalogs', f'Lote de {len(to_create_vals)} productos creados masivamente en el ORM.')
            except Exception as e:
                _logger.error(f"Fallo al crear productos masivamente: {str(e)}")

        return processed, failed

    def _sync_customers_batch(self, customers):
        processed = 0
        failed = 0
        cust_ids = [str(cust.get('pos_customer_id') or cust.get('posCustomerId') or cust.get('id')) for cust in customers if (cust.get('pos_customer_id') or cust.get('posCustomerId') or cust.get('id'))]
        rfcs = [cust.get('rfc') or cust.get('vat') or cust.get('tax_id') for cust in customers if (cust.get('rfc') or cust.get('vat') or cust.get('tax_id'))]

        partners_map = {}
        for i in range(0, len(cust_ids), 1000):
            chunk = cust_ids[i:i+1000]
            found = self.env['res.partner'].search([('x_id_pos', 'in', chunk)])
            for p in found:
                partners_map[p.x_id_pos] = p

        vat_map = {}
        for i in range(0, len(rfcs), 1000):
            chunk = rfcs[i:i+1000]
            found = self.env['res.partner'].search([('vat', 'in', chunk)])
            for p in found:
                if p.vat:
                    vat_map[p.vat] = p

        to_create_vals = []

        for cust in customers:
            cust_id = cust.get('pos_customer_id') or cust.get('posCustomerId') or cust.get('id')
            if not cust_id:
                continue
            cust_name = cust.get('pos_customer_name') or cust.get('posCustomerName') or cust.get('name') or cust.get('company_name') or f"Cliente {cust_id}"
            rfc = cust.get('rfc') or cust.get('vat') or cust.get('tax_id') or ''
            phone = cust.get('customer_phone') or cust.get('customerPhone') or cust.get('phone') or cust.get('mobile') or ''
            email = cust.get('email') or ''
            street = cust.get('street') or cust.get('address') or ''
            zip_code = cust.get('zip_code') or cust.get('zip') or ''
            city = cust.get('city') or ''

            try:
                partner = partners_map.get(str(cust_id))
                if not partner and rfc:
                    partner = vat_map.get(rfc)

                vals = {
                    'name': cust_name,
                    'vat': rfc,
                    'phone': phone,
                    'email': email,
                    'street': street,
                    'zip': zip_code,
                    'city': city,
                    'customer_rank': 1,
                    'x_id_pos': str(cust_id)
                }

                if not partner:
                    to_create_vals.append(vals)
                else:
                    changed = any(getattr(partner, k) != v for k, v in vals.items())
                    if changed:
                        partner.write(vals)
                processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al preparar cliente {cust_id}: {str(e)}', f'res.partner:{cust_id}')

        if to_create_vals:
            try:
                self.env['res.partner'].create(to_create_vals)
                self._log('info', 'catalogs', f'Lote de {len(to_create_vals)} clientes creados masivamente en el ORM.')
            except Exception as e:
                _logger.error(f"Fallo al crear clientes masivamente: {str(e)}")

        return processed, failed

    def _sync_suppliers_batch(self, suppliers):
        processed = 0
        failed = 0
        supp_ids = [str(supp.get('pos_supplier_id') or supp.get('posSupplierId') or supp.get('pos_provider_id') or supp.get('posProviderId') or supp.get('id')) for supp in suppliers if (supp.get('pos_supplier_id') or supp.get('posSupplierId') or supp.get('pos_provider_id') or supp.get('posProviderId') or supp.get('id'))]
        vats = [supp.get('vat') or supp.get('rfc') or supp.get('tax_id') for supp in suppliers if (supp.get('vat') or supp.get('rfc') or supp.get('tax_id'))]

        partners_map = {}
        for i in range(0, len(supp_ids), 1000):
            chunk = supp_ids[i:i+1000]
            found = self.env['res.partner'].search([('x_id_pos', 'in', chunk)])
            for p in found:
                partners_map[p.x_id_pos] = p

        vat_map = {}
        for i in range(0, len(vats), 1000):
            chunk = vats[i:i+1000]
            found = self.env['res.partner'].search([('vat', 'in', chunk)])
            for p in found:
                if p.vat:
                    vat_map[p.vat] = p

        to_create_vals = []

        for supp in suppliers:
            supp_id = supp.get('pos_supplier_id') or supp.get('posSupplierId') or supp.get('pos_provider_id') or supp.get('posProviderId') or supp.get('id')
            if not supp_id:
                continue
            supp_name = supp.get('pos_supplier_name') or supp.get('posSupplierName') or supp.get('pos_provider_name') or supp.get('posProviderName') or supp.get('name') or supp.get('company_name') or f"Proveedor {supp_id}"
            vat = supp.get('vat') or supp.get('rfc') or supp.get('tax_id') or ''
            phone = supp.get('phone') or supp.get('mobile') or ''
            email = supp.get('email') or ''
            street = supp.get('street') or supp.get('address') or ''
            zip_code = supp.get('zip_code') or supp.get('zip') or ''
            city = supp.get('city') or ''

            try:
                partner = partners_map.get(str(supp_id))
                if not partner and vat:
                    partner = vat_map.get(vat)

                vals = {
                    'name': supp_name,
                    'vat': vat,
                    'phone': phone,
                    'email': email,
                    'street': street,
                    'zip': zip_code,
                    'city': city,
                    'supplier_rank': 1,
                    'x_id_pos': str(supp_id)
                }

                if not partner:
                    to_create_vals.append(vals)
                else:
                    changed = any(getattr(partner, k) != v for k, v in vals.items())
                    if changed:
                        partner.write(vals)
                processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al preparar proveedor {supp_id}: {str(e)}', f'res.partner:{supp_id}')

        if to_create_vals:
            try:
                self.env['res.partner'].create(to_create_vals)
                self._log('info', 'catalogs', f'Lote de {len(to_create_vals)} proveedores creados masivamente en el ORM.')
            except Exception as e:
                _logger.error(f"Fallo al crear proveedores masivamente: {str(e)}")

        return processed, failed

    def _sync_employees_batch(self, employees):
        processed = 0
        failed = 0
        emp_ids = [str(emp.get('pos_employee_id') or emp.get('posEmployeeId') or emp.get('id')) for emp in employees if (emp.get('pos_employee_id') or emp.get('posEmployeeId') or emp.get('id'))]

        emps_map = {}
        for i in range(0, len(emp_ids), 1000):
            chunk = emp_ids[i:i+1000]
            found = self.env['hr.employee'].search([('x_id_pos', 'in', chunk)])
            for e in found:
                emps_map[e.x_id_pos] = e

        to_create_vals = []

        for emp in employees:
            emp_id = emp.get('pos_employee_id') or emp.get('posEmployeeId') or emp.get('id')
            if not emp_id:
                continue
            emp_name = emp.get('pos_employee_name') or emp.get('posEmployeeName') or emp.get('name') or emp.get('full_name') or f"Empleado {emp_id}"
            email = emp.get('email') or emp.get('work_email') or ''
            mobile = emp.get('mobile') or emp.get('mobile_phone') or emp.get('phone') or ''
            job_title = emp.get('job_title') or emp.get('position') or ''

            try:
                employee = emps_map.get(str(emp_id))
                vals = {
                    'name': emp_name,
                    'work_email': email,
                    'mobile_phone': mobile,
                    'job_title': job_title,
                    'x_id_pos': str(emp_id)
                }

                if not employee:
                    to_create_vals.append(vals)
                else:
                    changed = any(getattr(employee, k) != v for k, v in vals.items())
                    if changed:
                        employee.write(vals)
                processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al preparar empleado {emp_id}: {str(e)}', f'hr.employee:{emp_id}')

        if to_create_vals:
            try:
                self.env['hr.employee'].create(to_create_vals)
                self._log('info', 'catalogs', f'Lote de {len(to_create_vals)} empleados creados masivamente en el ORM.')
            except Exception as e:
                _logger.error(f"Fallo al crear empleados masivamente: {str(e)}")

        return processed, failed

    def _sync_categories_batch(self, categories):
        processed = 0
        failed = 0
        for cat in categories:
            cat_name = cat.get('name') or cat.get('category_name') or cat.get('categoryName')
            if not cat_name:
                continue
            try:
                self._get_or_create_product_category(cat_name)
                processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al procesar categoría {cat_name}: {str(e)}')
        return processed, failed

    def _sync_payment_rules_batch(self, payment_rules):
        processed = len(payment_rules)
        failed = 0
        self._log('info', 'catalogs', f'Reglas de Pago verificadas: {processed} registros.')
        return processed, failed

    def _sync_catalogs_phase(self):
        """ Descarga y actualiza todos los catálogos maestros de forma optimizada """
        processed = 0
        failed = 0

        # 1. Tasas de Impuestos
        self._log('info', 'catalogs', 'Sincronizando Tasas de Impuestos...')
        try:
            taxes = self._call_api('catalogs/taxes')
            p, f = self._sync_taxes_batch(taxes)
            processed += p
            failed += f
        except Exception as e:
            failed += 1
            self._log('error', 'catalogs', f'Error sincronizando impuestos: {str(e)}')

        # 2. Almacenes (Ubicaciones)
        self._log('info', 'catalogs', 'Sincronizando Sucursales/Almacenes...')
        try:
            warehouses = self._call_api('catalogs/warehouses')
            p, f = self._sync_locations_batch(warehouses)
            processed += p
            failed += f
        except Exception as e:
            failed += 1
            self._log('error', 'catalogs', f'Error sincronizando almacenes: {str(e)}')

        # 3. Productos
        self._log('info', 'catalogs', 'Sincronizando Productos...')
        try:
            for page, products in self._call_api_paginated('catalogs/products', limit=500):
                if not products:
                    continue
                self._log('info', 'catalogs', f'Procesando lote de Productos (Página {page}, {len(products)} registros)...')
                p, f = self._sync_products_batch(products)
                processed += p
                failed += f
        except Exception as e:
            failed += 1
            self._log('error', 'catalogs', f'Error sincronizando productos: {str(e)}')

        # 4. Clientes (Contactos)
        self._log('info', 'catalogs', 'Sincronizando Clientes...')
        try:
            for page, customers in self._call_api_paginated('catalogs/customers', limit=500):
                if not customers:
                    continue
                self._log('info', 'catalogs', f'Procesando lote de Clientes (Página {page}, {len(customers)} registros)...')
                p, f = self._sync_customers_batch(customers)
                processed += p
                failed += f
        except Exception as e:
            failed += 1
            self._log('error', 'catalogs', f'Error sincronizando clientes: {str(e)}')

        # 5. Proveedores (Contactos)
        self._log('info', 'catalogs', 'Sincronizando Proveedores...')
        try:
            for page, suppliers in self._call_api_paginated('catalogs/suppliers', limit=500):
                if not suppliers:
                    continue
                self._log('info', 'catalogs', f'Procesando lote de Proveedores (Página {page}, {len(suppliers)} registros)...')
                p, f = self._sync_suppliers_batch(suppliers)
                processed += p
                failed += f
        except Exception as e:
            failed += 1
            self._log('error', 'catalogs', f'Error sincronizando proveedores: {str(e)}')

        # 6. Personal (Empleados)
        self._log('info', 'catalogs', 'Sincronizando Personal...')
        try:
            for page, employees in self._call_api_paginated('catalogs/employees', limit=500):
                if not employees:
                    continue
                self._log('info', 'catalogs', f'Procesando lote de Personal (Página {page}, {len(employees)} registros)...')
                p, f = self._sync_employees_batch(employees)
                processed += p
                failed += f
        except Exception as e:
            failed += 1
            self._log('error', 'catalogs', f'Error sincronizando personal: {str(e)}')

        return processed, failed


    def _sync_incomes(self):
        """ Sincroniza cierres de caja y sus correspondientes tickets de venta """
        log_lines = []
        processed = 0
        failed = 0
        
        log_lines.append("<h5>Obteniendo Cortes de Caja...</h5>")
        params = {}
        if self.start_date:
            params['start_date'] = self.start_date.strftime('%Y-%m-%d %H:%M:%S')
        if self.end_date:
            params['end_date'] = self.end_date.strftime('%Y-%m-%d %H:%M:%S')
            
        try:
            for page, closures in self._call_api_paginated('processes/closures', limit=100, extra_params=params):
                if not closures:
                    continue
                log_lines.append(f"<p>Procesando lote de cierres de caja (Página {page}, {len(closures)} cierres)...</p>")
                
                for c in closures:
                    closure_id_pos = c.get('posClosureId')
                    if not closure_id_pos:
                        continue
                
                # Buscar ubicación/almacén
                wh_id = c.get('posWarehouseId')
                loc = self.env['stock.location'].search([('x_id_pos', '=', str(wh_id))], limit=1)
                
                # Buscar responsable
                resp_id = c.get('responsibleId')
                employee = self.env['hr.employee'].search([('x_id_pos', '=', str(resp_id))], limit=1)
                
                totals = c.get('totals', {})
                
                vals = {
                    'x_id_pos': closure_id_pos,
                    'session_number': c.get('sessionNumber'),
                    'location_id': loc.id if loc else False,
                    'closing_date': c.get('closingDate'),
                    'closing_time': c.get('closingTime'),
                    'total_sales': c.get('totalSales'),
                    'total_refunds': c.get('totalRefunds'),
                    'total_shortage': c.get('totalShortage'),
                    'shortage_notes': c.get('shortageNotes'),
                    'responsible_id': employee.id if employee else False,
                    'card_total': totals.get('card', 0.0),
                    'cash_total': totals.get('cash', 0.0),
                    'transfers_total': totals.get('transfers', 0.0),
                    'checks_total': totals.get('checks', 0.0),
                    'other_total': totals.get('other', 0.0),
                    'notes': c.get('notes'),
                    'state': 'synced'
                }
                
                closure = self.env['plows.pos.closure'].search([('x_id_pos', '=', closure_id_pos)], limit=1)
                if not closure:
                    closure = self.env['plows.pos.closure'].create(vals)
                    log_lines.append(f"<p style='color:green;'><b>Cierre Creado:</b> Folio {closure.name} (ID POS: {closure_id_pos})</p>")
                else:
                    closure.write(vals)
                    log_lines.append(f"<p><b>Cierre Actualizado:</b> Folio {closure.name}</p>")
                
                # Sincronizar Tickets de este cierre
                try:
                    tickets = self._call_api(f'processes/closures/{closure_id_pos}/tickets')
                    log_lines.append(f"<ul><li>Cierre {closure.name}: Descargando {len(tickets)} tickets.</li>")
                    
                    for t in tickets:
                        tkt_id_pos = t.get('posTicketId')
                        if not tkt_id_pos:
                            continue
                            
                        order = self.env['sale.order'].search([('x_id_pos', '=', str(tkt_id_pos))], limit=1)
                        if order:
                            log_lines.append(f"<li>Ticket {order.name} (ID POS: {tkt_id_pos}) ya importado. Omitiendo.</li>")
                            continue
                            
                        # Determinar cliente
                        cust_id = t.get('posCustomerId')
                        partner = self.env['res.partner'].search([('x_id_pos', '=', str(cust_id))], limit=1)
                        if not partner:
                            # Fallback cliente por defecto
                            default_cust_param = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.default_customer_id')
                            if default_cust_param:
                                partner = self.env['res.partner'].browse(int(default_cust_param))
                        if not partner:
                            log_lines.append(f"<li style='color:red;'>Error Ticket {tkt_id_pos}: Cliente con ID POS {cust_id} no encontrado y no hay cliente por defecto.</li>")
                            failed += 1
                            continue
                            
                        # Determinar almacén Odoo a partir de ubicación
                        warehouse = False
                        if loc:
                            warehouse = self.env['stock.warehouse'].search([('lot_stock_id', '=', loc.id)], limit=1)
                        if not warehouse:
                            warehouse = self.env['stock.warehouse'].search([], limit=1)
                            
                        order_vals = {
                            'partner_id': partner.id,
                            'warehouse_id': warehouse.id if warehouse else False,
                            'date_order': t.get('orderDate'),
                            'x_id_pos': str(tkt_id_pos),
                            'x_closure_id': closure.id,
                            'note': t.get('notes') or '',
                            'order_line': []
                        }
                        
                        line_errors = False
                        for line in t.get('lines', []):
                            pos_prod_id = line.get('posProductId')
                            product = self.env['product.product'].search([('x_id_pos', '=', str(pos_prod_id))], limit=1)
                            if not product:
                                log_lines.append(f"<li style='color:red;'>Error Ticket {tkt_id_pos}: Producto POS ID {pos_prod_id} no está sincronizado en Odoo.</li>")
                                line_errors = True
                                break
                                
                            # Mapear impuesto
                            pos_tax_id = line.get('posTaxId')
                            tax_ids = []
                            if pos_tax_id:
                                tax_rule = self.env['plows.pos.tax.rule'].search([('name', '=', str(pos_tax_id))], limit=1)
                                if tax_rule and tax_rule.odoo_tax_id:
                                    tax_ids = [(4, tax_rule.odoo_tax_id.id)]
                                    
                            order_vals['order_line'].append((0, 0, {
                                'product_id': product.id,
                                'product_uom_qty': line.get('qty', 1.0),
                                'price_unit': line.get('priceUnit', 0.0),
                                'discount': line.get('discount', 0.0) or 0.0,
                                'tax_id': tax_ids
                            }))
                            
                        if line_errors:
                            failed += 1
                            continue
                            
                        new_order = self.env['sale.order'].create(order_vals)
                        try:
                            new_order.action_confirm()
                            log_lines.append(f"<li style='color:green;'>Ticket importado y confirmado: {new_order.name}</li>")
                            processed += 1
                        except Exception as ex:
                            log_lines.append(f"<li style='color:orange;'>Ticket importado como borrador (Fallo de confirmación: {str(ex)}): {new_order.name}</li>")
                            processed += 1
                            
                    log_lines.append("</ul>")
                    
                except Exception as e:
                    failed += 1
                    log_lines.append(f"<p style='color:red;'>Error descargando/procesando tickets para cierre {closure_id_pos}: {str(e)}</p>")
                
                processed += 1
                
        except Exception as e:
            failed += 1
            log_lines.append(f"<p style='color:red;'>Fallo en consulta de cierres de caja: {str(e)}</p>")
            
        return processed, failed, log_lines

    def _sync_expenses(self):
        """ Sincroniza movimientos de caja chica y los registra como movimientos contables """
        log_lines = []
        processed = 0
        failed = 0
        
        log_lines.append("<h5>Sincronizando Egresos/Movimientos de Caja Chica...</h5>")
        
        # Consultar cierres en el rango
        params = {}
        if self.start_date:
            params['start_date'] = self.start_date.strftime('%Y-%m-%d %H:%M:%S')
        if self.end_date:
            params['end_date'] = self.end_date.strftime('%Y-%m-%d %H:%M:%S')
            
        try:
            closures = self._call_api('processes/closures', params=params)
            for c in closures:
                closure_id_pos = c.get('posClosureId')
                if not closure_id_pos:
                    continue
                
                closure = self.env['plows.pos.closure'].search([('x_id_pos', '=', closure_id_pos)], limit=1)
                if not closure:
                    log_lines.append(f"<p style='color:orange;'>Cierre POS {closure_id_pos} no importado aún. Se requiere importar ingresos primero.</p>")
                    continue
                
                # Consumir endpoint de sesiones y movimientos: processes/closures/{id}/sessions
                try:
                    caja_sessions = self._call_api(f'processes/closures/{closure_id_pos}/sessions')
                    for session_container in caja_sessions:
                        for sess in session_container.get('sessions', []):
                            for mov in sess.get('movements', []):
                                mov_id_pos = mov.get('movementId')
                                if not mov_id_pos:
                                    continue
                                    
                                db_mov = self.env['plows.pos.closure.movement'].search([('x_id_pos', '=', mov_id_pos)], limit=1)
                                if db_mov:
                                    # Ya importado
                                    continue
                                    
                                mov_type_desc = mov.get('movementType', 'Egreso')
                                mov_type = 'expense' if mov_type_desc == 'Egreso' else 'income'
                                amount = mov.get('amount') or 0.0
                                
                                # Crear registro en Odoo
                                db_mov = self.env['plows.pos.closure.movement'].create({
                                    'closure_id': closure.id,
                                    'x_id_pos': mov_id_pos,
                                    'folio': mov.get('folio'),
                                    'movement_type': mov_type,
                                    'amount': amount,
                                    'date': mov.get('date'),
                                    'notes': mov.get('notes'),
                                })
                                
                                # Generar póliza contable (account.move)
                                try:
                                    # Resolver diario de caja
                                    payment_rule = self.env['plows.pos.payment.rule'].search([('name', '=ilike', 'Cash')], limit=1)
                                    if not payment_rule:
                                        payment_rule = self.env['plows.pos.payment.rule'].search([], limit=1)
                                        
                                    journal = payment_rule.odoo_journal_id if payment_rule else False
                                    if not journal:
                                        journal = self.env['account.journal'].search([('type', '=', 'cash')], limit=1)
                                        
                                    if not journal or not journal.default_account_id:
                                        log_lines.append(f"<p style='color:orange;'>Movimiento {mov.get('folio')}: No se pudo generar póliza (sin diario de caja configurado).</p>")
                                        processed += 1
                                        continue
                                        
                                    # Resolver cuenta de gasto
                                    expense_account = self.env['account.account'].search([
                                        ('account_type', '=', 'expense'), 
                                        ('company_id', '=', self.env.company.id)
                                    ], limit=1)
                                    if not expense_account:
                                        expense_account = journal.default_account_id # fallback
                                        
                                    move_vals = {
                                        'journal_id': journal.id,
                                        'date': fields.Date.context_today(self),
                                        'ref': f"POS Mov {mov.get('folio') or ''} - Cierre {closure_id_pos}",
                                        'line_ids': []
                                    }
                                    
                                    if mov_type == 'expense':
                                        move_vals['line_ids'] = [
                                            (0, 0, {
                                                'name': mov.get('notes') or 'Egreso de Caja POS',
                                                'account_id': expense_account.id,
                                                'debit': amount,
                                                'credit': 0.0,
                                            }),
                                            (0, 0, {
                                                'name': mov.get('notes') or 'Egreso de Caja POS',
                                                'account_id': journal.default_account_id.id,
                                                'debit': 0.0,
                                                'credit': amount,
                                            })
                                        ]
                                    else:
                                        move_vals['line_ids'] = [
                                            (0, 0, {
                                                'name': mov.get('notes') or 'Ingreso de Caja POS',
                                                'account_id': journal.default_account_id.id,
                                                'debit': amount,
                                                'credit': 0.0,
                                            }),
                                            (0, 0, {
                                                'name': mov.get('notes') or 'Ingreso de Caja POS',
                                                'account_id': expense_account.id,
                                                'debit': 0.0,
                                                'credit': amount,
                                            })
                                        ]
                                        
                                    new_move = self.env['account.move'].create(move_vals)
                                    new_move.action_post()
                                    
                                    db_mov.write({'journal_entry_id': new_move.id})
                                    log_lines.append(f"<p style='color:green;'>Movimiento {mov.get('folio')} ({mov_type_desc}) sincronizado y póliza {new_move.name} creada.</p>")
                                except Exception as cont_ex:
                                    log_lines.append(f"<p style='color:orange;'>Movimiento {mov.get('folio')}: Póliza contable fallida: {str(cont_ex)}</p>")
                                
                                processed += 1
                except Exception as sess_ex:
                    failed += 1
                    log_lines.append(f"<p style='color:red;'>Error obteniendo sesiones para cierre {closure_id_pos}: {str(sess_ex)}</p>")
                    
        except Exception as e:
            failed += 1
            log_lines.append(f"<p style='color:red;'>Fallo al recuperar cierres para egresos: {str(e)}</p>")
            
        return processed, failed, log_lines
