# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from unittest.mock import patch


class TestCatalogExtendedSync(TransactionCase):

    def setUp(self):
        super().setUp()
        self.SyncJob = self.env['plows.pos.sync.job']
        self.job = self.SyncJob.create({
            'name': 'Test Extended Sync Job'
        })

    @patch.object(SyncJob, '_call_api')
    def test_sync_catalogs_extended_mapping(self, mock_api):
        """ Prueba que los atributos extendidos de productos, contactos, almacenes y empleados se mapeen correctamente """
        mock_api.side_effect = lambda endpoint: {
            'catalogs/taxes': [],
            'catalogs/warehouses': [{
                'id': 'WH-EXT-01',
                'name': 'Sucursal Norte',
                'code': 'SUC-NOR',
                'address': 'Av. Universidad 500'
            }],
            'catalogs/products': [{
                'id': 'PROD-EXT-01',
                'name': 'Café Americano 16oz',
                'sku': 'CAF-AME-16',
                'price': 48.50,
                'cost': 12.00,
                'barcode': '7509998877665',
                'description': 'Café de grano recién molido',
                'category_name': 'Bebidas Calientes',
                'uom_name': 'Units',
                'active': True
            }],
            'catalogs/customers': [{
                'id': 'CUST-EXT-01',
                'name': 'Empresa Prueba S.A. de C.V.',
                'rfc': 'EPR180101XYZ',
                'phone': '5511223344',
                'mobile': '5599887766',
                'email': 'contacto@empresaprueba.com',
                'street': 'Calle Reforma 100',
                'zip': '06600',
                'city': 'CDMX'
            }],
            'catalogs/suppliers': [{
                'id': 'SUPP-EXT-01',
                'name': 'Proveedor de Granos S.A.',
                'vat': 'PGR100505ABC',
                'phone': '5544332211',
                'email': 'ventas@proveedorgranos.com'
            }],
            'catalogs/employees': [{
                'id': 'EMP-EXT-01',
                'name': 'María López',
                'email': 'maria.lopez@plows.com',
                'mobile': '5533445566',
                'job_title': 'Supervisora de Barra'
            }]
        }.get(endpoint, [])

        processed, failed = self.job._sync_catalogs_phase()

        self.assertGreater(processed, 0)
        self.assertEqual(failed, 0)

        # 1. Verificar Producto Extendido
        product = self.env['product.product'].search([('x_id_pos', '=', 'PROD-EXT-01')], limit=1)
        self.assertTrue(product)
        self.assertEqual(product.name, 'Café Americano 16oz')
        self.assertEqual(product.default_code, 'CAF-AME-16')
        self.assertEqual(product.list_price, 48.50)
        self.assertEqual(product.standard_price, 12.00)
        self.assertEqual(product.barcode, '7509998877665')
        self.assertEqual(product.description_sale, 'Café de grano recién molido')
        self.assertEqual(product.categ_id.name, 'Bebidas Calientes')

        # 2. Verificar Cliente Extendido
        customer = self.env['res.partner'].search([('x_id_pos', '=', 'CUST-EXT-01')], limit=1)
        self.assertTrue(customer)
        self.assertEqual(customer.name, 'Empresa Prueba S.A. de C.V.')
        self.assertEqual(customer.vat, 'EPR180101XYZ')
        self.assertEqual(customer.email, 'contacto@empresaprueba.com')
        self.assertEqual(customer.street, 'Calle Reforma 100')
        self.assertEqual(customer.zip, '06600')
        self.assertEqual(customer.customer_rank, 1)

        # 3. Verificar Ubicación Extendida
        location = self.env['stock.location'].search([('x_id_pos', '=', 'WH-EXT-01')], limit=1)
        self.assertTrue(location)
        self.assertEqual(location.name, 'Sucursal Norte')
        self.assertEqual(location.x_warehouse_code, 'SUC-NOR')
        self.assertEqual(location.x_notes, 'Av. Universidad 500')

        # 4. Verificar Empleado Extendido
        employee = self.env['hr.employee'].search([('x_id_pos', '=', 'EMP-EXT-01')], limit=1)
        self.assertTrue(employee)
        self.assertEqual(employee.name, 'María López')
        self.assertEqual(employee.work_email, 'maria.lopez@plows.com')
        self.assertEqual(employee.mobile_phone, '5533445566')
        self.assertEqual(employee.job_title, 'Supervisora de Barra')
