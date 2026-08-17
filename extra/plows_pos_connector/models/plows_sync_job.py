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

    start_date = fields.Datetime(string='Fecha Inicio Sincronización', default=fields.Datetime.now)
    end_date = fields.Datetime(string='Fecha Fin Sincronización')
    execution_start_date = fields.Datetime(string='Inicio de Ejecución')
    execution_end_date = fields.Datetime(string='Fin de Ejecución')
    closures_synced = fields.Boolean(string='Cierres Sincronizados', default=False)

    records_processed = fields.Integer(string='Registros Procesados', default=0)
    records_failed = fields.Integer(string='Registros Fallidos', default=0)

    state = fields.Selection([
        ('draft', 'Planificado'),
        ('queued', 'En Cola'),
        ('running', 'En Ejecución'),
        ('paused', 'Pausado'),
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

    @api.depends('task_ids', 'task_ids.total_records', 'task_ids.processed_records', 'task_ids.progress_percentage', 'task_ids.state')
    def _compute_job_progress_totals(self):
        for job in self:
            tasks = job.task_ids
            if not tasks:
                job.total_records = 0
                job.processed_records = 0
                job.global_progress = 0.0
                continue

            tot = sum(t.total_records for t in tasks)
            proc = sum(t.processed_records for t in tasks)
            job.total_records = tot
            job.processed_records = proc

            # Regla 1 (FR-020): Progreso global promediado entre todas las tareas enroladas
            avg_progress = sum(t.progress_percentage for t in tasks) / float(len(tasks))
            job.global_progress = min(100.0, round(avg_progress, 2))

    def action_pause_sync(self):
        """ Pausa la sincronización activa (Regla 3 - FR-020). """
        for job in self:
            if job.state in ['queued', 'running']:
                job.write({'state': 'paused'})
                job.task_ids.filtered(lambda t: t.state in ['queued', 'in_progress', 'retrying']).write({'state': 'paused'})
                job._notify_bus_update(job)
                self.env.cr.commit()
                self._log('warning', 'general', 'Sincronización pausada por el usuario.')
        return True

    def action_resume_sync(self):
        """ Reanuda la sincronización pausada (Regla 3 - FR-020). """
        for job in self:
            if job.state == 'paused':
                job.write({'state': 'running'})
                job.task_ids.filtered(lambda t: t.state == 'paused').write({'state': 'in_progress'})
                job._notify_bus_update(job)
                self.env.cr.commit()
                self._log('info', 'general', 'Sincronización reanudada por el usuario.')
                job.action_process_next_batch()
        return True

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
                    'locations', 'employees', 'taxes', 'payment_methods', 'closures',
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
                'state': 'running',
                'execution_start_date': fields.Datetime.now() if not job.execution_start_date else job.execution_start_date
            })
            self.env.cr.commit()

            # Emitir notificación bus de inmediato para refresco UI en < 200ms
            job._notify_bus_update(job)

            # Consultar metadatos iniciales por tarea
            job.task_ids._fetch_initial_metadata_count()
            self.env.cr.commit()

            # Disparar ejecución de lotes
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

            if not total_count:
                for d in dicts_to_check:
                    for key in ['total_count', 'totalCount', 'count', 'total']:
                        val = self._to_int(d.get(key))
                        if val > 0:
                            total_count = val
                            break
                    if total_count > 0:
                        break

            if total_count == 0 and total_pages > 0:
                total_count = total_pages * limit

            if total_count > 0 and total_pages == 0:
                import math
                total_pages = math.ceil(total_count / float(limit))

            if total_count == 0:
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
        if catalog_name == 'closures':
            processed, failed, logs = self._sync_incomes()
            total_count = processed + failed
            return processed, total_count, False

        endpoint_map = {
            'products': 'catalogs/products',
            'customers': 'catalogs/customers',
            'suppliers': 'catalogs/warehouses',
            'locations': 'catalogs/warehouses',
            'employees': 'catalogs/employees',
            'taxes': 'catalogs/taxes',
            'categories': 'catalogs/categories',
            'payment_methods': 'catalogs/payment-methods',
            'payment_rules': 'catalogs/payment-methods',
        }
        endpoint = endpoint_map.get(catalog_name)
        if not endpoint:
            _logger.warning(f"Catálogo desconocido o no soportado: {catalog_name}")
            return 0, 0, False

        items, total_count, has_next = self._fetch_api_page(endpoint, page=page, limit=limit)
        if not items:
            return 0, total_count, False

        # Fase 1: Extracción y volcado crudo a Staging con Hash MD5
        import json, hashlib
        staging_vals = []
        for item in items:
            rec_id = str(item.get('pos_product_id') or item.get('id') or item.get('pos_customer_id') or item.get('pos_supplier_id') or item.get('pos_employee_id') or item.get('pos_payment_method_id') or '')
            raw_str = json.dumps(item, ensure_ascii=False)
            md5_hash = hashlib.md5(raw_str.encode('utf-8')).hexdigest()

            # Buscar si existe en la tarea actual
            matching_task = self.env['plows.pos.sync.task'].search([('job_id', '=', self.id), ('catalog_name', '=', catalog_name)], limit=1)
            if matching_task:
                staging_vals.append({
                    'job_id': self.id,
                    'task_id': matching_task.id,
                    'catalog_name': catalog_name,
                    'pos_record_id': rec_id,
                    'page_number': page,
                    'payload_hash': md5_hash,
                    'raw_payload': raw_str,
                    'state': 'pending',
                })

        if staging_vals:
            self.env['plows.pos.staging.raw'].sudo().create(staging_vals)

        # Fase 2: Vaciado e Inserción/Upsert en Bloque a Odoo ORM
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
        elif catalog_name in ['payment_methods', 'payment_rules']:
            processed, failed = self._sync_payment_methods_batch(items)
        else:
            processed = len(items)

        return processed, total_count, has_next

    def action_extract_api_to_staging(self, task):
        """ Fase 1: Extracción Ultra-Rápida HTTP GET a plows.pos.staging.raw (FR-037, FR-038, FR-041, FR-042). """
        import hashlib
        import json

        endpoint_map = {
            'products': 'catalogs/products',
            'customers': 'catalogs/customers',
            'suppliers': 'catalogs/suppliers',
            'locations': 'catalogs/locations',
            'employees': 'catalogs/employees',
            'taxes': 'catalogs/taxes',
            'payment_methods': 'catalogs/payment-methods',
            'payment_rules': 'catalogs/payment-methods',
        }
        endpoint = endpoint_map.get(task.catalog_name)
        if not endpoint:
            return 0

        items, total_count, has_next = self._fetch_api_page(endpoint, page=task.current_page, limit=task.page_size)
        if not items and not has_next:
            return 0

        if total_count > 0 and task.total_records == 0:
            task.write({'total_records': total_count})

        staging_vals = []
        for item in items:
            rec_id = str(item.get('pos_product_id') or item.get('id') or item.get('pos_customer_id') or item.get('pos_supplier_id') or item.get('pos_employee_id') or '')
            raw_str = json.dumps(item, ensure_ascii=False)
            md5_hash = hashlib.md5(raw_str.encode('utf-8')).hexdigest()

            staging_vals.append({
                'job_id': task.job_id.id,
                'task_id': task.id,
                'catalog_name': task.catalog_name,
                'pos_record_id': rec_id,
                'page_number': task.current_page,
                'payload_hash': md5_hash,
                'raw_payload': raw_str,
                'state': 'pending',
            })

        if staging_vals:
            self.env['plows.pos.staging.raw'].sudo().create(staging_vals)

        return len(items)

    def action_process_staging_to_odoo(self, task):
        """ Fase 2: Filtro Delta MD5 y Vaciado Upsert Idempotente a Tablas de Odoo (FR-039, FR-041). """
        import json

        pending_staging = self.env['plows.pos.staging.raw'].search([
            ('task_id', '=', task.id),
            ('state', '=', 'pending')
        ], limit=task.page_size)

        if not pending_staging:
            return 0, 0

        processed = 0
        failed = 0
        to_process_items = []
        to_skip_staging = self.env['plows.pos.staging.raw']

        for stg in pending_staging:
            if stg.pos_record_id:
                prev_stg = self.env['plows.pos.staging.raw'].search([
                    ('catalog_name', '=', task.catalog_name),
                    ('pos_record_id', '=', stg.pos_record_id),
                    ('state', '=', 'processed'),
                    ('id', '!=', stg.id)
                ], limit=1, order='id desc')
                if prev_stg and prev_stg.payload_hash == stg.payload_hash:
                    to_skip_staging |= stg
                    continue

            try:
                item_data = json.loads(stg.raw_payload)
                to_process_items.append((stg, item_data))
            except Exception as e:
                stg.write({'state': 'failed', 'error_message': str(e)})
                failed += 1

        if to_skip_staging:
            to_skip_staging.write({'state': 'skipped'})

        if not to_process_items:
            return len(to_skip_staging), 0

        items_batch = [item for _, item in to_process_items]
        cat_name = task.catalog_name

        try:
            if cat_name == 'products':
                p, f = self._sync_products_batch(items_batch)
            elif cat_name == 'customers':
                p, f = self._sync_customers_batch(items_batch)
            elif cat_name == 'suppliers':
                p, f = self._sync_suppliers_batch(items_batch)
            elif cat_name == 'locations':
                p, f = self._sync_locations_batch(items_batch)
            elif cat_name == 'employees':
                p, f = self._sync_employees_batch(items_batch)
            elif cat_name == 'taxes':
                p, f = self._sync_taxes_batch(items_batch)
            elif cat_name in ['payment_methods', 'payment_rules']:
                p, f = self._sync_payment_methods_batch(items_batch)
            else:
                p, f = len(items_batch), 0

            processed += p
            failed += f

            for stg, _ in to_process_items:
                stg.write({'state': 'processed'})

        except Exception as batch_err:
            failed += len(to_process_items)
            for stg, _ in to_process_items:
                stg.write({'state': 'failed', 'error_message': str(batch_err)})

        return processed + len(to_skip_staging), failed

    def _get_mapped_vals(self, catalog_name, json_data):
        """ Consulta dinámicamente plows.pos.field.mapping para estructurar el diccionario vals de Odoo a partir del JSON (FR-043, FR-045). """
        mappings = self.env['plows.pos.field.mapping'].search([
            ('pos_catalog', '=', catalog_name),
            ('is_active', '=', True)
        ])
        if not mappings:
            return {}

        vals = {}
        for m in mappings:
            val = json_data.get(m.json_key)
            if val is None or val == '':
                if m.default_fallback:
                    val = m.default_fallback
                else:
                    continue
            vals[m.odoo_field_name] = val

        return vals

    def _is_create_allowed(self, catalog_name):
        """ Verifica si la política de mapeo permite la creación de nuevos registros para el catálogo especificado (FR-050, FR-052). """
        rule = self.env['plows.pos.field.mapping'].search([
            ('pos_catalog', '=', catalog_name),
            ('is_active', '=', True)
        ], limit=1)
        if rule:
            return rule.allow_create
        return True

    def action_process_next_batch(self):
        """ Método no bloqueante invocado por cron/botón para procesar lotes de páginas de forma continua (FR-001, FR-002, FR-006, FR-012, FR-013, FR-014, FR-015, FR-016, FR-020). """
        import time
        import psycopg2

        for job in self:
            if job.state not in ['queued', 'running']:
                continue

            # Bloqueo de fila no bloqueante con savepoint anti-deadlock
            try:
                with self.env.cr.savepoint():
                    self.env.cr.execute("SELECT id FROM plows_pos_sync_job WHERE id = %s FOR UPDATE NOWAIT", (job.id,))
            except Exception:
                self.env.cr.rollback()
                _logger.info(f"[PlowsSyncEngine] Job {job.id} está siendo procesado por otro proceso concurrente. Omitiendo execution.")
                continue

            if job.state == 'queued':
                job.write({
                    'state': 'running',
                    'execution_start_date': fields.Datetime.now() if not job.execution_start_date else job.execution_start_date
                })
                self.env.cr.commit()

            start_time = time.time()
            max_duration = 15.0  # Procesar lotes de forma continua durante hasta 15 segundos por invocación
            task_index = 0

            while (time.time() - start_time) < max_duration:
                # Regla 3 (FR-020): Interrumpir ciclo si el job fue pausado
                job.invalidate_recordset(['state'])
                if job.state == 'paused':
                    _logger.info(f"[PlowsSyncEngine] Job {job.id} pausado por el usuario. Interrumpiendo bucle.")
                    break

                active_tasks = job.task_ids.filtered(lambda t: t.state in ['queued', 'in_progress', 'retrying'])
                if not active_tasks:
                    if not job.closures_synced:
                        _logger.info(f"[PlowsSyncEngine] Catálogos completados. Iniciando sincronización de cierres, sesiones simuladas y tickets (Fase 2) para Job {job.id}...")
                        try:
                            job._sync_closures_phase()
                            job._sync_movements_tickets_phase()
                            job.write({'closures_synced': True})
                        except Exception as closure_err:
                            _logger.error(f"[PlowsSyncEngine] Error durante la sincronización de cierres en Job {job.id}: {closure_err}")

                    if any(t.state == 'failed' for t in job.task_ids):
                        new_job_state = 'partial_failed' if any(t.state == 'completed' for t in job.task_ids) else 'failed'
                    else:
                        new_job_state = 'done'
                    job.write({'state': new_job_state, 'execution_end_date': fields.Datetime.now()})
                    self.env.cr.commit()
                    job._notify_bus_update(job)
                    break

                # Regla 4 (FR-020): Selección intercalada Round-Robin entre todos los modelos activos
                target_task = active_tasks[task_index % len(active_tasks)]
                task_index += 1

                # Proteccion anti-deadlock: Bloqueo exclusivo de fila de tarea (plows_pos_sync_task)
                try:
                    with self.env.cr.savepoint():
                        self.env.cr.execute("SELECT id FROM plows_pos_sync_task WHERE id = %s FOR UPDATE NOWAIT", (target_task.id,))
                except Exception:
                    self.env.cr.rollback()
                    _logger.info(f"[PlowsSyncEngine] Tarea {target_task.catalog_name} id={target_task.id} ocupada por otro proceso. Omitiendo concurrencia.")
                    continue

                _logger.info(f"[PlowsSyncEngine] Procesando lote para tarea '{target_task.catalog_name}' (Pág {target_task.current_page})")

                try:
                    processed_count, total_count, has_next = job._sync_catalog_page(
                        target_task.catalog_name,
                        page=target_task.current_page,
                        limit=target_task.page_size
                    )
                    
                    # Regla 2 (FR-020): Fijar total_records exacto desde la primera respuesta de la API
                    if total_count > 0:
                        target_task.write({'total_records': total_count})

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
            _logger.error(f"No se pudo persistir log de sync job (ID {self.id if self else 'unknown'}): {log_err}")


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
            
            payload = res_json.get('payload')
            if isinstance(payload, list):
                return payload
            elif isinstance(payload, dict):
                data = payload.get('data') or payload.get('items') or payload.get('records') or payload.get('tickets') or payload.get('sessions')
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
                return payload

            data = res_json.get('data') or res_json.get('items')
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return [data]

            return []
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
            'execution_start_date': fields.Datetime.now(),
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
                'execution_end_date': fields.Datetime.now(),
                'records_processed': total_processed,
                'records_failed': total_failed,
            })
            self._log('info' if state == 'done' else 'warning', 'catalogs',
                      f'Sincronización integral finalizada con estado: {state.upper()}. '
                      f'Total procesados: {total_processed}, Total fallidos: {total_failed}')

        except SyncAbortError as abort_err:
            error_trace = traceback.format_exc()
            _logger.error(f"SyncAbortError en job ID {self.id if self else 'unknown'}: {abort_err}\n{error_trace}")
            self._log('error', 'catalogs',
                      f'ABORTO CRÍTICO: {str(abort_err)}. Fases posteriores canceladas.')
            self.write({
                'state': 'failed',
                'execution_end_date': fields.Datetime.now(),
                'records_processed': total_processed,
                'records_failed': total_failed + 1,
            })

        except Exception as e:
            error_trace = traceback.format_exc()
            _logger.error(f"Error general en Plows Sync Job ID {self.id if self else 'unknown'}: {str(e)}\n{error_trace}")

            self._log('error', 'catalogs',
                      f'Error general inesperado: {str(e)}')
            self.write({
                'state': 'failed',
                'execution_end_date': fields.Datetime.now(),
                'records_processed': total_processed,
                'records_failed': total_failed + 1,
            })

        self.env.cr.commit()

    def _sync_closures_phase(self):
        """ FASE 2: Sincronización de Cortes de Caja e Ingresos (Tickets) """
        processed, failed, logs = self._sync_incomes()
        return processed, failed

    def _sync_movements_tickets_phase(self):
        """ FASE 3: Sincronización de Egresos / Movimientos de Caja Chica """
        processed, failed, logs = self._sync_expenses()
        return processed, failed
    def _get_or_create_product_category(self, name, category_cache=None):
        """ Retorna o crea una categoría jerárquica basada en una cadena con / (ej. 'Electrónica / Pantallas') con caché en memoria. """
        if not name:
            return False

        if category_cache is not None and name in category_cache:
            return category_cache[name]

        parts = [p.strip() for p in name.split('/') if p.strip()]
        if not parts:
            return False

        parent_id = False
        current_cat = False

        for part in parts:
            domain = [('name', '=ilike', part)]
            if parent_id:
                domain.append(('parent_id', '=', parent_id))
            else:
                domain.append(('parent_id', '=', False))

            current_cat = self.env['product.category'].search(domain, limit=1)
            if not current_cat:
                try:
                    with self.env.cr.savepoint():
                        vals = {'name': part}
                        if parent_id:
                            vals['parent_id'] = parent_id
                        current_cat = self.env['product.category'].create(vals)
                except Exception:
                    current_cat = self.env['product.category'].search(domain, limit=1)

            if current_cat:
                parent_id = current_cat.id

        if current_cat and category_cache is not None:
            category_cache[name] = current_cat

        return current_cat

    def _get_or_create_plows_attribute(self, attr_name=None):
        """ Retorna el objeto product.attribute universal 'Exhibición PLOWS' (FR-024) con protección anti-deadlock. """
        target_name = attr_name.strip() if attr_name else 'Exhibición PLOWS'
        attr = self.env['product.attribute'].search([('name', '=ilike', target_name)], limit=1)
        if not attr:
            try:
                with self.env.cr.savepoint():
                    attr = self.env['product.attribute'].create({
                        'name': target_name,
                        'create_variant': 'always',
                    })
            except Exception:
                attr = self.env['product.attribute'].search([('name', '=ilike', target_name)], limit=1)
        return attr

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
                        if self._is_create_allowed('locations'):
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
                    
                    # Sincronizar Puntos de Venta (pos.config) según controls o Almacén
                    controls = wh.get('controls') or wh.get('Controls') or []
                    self._sync_pos_configs_for_warehouse(wh_id, wh_name, controls)
                    processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al guardar ubicación {wh_id}: {str(e)}',
                          f'stock.location:{wh_id}')
        return processed, failed

    def _sync_pos_configs_for_warehouse(self, wh_id, wh_name, controls):
        """ Sincroniza las pos.config para un almacén según sus cajas (controls) """
        picking_type = self.env['stock.picking.type'].search([('code', '=', 'outgoing')], limit=1)
        
        # Odoo 19 prohibe compartir métodos de pago tipo Efectivo en múltiples pos.config
        payment_methods = self.env['pos.payment.method'].search([('journal_id.type', '!=', 'cash')])
        if not payment_methods:
            payment_methods = self.env['pos.payment.method'].search([])
        pm_cmd = [(6, 0, payment_methods.ids)] if payment_methods else False


        if controls and len(controls) > 0:
            # Si el almacén tiene cajas, marcamos/actualizamos pos.config previo del almacén genérico si existe
            old_wh_cfg = self.env['pos.config'].search([('x_id_pos', '=', str(wh_id)), ('name', '=', wh_name)], limit=1)
            if old_wh_cfg:
                old_wh_cfg.write({'name': f"{wh_name} (General)"})

            for ctrl in controls:
                ctrl_id = str(ctrl.get('posControlId') or ctrl.get('pos_control_id') or ctrl.get('id'))
                ctrl_name = ctrl.get('posControlName') or ctrl.get('pos_control_name') or ctrl.get('name') or f"Caja {ctrl_id}"
                cfg_name = f"{wh_name} - {ctrl_name}"
                
                pos_cfg = self.env['pos.config'].search([('x_id_pos', '=', ctrl_id)], limit=1)
                if not pos_cfg:
                    pos_cfg = self.env['pos.config'].search([('name', '=', cfg_name)], limit=1)
                
                vals = {'name': cfg_name, 'x_id_pos': ctrl_id}
                if pm_cmd:
                    vals['payment_method_ids'] = pm_cmd
                if picking_type and not pos_cfg:
                    vals['picking_type_id'] = picking_type.id
                
                if not pos_cfg:
                    self.env['pos.config'].create(vals)
                    self._log('info', 'catalogs', f"Punto de Venta creado por Caja: '{cfg_name}' (ID POS Control: {ctrl_id})")
                else:
                    pos_cfg.write(vals)
        else:
            # Almacén sin cajas: 1 pos.config con el nombre del almacén
            cfg_name = wh_name
            pos_cfg = self.env['pos.config'].search([('x_id_pos', '=', str(wh_id))], limit=1)
            if not pos_cfg:
                pos_cfg = self.env['pos.config'].search([('name', '=', cfg_name)], limit=1)
            
            vals = {'name': cfg_name, 'x_id_pos': str(wh_id)}
            if pm_cmd:
                vals['payment_method_ids'] = pm_cmd
            if picking_type and not pos_cfg:
                vals['picking_type_id'] = picking_type.id
            
            if not pos_cfg:
                self.env['pos.config'].create(vals)
                self._log('info', 'catalogs', f"Punto de Venta creado para Almacén sin cajas: '{cfg_name}' (ID POS: {wh_id})")
            else:
                pos_cfg.write(vals)


    def _sync_products_batch(self, products):
        self = self.with_context(tracking_disable=True, mail_notrack=True, mail_create_nolog=True, recompute=False)
        processed = 0
        failed = 0

        # 1. Obtener IDs POS, SKUs y Barcodes para precarga masiva en memoria (0 consultas en bucle)
        tmpl_ids = [str(prod.get('pos_product_id') or prod.get('posProductId') or prod.get('id')) for prod in products if (prod.get('pos_product_id') or prod.get('posProductId') or prod.get('id'))]
        skus = list({str(prod.get('sku') or prod.get('posProductSku') or prod.get('default_code') or prod.get('code')) for prod in products if (prod.get('sku') or prod.get('posProductSku') or prod.get('default_code') or prod.get('code'))})
        barcodes = list({str(prod.get('barcode') or prod.get('upc')) for prod in products if (prod.get('barcode') or prod.get('upc'))})

        existing_tmpl_map = {}
        for i in range(0, len(tmpl_ids), 1000):
            chunk = tmpl_ids[i:i+1000]
            for t in self.env['product.template'].search([('x_id_pos', 'in', chunk)]):
                existing_tmpl_map[t.x_id_pos] = t

        sku_tmpl_map = {}
        if skus:
            for i in range(0, len(skus), 1000):
                chunk = skus[i:i+1000]
                for t in self.env['product.template'].search([('default_code', 'in', chunk)]):
                    sku_tmpl_map[t.default_code] = t

        barcode_tmpl_map = {}
        if barcodes:
            for i in range(0, len(barcodes), 1000):
                chunk = barcodes[i:i+1000]
                for t in self.env['product.template'].search([('barcode', 'in', chunk)]):
                    barcode_tmpl_map[t.barcode] = t

        universal_attr = self._get_or_create_plows_attribute('Exhibición PLOWS')
        attr_val_cache = {}
        category_cache = {}
        uom_cache = {}

        to_create_vals = []
        to_create_meta = []  # Guardo (prod_id, val_ids, attr_val_exhib_map)
        to_update_list = []  # Guardo (tmpl, changed_vals, val_ids, attr_val_exhib_map)

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

            category_name = prod.get('categories') or prod.get('category_name') or prod.get('category') or False
            uom_name = prod.get('uom_name') or prod.get('uom') or False

            # Caché de categorías en memoria
            categ_id = False
            if category_name:
                if category_name not in category_cache:
                    category_cache[category_name] = self._get_or_create_product_category(category_name, category_cache=category_cache)
                categ_id = category_cache[category_name]

            # Caché de UOMs en memoria
            uom_id = False
            if uom_name:
                if uom_name not in uom_cache:
                    uom_cache[uom_name] = self._get_uom_by_name(uom_name)
                uom_id = uom_cache[uom_name]

            product_type = 'service' if (is_service == 1 or is_service is True) else 'consu'
            raw_attributes = prod.get('attributes') or []

            try:
                tmpl = existing_tmpl_map.get(str(prod_id))
                if not tmpl and prod_sku:
                    tmpl = sku_tmpl_map.get(prod_sku)
                if not tmpl and barcode:
                    tmpl = barcode_tmpl_map.get(barcode)

                vals_tmpl = {
                    'name': prod_name,
                    'default_code': prod_sku,
                    'type': product_type,
                    'list_price': price if price else (tmpl.list_price if tmpl else 0.0),
                    'standard_price': cost if cost else (tmpl.standard_price if tmpl else 0.0),
                    'x_id_pos': str(prod_id),
                    'x_sync_status': 'synced',
                    'x_last_sync_date': fields.Datetime.now(),
                }
                if barcode and not barcode_tmpl_map.get(barcode):
                    vals_tmpl['barcode'] = barcode

                if description:
                    vals_tmpl['description_sale'] = description
                if categ_id:
                    vals_tmpl['categ_id'] = categ_id.id
                if uom_id:
                    vals_tmpl['uom_id'] = uom_id.id
                    vals_tmpl['uom_po_id'] = uom_id.id
                if active is not None:
                    vals_tmpl['active'] = active

                # Pre-procesar valores de atributos en memoria
                val_ids = []
                attr_val_exhib_map = {}
                if raw_attributes and isinstance(raw_attributes, list):
                    for attr_item in raw_attributes:
                        val_str = str(attr_item.get('attribute') or attr_item.get('name') or 'Único').strip()
                        exhib_id = str(attr_item.get('prod_exhibicion_id') or attr_item.get('prodExhibicionId') or '')

                        val_obj = attr_val_cache.get(val_str)
                        if not val_obj:
                            val_obj = self.env['product.attribute.value'].search([
                                ('attribute_id', '=', universal_attr.id),
                                ('name', '=ilike', val_str)
                            ], limit=1)
                            if not val_obj:
                                val_obj = self.env['product.attribute.value'].create({
                                    'attribute_id': universal_attr.id,
                                    'name': val_str,
                                })
                            attr_val_cache[val_str] = val_obj

                        val_ids.append(val_obj.id)
                        if isinstance(attr_item, dict):
                            attr_val_exhib_map[val_obj.id] = attr_item

                if not tmpl:
                    if self._is_create_allowed('products'):
                        if val_ids:
                            vals_tmpl['attribute_line_ids'] = [(0, 0, {
                                'attribute_id': universal_attr.id,
                                'value_ids': [(6, 0, val_ids)]
                            })]
                        to_create_vals.append(vals_tmpl)
                        to_create_meta.append((str(prod_id), val_ids, attr_val_exhib_map))
                else:
                    changed_vals = {}
                    for k, v in vals_tmpl.items():
                        if k in ['x_last_sync_date']:
                            continue
                        curr_v = getattr(tmpl, k)
                        if hasattr(curr_v, 'id'):
                            curr_v = curr_v.id
                        if curr_v != v:
                            changed_vals[k] = v
                    to_update_list.append((tmpl, changed_vals, val_ids, attr_val_exhib_map))

                processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al preparar producto {prod_id}: {str(e)}', f'product.template:{prod_id}')

        def _apply_variant_mappings(tmpl_obj, exmap):
            if not (tmpl_obj.product_variant_ids and exmap):
                return
            for variant in tmpl_obj.product_variant_ids:
                for p_val in variant.product_template_attribute_value_ids.mapped('product_attribute_value_id'):
                    attr_item = exmap.get(p_val.id)
                    if attr_item and isinstance(attr_item, dict):
                        v_mapped = self._get_mapped_vals('product_variants', attr_item)
                        ex_id = str(attr_item.get('prod_exhibicion_id') or attr_item.get('prodExhibicionId') or '')
                        p_key = str(attr_item.get('plows_key') or attr_item.get('plowsKey') or attr_item.get('sku') or attr_item.get('code') or '')

                        if 'x_id_pos' not in v_mapped and ex_id:
                            v_mapped['x_id_pos'] = ex_id
                        if 'default_code' not in v_mapped and p_key:
                            v_mapped['default_code'] = p_key

                        v_changed = {}
                        for vk, vv in v_mapped.items():
                            if hasattr(variant, vk) and getattr(variant, vk) != vv:
                                v_changed[vk] = vv
                        if v_changed:
                            variant.write(v_changed)

        # 2. Inserción Masiva en Bloque (1 sola consulta SQL en lugar de N consultas)
        if to_create_vals:
            try:
                created_templates = self.env['product.template'].create(to_create_vals)
                for created_tmpl, (pid, vids, exmap) in zip(created_templates, to_create_meta):
                    existing_tmpl_map[pid] = created_tmpl
                    _apply_variant_mappings(created_tmpl, exmap)
                self._log('info', 'catalogs', f'Lote de {len(to_create_vals)} productos creados masivamente en el ORM.')
            except Exception as batch_create_err:
                _logger.error(f"Fallo al crear productos masivamente: {batch_create_err}")

        # 3. Actualización Masiva Diferencial
        for tmpl, changed_vals, val_ids, exmap in to_update_list:
            try:
                if changed_vals:
                    changed_vals['x_last_sync_date'] = fields.Datetime.now()
                    tmpl.write(changed_vals)

                if val_ids:
                    line = tmpl.attribute_line_ids.filtered(lambda l: l.attribute_id.id == universal_attr.id)
                    if not line:
                        tmpl.write({
                            'attribute_line_ids': [(0, 0, {
                                'attribute_id': universal_attr.id,
                                'value_ids': [(6, 0, val_ids)]
                            })]
                        })
                    else:
                        existing_val_ids = line.value_ids.ids
                        if set(existing_val_ids) != set(val_ids):
                            line.write({'value_ids': [(6, 0, list(set(existing_val_ids) | set(val_ids)))]})

                _apply_variant_mappings(tmpl, exmap)
            except Exception as update_err:
                _logger.error(f"Fallo al actualizar producto {tmpl.id}: {update_err}")

        return processed, failed

    def _sync_customers_batch(self, customers):
        processed = 0
        failed = 0
        cust_ids = [str(cust.get('pos_customer_id') or cust.get('posCustomerId') or cust.get('id')) for cust in customers if (cust.get('pos_customer_id') or cust.get('posCustomerId') or cust.get('id'))]
        rfcs = [cust.get('rfc') or cust.get('vat') or cust.get('tax_id') for cust in customers if (cust.get('rfc') or cust.get('vat') or cust.get('tax_id'))]
        emails = [cust.get('email').strip() for cust in customers if cust.get('email') and isinstance(cust.get('email'), str) and cust.get('email').strip()]

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

        email_map = {}
        if emails:
            for i in range(0, len(emails), 1000):
                chunk = emails[i:i+1000]
                found = self.env['res.partner'].search([('email', 'in', chunk)])
                for p in found:
                    if p.email:
                        email_map[p.email] = p

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
                if not partner and email and email.strip():
                    partner = email_map.get(email.strip())

                mapped_vals = self._get_mapped_vals('customers', cust)
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
                vals.update(mapped_vals)

                if not partner:
                    if self._is_create_allowed('customers'):
                        to_create_vals.append(vals)
                else:
                    changed = {}
                    for k, v in vals.items():
                        if getattr(partner, k) != v:
                            changed[k] = v
                    if changed:
                        partner.write(changed)
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
        emails = [supp.get('email').strip() for supp in suppliers if supp.get('email') and isinstance(supp.get('email'), str) and supp.get('email').strip()]

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

        email_map = {}
        if emails:
            for i in range(0, len(emails), 1000):
                chunk = emails[i:i+1000]
                found = self.env['res.partner'].search([('email', 'in', chunk)])
                for p in found:
                    if p.email:
                        email_map[p.email] = p

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
                if not partner and email and email.strip():
                    partner = email_map.get(email.strip())

                mapped_vals = self._get_mapped_vals('suppliers', supp)
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
                vals.update(mapped_vals)

                if not partner:
                    if self._is_create_allowed('suppliers'):
                        to_create_vals.append(vals)
                else:
                    changed = {}
                    for k, v in vals.items():
                        if getattr(partner, k) != v:
                            changed[k] = v
                    if changed:
                        partner.write(changed)
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
                
                # Buscar o crear partner explícito para evitar duplicados huérfanos generados por Odoo ORM
                partner = False
                if email and email.strip():
                    partner = self.env['res.partner'].search([('email', '=', email.strip())], limit=1)
                if not partner and mobile and mobile.strip():
                    partner = self.env['res.partner'].search([('phone', '=', mobile.strip())], limit=1)
                if not partner:
                    partner = self.env['res.partner'].search([('name', '=ilike', emp_name)], limit=1)

                if not partner and self._is_create_allowed('employees'):
                    partner = self.env['res.partner'].create({
                        'name': emp_name,
                        'email': email,
                        'phone': mobile,
                        'x_id_pos': f"EMP-{emp_id}"
                    })
                elif partner and email and not partner.email:
                    partner.write({'email': email})

                mapped_vals = self._get_mapped_vals('employees', emp)
                vals = {
                    'name': emp_name,
                    'work_email': email,
                    'mobile_phone': mobile,
                    'job_title': job_title,
                    'x_id_pos': str(emp_id)
                }
                if partner:
                    vals['work_contact_id'] = partner.id
                vals.update(mapped_vals)

                if not employee:
                    if self._is_create_allowed('employees'):
                        to_create_vals.append(vals)
                else:
                    changed = {}
                    for k, v in vals.items():
                        curr_val = getattr(employee, k) if hasattr(employee, k) else False
                        if isinstance(curr_val, models.BaseModel):
                            curr_val = curr_val.id if curr_val else False
                        if curr_val != v:
                            changed[k] = v
                    if changed:
                        employee.write(changed)
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

    def _sync_payment_methods_batch(self, methods):
        processed = 0
        failed = 0
        pm_ids = [str(pm.get('pos_payment_method_id') or pm.get('posPaymentMethodId') or pm.get('id')) for pm in methods if (pm.get('pos_payment_method_id') or pm.get('posPaymentMethodId') or pm.get('id'))]

        existing_rules = self.env['plows.pos.payment.rule'].search([('pos_payment_method_id', 'in', pm_ids)])
        rule_map = {r.pos_payment_method_id: r for r in existing_rules}

        # Cargar métodos de pago nativos de Odoo POS para autocoincidencia inteligente
        odoo_methods = self.env['pos.payment.method'].search([])

        for pm in methods:
            pm_id = pm.get('pos_payment_method_id') or pm.get('posPaymentMethodId') or pm.get('id')
            if not pm_id:
                continue
            pm_name = pm.get('pos_payment_method') or pm.get('posPaymentMethod') or pm.get('name') or f"Método {pm_id}"
            raw_status = pm.get('status') or 'Activo'
            is_active = bool(raw_status in [1, True, 'active', '1', 'Activo'] if raw_status is not None else True)

            try:
                rule = rule_map.get(str(pm_id))

                # Inferencia / Autocoincidencia por Nombre si no tiene asignado un Método de Pago en Odoo
                auto_odoo_method_id = False
                if odoo_methods:
                    clean_name = pm_name.upper()
                    for om in odoo_methods:
                        om_name = (om.name or '').upper()
                        if ('EFECTIVO' in clean_name and ('CASH' in om_name or 'EFECTIVO' in om_name)) or \
                           ('TARJETA' in clean_name and ('CARD' in om_name or 'TARJETA' in om_name or 'BANK' in om_name)) or \
                           ('TRANSFERENCIA' in clean_name and ('BANK' in om_name or 'TRANSF' in om_name)) or \
                           (clean_name == om_name):
                            auto_odoo_method_id = om.id
                            break

                vals = {
                    'pos_payment_method_id': str(pm_id),
                    'name': pm_name,
                    'pos_payment_desc': str(raw_status),
                    'is_active': is_active,
                }

                if not rule:
                    if self._is_create_allowed('payment_methods'):
                        if auto_odoo_method_id:
                            vals['odoo_payment_method_id'] = auto_odoo_method_id
                        self.env['plows.pos.payment.rule'].create(vals)
                else:
                    changed = {}
                    if not rule.odoo_payment_method_id and auto_odoo_method_id:
                        vals['odoo_payment_method_id'] = auto_odoo_method_id
                    for k, v in vals.items():
                        if getattr(rule, k) != v:
                            changed[k] = v
                    if changed:
                        rule.write(changed)
                processed += 1
            except Exception as e:
                failed += 1
                self._log('error', 'catalogs', f'Fallo al preparar regla de método de pago {pm_id}: {str(e)}', f'plows.pos.payment.rule:{pm_id}')

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

        # 2. Almacenes (Ubicaciones y Puntos de Venta por Cajas)
        self._log('info', 'catalogs', 'Sincronizando Sucursales/Almacenes y Puntos de Venta...')
        try:
            for page, warehouses in self._call_api_paginated('catalogs/warehouses', limit=100):
                if not warehouses:
                    continue
                self._log('info', 'catalogs', f'Procesando lote de Almacenes y Puntos de Venta (Página {page}, {len(warehouses)} registros)...')
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


    def _get_or_create_warehouse_pos_config(self, location=None):
        """ Obtiene o crea un pos.config asociado exclusivamente al Almacén (modo sin control de caja) """
        pos_config = False
        if location and location.x_id_pos:
            x_id_pos = f"POS-CONFIG-WH-{location.x_id_pos}"
            pos_config = self.env['pos.config'].search([('x_id_pos', '=', x_id_pos)], limit=1)

        if not pos_config and location and location.name:
            pos_config = self.env['pos.config'].search([('name', '=', location.name)], limit=1)

        if not pos_config:
            picking_type = self.env['stock.picking.type'].search([('code', '=', 'outgoing')], limit=1)
            config_name = location.name if (location and location.name) else 'Plows POS Sync'
            config_x_id_pos = f"POS-CONFIG-WH-{location.x_id_pos or location.id}" if location else 'POS-CONFIG-WH-DEFAULT'
            pos_config = self.env['pos.config'].create({
                'name': config_name,
                'x_id_pos': config_x_id_pos,
                'picking_type_id': picking_type.id if picking_type else False,
            })
        return pos_config

    def _get_or_create_register_pos_config(self, location=None, pos_control_id=None, register_name=None):
        """ Obtiene o crea un pos.config asociado a Almacén + Caja (modo con control de caja) """
        pos_config = False
        if pos_control_id:
            x_id_pos = f"POS-CONFIG-WH-{location.x_id_pos if location else '0'}-CTRL-{pos_control_id}"
            pos_config = self.env['pos.config'].search(['|', ('x_id_pos', '=', x_id_pos), ('x_id_pos', '=', str(pos_control_id))], limit=1)

        if not pos_config and location and location.x_id_pos:
            pos_config = self.env['pos.config'].search([('x_id_pos', '=', str(location.x_id_pos))], limit=1)

        if not pos_config and location and location.name:
            pos_config = self.env['pos.config'].search([('name', '=', location.name)], limit=1)

        if not pos_config:
            picking_type = self.env['stock.picking.type'].search([('code', '=', 'outgoing')], limit=1)
            config_name = f"{location.name} - {register_name or f'Caja {pos_control_id}'}" if location else f"Caja {pos_control_id}"
            config_x_id_pos = f"POS-CONFIG-WH-{location.x_id_pos if location else '0'}-CTRL-{pos_control_id}" if pos_control_id else 'POS-CONFIG-DEFAULT'
            pos_config = self.env['pos.config'].create({
                'name': config_name,
                'x_id_pos': config_x_id_pos,
                'picking_type_id': picking_type.id if picking_type else False,
            })
        return pos_config

    def _create_simulated_pos_session(self, pos_config, closure):
        """ Crea o actualiza una pos.session simulada para cierres sin control de caja """
        session_x_id_pos = f"POS-SESSION-CLOSURE-{closure.x_id_pos or closure.id}"
        session = self.env['pos.session'].search([('x_id_pos', '=', session_x_id_pos)], limit=1)

        total_sales = closure.total_sales or 0.0

        if not session:
            # Si existen sesiones abiertas en este config, cerrar antes de abrir la simulada
            open_sessions = self.env['pos.session'].search([('config_id', '=', pos_config.id), ('state', '!=', 'closed')])
            for os in open_sessions:
                try:
                    os.write({'state': 'closed', 'stop_at': fields.Datetime.now()})
                except Exception:
                    pass

            session_name = f"{pos_config.name}/Corte-{closure.name or closure.x_id_pos}"
            session = self.env['pos.session'].create({
                'name': session_name,
                'x_id_pos': session_x_id_pos,
                'config_id': pos_config.id,
                'user_id': self.env.user.id,
                'state': 'opened',
                'cash_register_balance_start': 0.0,
                'cash_register_balance_end_real': total_sales,
            })
        else:
            session.write({
                'cash_register_balance_start': 0.0,
                'cash_register_balance_end_real': total_sales,
            })

        return session

    def _create_pos_session_for_closure(self, location=None, pos_control_id=None, closure=None):
        """ Helper para crear una nueva pos.session dedicada a un corte de caja en su PdV correspondiente """
        pos_config = self._get_or_create_register_pos_config(location, pos_control_id)

        session_x_id_pos = f"POS-SESSION-CLOSURE-{closure.x_id_pos or closure.id}" if closure else False
        session = False
        if session_x_id_pos:
            session = self.env['pos.session'].search([('x_id_pos', '=', session_x_id_pos)], limit=1)

        if not session:
            open_sessions = self.env['pos.session'].search([('config_id', '=', pos_config.id), ('state', '!=', 'closed')])
            for os in open_sessions:
                try:
                    os.write({'state': 'closed', 'stop_at': fields.Datetime.now()})
                except Exception:
                    pass

            session_name = f"{pos_config.name}/Corte-{closure.name if closure else 'Sync'}"
            session_vals = {
                'name': session_name,
                'config_id': pos_config.id,
                'user_id': self.env.user.id,
                'state': 'opened',
            }
            if session_x_id_pos:
                session_vals['x_id_pos'] = session_x_id_pos
            session = self.env['pos.session'].create(session_vals)

        return session

    def _sync_incomes(self):
        """ Sincroniza cierres de caja y sus correspondientes tickets de venta como pos.order """
        log_lines = []
        processed = 0
        failed = 0
        
        log_lines.append("<h5>Obteniendo Cortes de Caja...</h5>")
        params = {}
        if self.start_date:
            s_date = self.start_date.strftime('%Y-%m-%d')
            params['start_date'] = s_date
            params['startDate'] = s_date
        if self.end_date:
            e_date = self.end_date.strftime('%Y-%m-%d')
            params['end_date'] = e_date
            params['endDate'] = e_date
        elif self.start_date:
            s_date = self.start_date.strftime('%Y-%m-%d')
            params['end_date'] = s_date
            params['endDate'] = s_date

            
        try:
            for page, closures in self._call_api_paginated('processes/closures', limit=100, extra_params=params):
                if not closures:
                    continue
                log_lines.append(f"<p>Procesando lote de cierres de caja (Página {page}, {len(closures)} cierres)...</p>")
                
                for c in closures:
                    closure_id_pos = c.get('posClosureId') or c.get('pos_closure_id') or c.get('id')
                    if not closure_id_pos:
                        continue
                
                    # Buscar ubicación/almacén
                    wh_id = c.get('posWarehouseId') or c.get('pos_warehouse_id')
                    loc = self.env['stock.location'].search([('x_id_pos', '=', str(wh_id))], limit=1)
                    
                    # Buscar responsable
                    resp_id = c.get('responsibleId') or c.get('responsible_id')
                    employee = self.env['hr.employee'].search([('x_id_pos', '=', str(resp_id))], limit=1)
                    
                    totals = c.get('totals', {})
                    session_num_str = str(c.get('sessionNumber') or c.get('session_number') or '')
                    pos_control_id = c.get('posControlId') or c.get('pos_control_id')
                    
                    vals = {
                        'x_id_pos': str(closure_id_pos),
                        'session_number': session_num_str,
                        'location_id': loc.id if loc else False,
                        'closing_date': c.get('closingDate') or c.get('closing_date'),
                        'closing_time': c.get('closingTime') or c.get('closing_time'),
                        'total_sales': c.get('totalSales') or c.get('total_sales') or 0.0,
                        'total_refunds': c.get('totalRefunds') or c.get('total_refunds') or 0.0,
                        'total_shortage': c.get('totalShortage') or c.get('total_shortage'),
                        'shortage_notes': c.get('shortageNotes') or c.get('shortage_notes'),
                        'responsible_id': employee.id if employee else False,
                        'card_total': totals.get('card', 0.0),
                        'cash_total': totals.get('cash', 0.0),
                        'transfers_total': totals.get('transfers', 0.0),
                        'checks_total': totals.get('checks', 0.0),
                        'other_total': totals.get('other', 0.0),
                        'notes': c.get('notes'),
                        'state': 'synced'
                    }
                    
                    closure = self.env['plows.pos.closure'].search([('x_id_pos', '=', str(closure_id_pos))], limit=1)
                    if not closure:
                        closure = self.env['plows.pos.closure'].create(vals)
                        log_lines.append(f"<p style='color:green;'><b>Cierre Creado:</b> Folio {closure.name} (ID POS: {closure_id_pos})</p>")
                    else:
                        closure.write(vals)
                        log_lines.append(f"<p><b>Cierre Actualizado:</b> Folio {closure.name}</p>")
                    
                    # Determinar si el cierre tiene control de caja (pos_control_id)
                    if not pos_control_id:
                        # Modo Sin Control de Caja -> Sesión Simulada por Almacén
                        pos_config = self._get_or_create_warehouse_pos_config(loc)
                        pos_session = self._create_simulated_pos_session(pos_config, closure)
                        log_lines.append(f"<p><b>Sesión Simulada:</b> POS Config '{pos_config.name}' (Apertura $0.00 / Cierre ${closure.total_sales:.2f})</p>")
                    else:
                        # Modo Con Control de Caja -> Sesión Real por Caja
                        pos_config = self._get_or_create_register_pos_config(loc, pos_control_id)
                        pos_session = self._create_pos_session_for_closure(loc, pos_control_id, closure)
                        log_lines.append(f"<p><b>Sesión Real:</b> POS Config '{pos_config.name}' (Caja ID {pos_control_id})</p>")

                    # Sincronizar Tickets de este cierre
                    try:
                        tickets = self._call_api(f'processes/closures/{closure_id_pos}/tickets')
                        log_lines.append(f"<ul><li>Cierre {closure.name}: Descargando {len(tickets)} tickets.</li>")
                        
                        for t in tickets:
                            try:
                                tkt_id_pos = t.get('posTicketId') or t.get('pos_ticket_id') or t.get('id')
                                if not tkt_id_pos:
                                    continue
                                    
                                pos_order = self.env['pos.order'].search([('x_id_pos', '=', str(tkt_id_pos))], limit=1)
                                if pos_order:
                                    pos_order.write({
                                        'session_id': pos_session.id,
                                        'x_closure_id': closure.id,
                                    })
                                    log_lines.append(f"<li>Ticket {pos_order.name} (ID POS: {tkt_id_pos}) ya importado. Actualizada sesión/cierre.</li>")
                                    continue
                                    
                                # Determinar cliente
                                cust_id = t.get('posCustomerId') or t.get('pos_customer_id')
                                partner = self.env['res.partner'].search([('x_id_pos', '=', str(cust_id))], limit=1) if cust_id else False
                                if not partner:
                                    default_cust_param = self.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.default_customer_id')
                                    if default_cust_param:
                                        partner = self.env['res.partner'].browse(int(default_cust_param))
                                if not partner:
                                    partner = self.env['res.partner'].search([('name', '=ilike', 'Público General')], limit=1)
                                if not partner:
                                    partner = self.env['res.partner'].search([], limit=1)
                                    
                                no_mov = str(t.get('noMov') or t.get('no_mov') or f"POS/{tkt_id_pos}")
                                order_date_raw = t.get('orderDate') or t.get('order_date')
                                if isinstance(order_date_raw, str):
                                    order_date = order_date_raw.replace('T', ' ')
                                else:
                                    order_date = order_date_raw or fields.Datetime.now()
                                
                                order_vals = {
                                    'name': no_mov,
                                    'x_id_pos': str(tkt_id_pos),
                                    'x_no_mov': no_mov,
                                    'x_closure_id': closure.id,
                                    'session_id': pos_session.id,
                                    'partner_id': partner.id if partner else False,
                                    'date_order': order_date,
                                    'company_id': self.env.company.id,
                                    'amount_tax': 0.0,
                                    'amount_total': 0.0,
                                    'amount_paid': 0.0,
                                    'amount_return': 0.0,
                                    'state': 'paid',
                                    'lines': []
                                }
                                
                                total_tax = 0.0
                                total_amount = 0.0

                                for line in t.get('lines', []):
                                    pos_prod_id = line.get('posProductId') or line.get('pos_product_id')
                                    product = False
                                    if pos_prod_id:
                                        product = self.env['product.product'].search(['|', ('x_id_pos', '=', str(pos_prod_id)), ('x_id_exhibicion_pos', '=', str(pos_prod_id))], limit=1)
                                    if not product:
                                        pos_prod_name = line.get('posProductName') or line.get('pos_product_name') or ''
                                        if pos_prod_name:
                                            first_code = pos_prod_name.split()[0]
                                            product = self.env['product.product'].search([('default_code', '=', first_code)], limit=1)
                                    if not product:
                                        product = self.env['product.product'].search([], limit=1)
                                        
                                    qty = float(line.get('qty', 1.0))
                                    price_unit = float(line.get('priceUnit') or line.get('price_unit') or 0.0)
                                    discount = float(line.get('discount') or 0.0)
                                    subtotal = float(line.get('priceSubtotal') or line.get('price_subtotal') or (qty * price_unit))
                                    tax_amt = float(line.get('taxAmount') or line.get('tax_amount') or 0.0)
                                    subtotal_incl = float(line.get('priceTotal') or line.get('price_total') or (subtotal + tax_amt))

                                    total_tax += tax_amt
                                    total_amount += subtotal_incl

                                    pos_tax_id = line.get('posTaxId') or line.get('pos_tax_id')
                                    tax_ids = []
                                    if pos_tax_id:
                                        tax_rule = self.env['plows.pos.tax.rule'].search([('name', '=', str(pos_tax_id))], limit=1)
                                        if tax_rule and tax_rule.odoo_tax_id:
                                            tax_ids = [(4, tax_rule.odoo_tax_id.id)]
                                            
                                    order_vals['lines'].append((0, 0, {
                                        'product_id': product.id if product else False,
                                        'qty': qty,
                                        'price_unit': price_unit,
                                        'discount': discount,
                                        'price_subtotal': subtotal,
                                        'price_subtotal_incl': subtotal_incl,
                                        'tax_ids': tax_ids
                                    }))
                                    
                                order_vals['amount_tax'] = total_tax
                                order_vals['amount_total'] = total_amount
                                order_vals['amount_paid'] = total_amount

                                new_order = self.env['pos.order'].create(order_vals)
                                
                                # Agregar pagos
                                for p in t.get('payments', []):
                                    pm_name = p.get('posPaymentMethod') or p.get('pos_payment_method') or 'EFECTIVO'
                                    pm_amount = float(p.get('amount') or p.get('paid') or total_amount)
                                    
                                    rule = self.env['plows.pos.payment.rule'].search([('name', '=ilike', pm_name)], limit=1)
                                    odoo_pm = rule.odoo_payment_method_id if (rule and rule.odoo_payment_method_id) else False
                                    if not odoo_pm:
                                        odoo_pm = self.env['pos.payment.method'].search([], limit=1)
                                        
                                    if odoo_pm:
                                        self.env['pos.payment'].create({
                                            'pos_order_id': new_order.id,
                                            'payment_method_id': odoo_pm.id,
                                            'amount': pm_amount,
                                            'payment_date': order_date,
                                        })

                                log_lines.append(f"<li style='color:green;'>Ticket pos.order importado: {new_order.name} (${total_amount:.2f})</li>")
                                processed += 1
                            except Exception as ticket_err:
                                failed += 1
                                log_lines.append(f"<li style='color:red;'>Error procesando ticket en cierre {closure_id_pos}: {str(ticket_err)}</li>")
                                
                        log_lines.append("</ul>")
                        
                    except Exception as e:
                        failed += 1
                        log_lines.append(f"<p style='color:red;'>Error descargando tickets para cierre {closure_id_pos}: {str(e)}</p>")
                    
                    # Cerrar formalmente la sesión al finalizar la importación del corte
                    try:
                        pos_session.write({'state': 'closed', 'stop_at': fields.Datetime.now()})
                    except Exception as session_close_err:
                        _logger.warning(f"No se pudo cambiar estado de pos.session {pos_session.id} a closed: {session_close_err}")

                        
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

    @api.model
    def _sync_single_entity(self, entity_type, entity_id, change_type='updated'):
        """
        Sincroniza una entidad individual desde Plows POS REST API impulsada por un evento Webhook (Pull On Demand).
        """
        _logger.info(f"Sincronización única por Webhook: type={entity_type}, id={entity_id}, action={change_type}")

        job = self.search([('state', 'in', ['draft', 'running', 'queued'])], order='id desc', limit=1)
        if not job:
            job = self.create({
                'name': f"Webhook Job {entity_type} {entity_id}",
                'state': 'running',
                'execution_start_date': fields.Datetime.now()
            })

        if entity_type == 'product':
            data = None
            if entity_id:
                try:
                    data = job._call_api(f'catalogs/products/{entity_id}')
                except Exception:
                    data = None
            if not data:
                data = job._call_api('catalogs/products', params={'limit': 500})
            items = data if isinstance(data, list) else ([data] if data else [])
            if entity_id:
                items = [item for item in items if str(item.get('posProductId') or item.get('pos_product_id') or item.get('id')) == str(entity_id)] or items
            if items:
                job._sync_products_batch(items)

        elif entity_type == 'customer':
            data = None
            if entity_id:
                try:
                    data = job._call_api(f'catalogs/customers/{entity_id}')
                except Exception:
                    data = None
            if not data:
                data = job._call_api('catalogs/customers', params={'limit': 500})
            items = data if isinstance(data, list) else ([data] if data else [])
            if entity_id:
                items = [item for item in items if str(item.get('posCustomerId') or item.get('pos_customer_id') or item.get('id')) == str(entity_id)] or items
            if items:
                job._sync_customers_batch(items)

        elif entity_type == 'supplier':
            data = None
            if entity_id:
                try:
                    data = job._call_api(f'catalogs/suppliers/{entity_id}')
                except Exception:
                    data = None
            if not data:
                data = job._call_api('catalogs/suppliers', params={'limit': 500})
            items = data if isinstance(data, list) else ([data] if data else [])
            if entity_id:
                items = [item for item in items if str(item.get('posSupplierId') or item.get('pos_supplier_id') or item.get('id')) == str(entity_id)] or items
            if items:
                job._sync_suppliers_batch(items)

        elif entity_type in ['warehouse', 'location']:
            data = None
            if entity_id:
                try:
                    data = job._call_api(f'catalogs/warehouses/{entity_id}')
                except Exception:
                    data = None
            if not data:
                data = job._call_api('catalogs/warehouses', params={'limit': 500})
            items = data if isinstance(data, list) else ([data] if data else [])
            if entity_id:
                items = [item for item in items if str(item.get('posWarehouseId') or item.get('pos_warehouse_id') or item.get('id')) == str(entity_id)] or items
            if items:
                job._sync_locations_batch(items)

        elif entity_type == 'employee':
            data = None
            if entity_id:
                try:
                    data = job._call_api(f'catalogs/employees/{entity_id}')
                except Exception:
                    data = None
            if not data:
                data = job._call_api('catalogs/employees', params={'limit': 500})
            items = data if isinstance(data, list) else ([data] if data else [])
            if entity_id:
                items = [item for item in items if str(item.get('posEmployeeId') or item.get('pos_employee_id') or item.get('id')) == str(entity_id)] or items
            if items:
                job._sync_employees_batch(items)

        elif entity_type == 'tax':
            data = job._call_api('catalogs/taxes')
            items = data if isinstance(data, list) else ([data] if data else [])
            if items:
                job._sync_taxes_batch(items)

        elif entity_type == 'payment_method':
            data = job._call_api('catalogs/payment-methods')
            items = data if isinstance(data, list) else ([data] if data else [])
            if items:
                job._sync_payment_methods_batch(items)

        elif entity_type in ['closure', 'ticket']:
            job._sync_incomes()

        return True

