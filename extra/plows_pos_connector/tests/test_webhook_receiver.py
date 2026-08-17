# -*- coding: utf-8 -*-
import hmac
import hashlib
import json
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestPlowsWebhookReceiver(HttpCase):

    def setUp(self):
        super(TestPlowsWebhookReceiver, self).setUp()
        self.secret = 'secreto_prueba_test_12345'
        self.env['ir.config_parameter'].sudo().set_param('plows_pos_connector.webhook_secret', self.secret)

    def test_01_webhook_valid_hmac_and_idempotency(self):
        """ Prueba la recepción exitosa de un webhook con firma HMAC válida y su idempotencia ante duplicados. """
        url = '/plows/webhook/receiver'
        event_id = 'test-evt-uuid-001'
        timestamp = '2026-08-13T12:00:00Z'

        payload = {
            'event_id': event_id,
            'tenant_id': 1,
            'entity_type': 'ping',
            'entity_id': '0',
            'change_type': 'created',
            'timestamp': timestamp
        }

        raw_body_bytes = json.dumps(payload).encode('utf-8')
        string_to_sign = f"{timestamp}.{raw_body_bytes.decode('utf-8')}".encode('utf-8')
        expected_sig = hmac.new(self.secret.encode('utf-8'), string_to_sign, hashlib.sha256).hexdigest().lower()

        headers = {
            'Content-Type': 'application/json',
            'X-Plows-Event-Id': event_id,
            'X-Plows-Signature': f"t={timestamp},v1={expected_sig}"
        }

        # 1. Envío inicial
        res1 = self.url_open(url, data=raw_body_bytes, headers=headers)
        self.assertEqual(res1.status_code, 200)

        log = self.env['plows.pos.webhook.log'].sudo().search([('event_id', '=', event_id)], limit=1)
        self.assertTrue(log, "El log del webhook debe haberse registrado en la base de datos.")
        self.assertEqual(log.state, 'processed')

        # 2. Envío duplicado (Idempotencia)
        res2 = self.url_open(url, data=raw_body_bytes, headers=headers)
        self.assertEqual(res2.status_code, 200)

    def test_02_webhook_invalid_hmac_rejected(self):
        """ Prueba que las peticiones con firmas HMAC inválidas sean rechazadas. """
        url = '/plows/webhook/receiver'
        event_id = 'test-evt-uuid-bad'
        timestamp = '2026-08-13T12:00:00Z'

        payload = {
            'event_id': event_id,
            'tenant_id': 1,
            'entity_type': 'ping',
            'entity_id': '0',
            'change_type': 'created'
        }

        raw_body_bytes = json.dumps(payload).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'X-Plows-Event-Id': event_id,
            'X-Plows-Signature': f"t={timestamp},v1=bad_signature_1234567890"
        }

        res = self.url_open(url, data=raw_body_bytes, headers=headers)
        self.assertEqual(res.status_code, 200)
        res_json = res.json()
        result = res_json.get('result', {})
        self.assertEqual(result.get('status'), 'error')
