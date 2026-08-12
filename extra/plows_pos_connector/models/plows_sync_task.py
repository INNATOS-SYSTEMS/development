# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class PlowsPosSyncTask(models.Model):
    _name = 'plows.pos.sync.task'
    _description = 'Plows POS Catalog Sync Task'
    _order = 'id asc'

    job_id = fields.Many2one(
        'plows.pos.sync.job',
        string='Trabajo de Sincronización',
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
        ('closures', 'Cierres de Caja y Ventas'),
    ], string='Catálogo', required=True)

    state = fields.Selection([
        ('queued', 'En Cola'),
        ('in_progress', 'En Progreso'),
        ('retrying', 'Reintentando'),
        ('paused', 'Pausado'),
        ('completed', 'Completado'),
        ('failed', 'Fallido'),
        ('skipped', 'Omitido'),
    ], string='Estado de Tarea', default='queued', required=True, index=True)

    total_records = fields.Integer(string='Total Registros', default=0)
    processed_records = fields.Integer(string='Registros Procesados', default=0)
    current_page = fields.Integer(string='Página Actual', default=1)
    page_size = fields.Integer(string='Tamaño de Página', default=100)
    last_processed_id = fields.Char(string='Último ID Procesado')
    current_page_retries = fields.Integer(string='Reintentos de Página', default=0)

    progress_percentage = fields.Float(
        string='Progreso (%)',
        compute='_compute_progress_percentage',
        store=True,
        digits=(5, 2),
    )

    error_log = fields.Text(string='Log de Errores')

    checkpoint_ids = fields.One2many(
        'plows.pos.sync.checkpoint',
        'task_id',
        string='Checkpoints de Página',
    )

    @api.depends('total_records', 'processed_records', 'state')
    def _compute_progress_percentage(self):
        for task in self:
            if task.state == 'completed':
                task.progress_percentage = 100.0
            elif task.total_records > 0:
                calc = (float(task.processed_records) / float(task.total_records)) * 100.0
                task.progress_percentage = min(100.0, round(calc, 2))
            else:
                task.progress_percentage = 0.0

    def _fetch_initial_metadata_count(self):
        """ Realiza consulta inicial de metadatos para fijar total_records al inicio (US2 / FR-032). """
        for task in self:
            if task.total_records > 0:
                continue
            if task.catalog_name == 'closures':
                params = {}
                if task.job_id.start_date:
                    s_date = task.job_id.start_date.strftime('%Y-%m-%d')
                    params['start_date'] = s_date
                    params['startDate'] = s_date
                if task.job_id.end_date:
                    e_date = task.job_id.end_date.strftime('%Y-%m-%d')
                    params['end_date'] = e_date
                    params['endDate'] = e_date
                elif task.job_id.start_date:
                    s_date = task.job_id.start_date.strftime('%Y-%m-%d')
                    params['end_date'] = s_date
                    params['endDate'] = s_date

                try:
                    _, total_count, _ = task.job_id._fetch_api_page('processes/closures', page=1, limit=1, extra_params=params)
                    if total_count > 0:
                        task.write({'total_records': total_count})
                except Exception as e:
                    _logger.warning(f"No se pudo consultar metadatos iniciales para cierres: {e}")
            else:
                endpoint_map = {
                    'products': 'catalogs/products',
                    'customers': 'catalogs/customers',
                    'suppliers': 'catalogs/suppliers',
                    'locations': 'catalogs/locations',
                    'employees': 'catalogs/employees',
                    'taxes': 'catalogs/taxes',
                    'payment_methods': 'catalogs/payment-methods',
                }
                endpoint = endpoint_map.get(task.catalog_name)
                if endpoint:
                    try:
                        _, total_count, _ = task.job_id._fetch_api_page(endpoint, page=1, limit=1)
                        if total_count > 0:
                            task.write({'total_records': total_count})
                    except Exception as e:
                        _logger.warning(f"No se pudo consultar metadatos iniciales para {task.catalog_name}: {e}")

    def _process_page_checkpoint(self, page_num, records_count, status='success', summary=None):
        """ Registra checkpoint por página y avanza puntero de paginación (SC-004). """
        for task in self:
            self.env['plows.pos.sync.checkpoint'].sudo().create({
                'task_id': task.id,
                'page_number': page_num,
                'records_count': records_count,
                'status': status,
                'response_summary': summary or f"Página {page_num} procesada exitosamente ({records_count} reg)."
            })

            new_processed = task.processed_records + records_count
            new_state = task.state
            if task.total_records > 0 and new_processed >= task.total_records:
                new_state = 'completed'
            elif task.state in ['queued', 'retrying']:
                new_state = 'in_progress'

            task.write({
                'processed_records': new_processed,
                'current_page': page_num + 1,
                'current_page_retries': 0,
                'state': new_state,
            })

    def _handle_page_error(self, error_msg):
        """ Maneja reintentos de página (máximo 2) y marca Failed si se excede (FR-007). """
        for task in self:
            new_retries = task.current_page_retries + 1
            log_entry = f"\n[Intento {new_retries}/2] Error en pág {task.current_page}: {error_msg}"
            updated_log = (task.error_log or "") + log_entry

            if new_retries <= 2:
                task.write({
                    'current_page_retries': new_retries,
                    'state': 'retrying',
                    'error_log': updated_log,
                })
                _logger.warning(f"Tarea {task.catalog_name} id={task.id} reintentando página {task.current_page} (intento {new_retries}/2)")
            else:
                task.write({
                    'current_page_retries': new_retries,
                    'state': 'failed',
                    'error_log': updated_log + "\n[FALLO FINAL] Reintentos agotados para esta página.",
                })
                _logger.error(f"Tarea {task.catalog_name} id={task.id} falló tras {new_retries} intentos en página {task.current_page}")
