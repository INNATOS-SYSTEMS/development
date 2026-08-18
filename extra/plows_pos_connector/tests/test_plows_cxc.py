# -*- coding: utf-8 -*-
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestPlowsReceivableCxc(TransactionCase):

    def setUp(self):
        super(TestPlowsReceivableCxc, self).setUp()
        self.CxcModel = self.env['plows.receivable.cxc']
        self.PartnerModel = self.env['res.partner']
        self.PosOrderModel = self.env['pos.order']

        # Crear contacto y ticket con x_id_pos para verificar búsqueda relacional
        self.test_partner = self.PartnerModel.create({
            'name': 'Cliente Test PLOWS',
            'x_id_pos': 'CUST-100',
        })
        self.test_pos_order = self.PosOrderModel.create({
            'name': 'Ticket Test 001',
            'x_id_pos': '500',
        })

    @patch('requests.get')
    def test_action_sync_receivables_mapping(self, mock_get):
        """ Verificar que action_sync_receivables realice consulta HTTP directa y mapee llaves de ReceivableDto """
        mock_payload = [
            {
                "pos_customer_id": "CUST-100",
                "pos_costumer_name": "Cliente Test PLOWS",
                "associated_customer_id": 99,
                "associated_customer_name": "Grupo Empresarial",
                "pos_ticket_id": 500,
                "pos_invoice_id": 8899,
                "no_remision": "REM-1234",
                "pos_invoice_folio": "A-8899",
                "invoice_date": "2026-08-01T10:00:00Z",
                "invoice_due_date": "2026-08-15T23:59:59Z",
                "total": 12500.50,
                "total_nc": 500.00,
                "total_balance": 12000.50,
                "total_paid": 500.00,
                "days_past_due": 3,
                "due_type": "vencido",
                "due_bucket": "vencido_1_7"
            }
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_payload
        mock_get.return_value = mock_response

        # Ejecutar la acción de sincronización
        action = self.CxcModel.action_sync_receivables()

        # Verificar notificación devuelta
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('tag'), 'display_notification')

        # Verificar registros creados en Odoo
        records = self.CxcModel.search([])
        self.assertEqual(len(records), 1)

        rec = records[0]
        self.assertEqual(rec.cliente_plows_id, 'CUST-100')
        self.assertEqual(rec.cliente_plows_nombre, 'Cliente Test PLOWS')
        self.assertEqual(rec.cliente_asociado_id, 99)
        self.assertEqual(rec.cliente_asociado_nombre, 'Grupo Empresarial')
        self.assertEqual(rec.almacen_mov_id, '500')
        self.assertEqual(rec.factura_id_plows, '8899')
        self.assertEqual(rec.no_remision, 'REM-1234')
        self.assertEqual(rec.serie_folio, 'A-8899')
        self.assertIn('2026-08-01', str(rec.fecha_factura))
        self.assertIn('2026-08-15', str(rec.fecha_vencimiento))
        self.assertAlmostEqual(rec.total_factura, 12500.50)
        self.assertAlmostEqual(rec.total_nc, 500.00)
        self.assertAlmostEqual(rec.saldo_pendiente, 12000.50)
        self.assertAlmostEqual(rec.total_pagado, 500.00)
        self.assertEqual(rec.dias_atraso, 3)
        self.assertEqual(rec.tipo_vencimiento, 'vencido')
        self.assertEqual(rec.bucket_antiguedad, 'vencido_1_7')

        # Verificar relaciones Odoo enlazadas por x_id_pos
        self.assertEqual(rec.partner_id.id, self.test_partner.id)
        self.assertEqual(rec.pos_order_id.id, self.test_pos_order.id)

    @patch('requests.get')
    def test_action_sync_receivables_null_handling(self, mock_get):
        """ Verificar manejo seguro cuando atributos opcionales vienen en null """
        mock_payload = [
            {
                "pos_customer_id": "CUST-200",
                "pos_costumer_name": "Cliente Sin Asociado",
                "associated_customer_id": None,
                "associated_customer_name": None,
                "pos_ticket_id": None,
                "pos_invoice_id": None,
                "no_remision": "REM-99",
                "pos_invoice_folio": "B-01",
                "invoice_date": None,
                "invoice_due_date": None,
                "total": 100,
                "total_nc": 0,
                "total_balance": 100,
                "total_paid": 0,
                "days_past_due": 0,
                "due_type": "por_vencer",
                "due_bucket": "hoy"
            }
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_payload
        mock_get.return_value = mock_response

        self.CxcModel.action_sync_receivables()
        rec = self.CxcModel.search([])[0]
        self.assertFalse(rec.cliente_asociado_id)
        self.assertFalse(rec.cliente_asociado_nombre)
        self.assertFalse(rec.fecha_factura)
        self.assertFalse(rec.fecha_vencimiento)
        self.assertEqual(rec.almacen_mov_id, '')
        self.assertEqual(rec.factura_id_plows, '')
