# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase


class TestSyncDashboard(TransactionCase):

    def setUp(self):
        super(TestSyncDashboard, self).setUp()
        self.Dashboard = self.env['plows.pos.sync.dashboard']
        self.SyncJob = self.env['plows.pos.sync.job']

    def test_01_overall_health_calculation(self):
        """ US1 (T006): Verificar afirmación cualitativa de salud (healthy, degraded, critical) sin datos de conteo crudos """
        status = self.Dashboard.get_dashboard_status()
        self.assertIn('overall_health', status)
        health = status['overall_health']
        self.assertIn(health['health_code'], ['healthy', 'degraded', 'critical'])
        self.assertTrue(health['health_title'])
        self.assertTrue(health['health_message'])

    def test_02_catalog_verifiers_aggregation(self):
        """ US2 (T010): Verificar verificadores consolidados de catálogo (sin números crudos) """
        status = self.Dashboard.get_dashboard_status()
        self.assertIn('catalog_verifiers', status)
        verifiers = status['catalog_verifiers']
        self.assertTrue(len(verifiers) >= 4)
        cat_keys = [v['catalog_key'] for v in verifiers]
        self.assertIn('products', cat_keys)
        self.assertIn('customers', cat_keys)

        for v in verifiers:
            self.assertIn(v['status_code'], ['up_to_date', 'syncing', 'warning', 'failed'])
            self.assertTrue(v['status_label'])

    def test_03_active_warnings_and_recent_closures_feed(self):
        """ US3 (T014): Verificar lista de advertencias activas y feed de cierres de caja recienes """
        status = self.Dashboard.get_dashboard_status()
        self.assertIn('warnings', status)
        self.assertIn('recent_closures', status)

        closures = status['recent_closures']
        self.assertTrue(isinstance(closures, list))
