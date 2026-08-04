# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch
from odoo.tests.common import TransactionCase
from odoo import fields

class TestPlowsPosClosureSync(TransactionCase):

    def setUp(self):
        super(TestPlowsPosClosureSync, self).setUp()
        self.location = self.env['stock.location'].create({
            'name': 'Ubicación de Test',
            'x_id_pos': '3',
            'usage': 'internal'
        })
        self.employee = self.env['hr.employee'].create({
            'name': 'Juan Pérez',
            'x_id_pos': '5'
        })

    def test_create_closure(self):
        """ Test normal creation of closure session and name sequence generation """
        closure = self.env['plows.pos.closure'].create({
            'x_id_pos': 12,
            'session_number': 482,
            'location_id': self.location.id,
            'closing_date': '2026-08-03',
            'closing_time': '18:30:00',
            'total_sales': 12500.50,
            'responsible_id': self.employee.id,
            'cash_total': 4020.50,
            'card_total': 7500.00
        })
        self.assertNotEqual(closure.name, 'Nuevo')
        self.assertEqual(closure.x_id_pos, 12)
        self.assertEqual(closure.state, 'draft')

    @patch('requests.post')
    def test_settings_action_generate_token(self, mock_post):
        """ Test token generation action from settings """
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            'code': 200,
            'message': 'Token de empresa generado con éxito.',
            'payload': {
                'data': {
                    'tenant_id': 1,
                    'token': 'mocked_token_xyz_123'
                }
            }
        }
        
        settings = self.env['res.config.settings'].create({
            'plows_pos_api_url': 'http://localhost:5109/api/v1',
            'plows_pos_tenant_id': '1'
        })
        
        settings.action_generate_token()
        
        self.assertEqual(settings.plows_pos_api_token, 'mocked_token_xyz_123')
        self.assertEqual(settings.plows_pos_token_status, 'valid')
        self.assertIsNotNone(settings.plows_pos_token_generated_at)
        
        params = self.env['ir.config_parameter'].sudo()
        self.assertEqual(params.get_param('plows_pos_connector.api_token'), 'mocked_token_xyz_123')
        self.assertEqual(params.get_param('plows_pos_connector.token_status'), 'valid')

    @patch('requests.get')
    def test_settings_action_test_connection_valid(self, mock_get):
        """ Test connection testing when token is valid """
        mock_get.return_value.status_code = 200
        
        settings = self.env['res.config.settings'].create({
            'plows_pos_api_url': 'http://localhost:5109/api/v1',
            'plows_pos_api_token': 'some_token'
        })
        
        res = settings.action_test_token_connection()
        self.assertEqual(settings.plows_pos_token_status, 'valid')
        self.assertEqual(res.get('tag'), 'display_notification')

    @patch('requests.post')
    @patch('requests.request')
    def test_sync_job_auto_renew_on_401(self, mock_request, mock_post):
        """ Test that sync job auto-renews token when encountering 401 """
        mock_response_401 = unittest.mock.MagicMock()
        mock_response_401.status_code = 401
        
        mock_response_200 = unittest.mock.MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {
            'code': 200,
            'payload': {
                'data': []
            }
        }
        
        mock_request.side_effect = [mock_response_401, mock_response_200]
        
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            'code': 200,
            'payload': {
                'data': {
                    'token': 'new_auto_renewed_token'
                }
            }
        }
        
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('plows_pos_connector.api_url', 'http://localhost:5109/api/v1')
        params.set_param('plows_pos_connector.api_token', 'old_expired_token')
        
        job = self.env['plows.pos.sync.job'].create({
            'name': 'Test Sync Job',
        })

        
        data = job._call_api('catalogs/taxes')
        
        self.assertEqual(params.get_param('plows_pos_connector.api_token'), 'new_auto_renewed_token')
        self.assertEqual(data, [])
