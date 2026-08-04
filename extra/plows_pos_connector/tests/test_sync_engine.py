# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase


class TestSyncEngine(TransactionCase):

    def setUp(self):
        super(TestSyncEngine, self).setUp()
        self.SyncJob = self.env['plows.pos.sync.job']
        self.SyncTask = self.env['plows.pos.sync.task']

    def test_01_job_queuing_and_tasks_creation(self):
        """ US1 (T007): Verificar encolamiento no bloqueante de jobs y creación de tareas por catálogo """
        job = self.SyncJob.create({'name': 'TEST/ENGINE/001'})
        self.assertEqual(job.state, 'draft')
        self.assertEqual(job.global_progress, 0.0)

        job.action_queue_job()
        self.assertEqual(job.state, 'queued')
        self.assertEqual(len(job.task_ids), 4)
        catalog_names = set(job.task_ids.mapped('catalog_name'))
        self.assertIn('products', catalog_names)
        self.assertIn('customers', catalog_names)

    def test_02_progress_percentage_calculation(self):
        """ US2 (T011): Verificar cálculo preciso de progreso por catálogo y progreso global del job """
        job = self.SyncJob.create({'name': 'TEST/ENGINE/002'})
        task1 = self.SyncTask.create({
            'job_id': job.id,
            'catalog_name': 'products',
            'total_records': 1000,
            'processed_records': 500,
            'state': 'in_progress',
        })
        task2 = self.SyncTask.create({
            'job_id': job.id,
            'catalog_name': 'customers',
            'total_records': 500,
            'processed_records': 500,
            'state': 'completed',
        })

        self.assertEqual(task1.progress_percentage, 50.0)
        self.assertEqual(task2.progress_percentage, 100.0)

        # Global progress: (500 + 500) / (1000 + 500) * 100 = 1000 / 1500 * 100 = 66.67%
        self.assertEqual(job.global_progress, 66.67)

    def test_03_retry_policy_and_error_isolation(self):
        """ US3 (T015): Verificar política de 2 reintentos por página e aislamiento de errores por catálogo """
        job = self.SyncJob.create({'name': 'TEST/ENGINE/003'})
        task_prod = self.SyncTask.create({
            'job_id': job.id,
            'catalog_name': 'products',
            'total_records': 200,
            'processed_records': 100,
            'state': 'in_progress',
        })
        task_cust = self.SyncTask.create({
            'job_id': job.id,
            'catalog_name': 'customers',
            'total_records': 100,
            'processed_records': 0,
            'state': 'in_progress',
        })

        # Simular Intento 1 de fallo en task_cust
        task_cust._handle_page_error("Connection timeout on page 1")
        self.assertEqual(task_cust.current_page_retries, 1)
        self.assertEqual(task_cust.state, 'retrying')

        # Simular Intento 2 de fallo en task_cust
        task_cust._handle_page_error("Connection timeout on page 1")
        self.assertEqual(task_cust.current_page_retries, 2)
        self.assertEqual(task_cust.state, 'retrying')

        # Simular Intento 3 de fallo (excede límite de 2 reintentos)
        task_cust._handle_page_error("Connection timeout on page 1")
        self.assertEqual(task_cust.current_page_retries, 3)
        self.assertEqual(task_cust.state, 'failed')

        # Verificar que la tarea de productos continúa sin ser afectada
        self.assertEqual(task_prod.state, 'in_progress')
