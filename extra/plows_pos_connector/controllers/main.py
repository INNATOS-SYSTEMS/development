# -*- coding: utf-8 -*-
import json
import hmac
import hashlib
import logging
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class PlowsWebhookController(http.Controller):

    def _verify_hmac_signature(self, raw_body_bytes, sig_header, secret):
        """
        Verifica la firma HMAC-SHA256 enviada en la cabecera X-Plows-Signature.
        Formato de cabecera: t=timestamp,v1=hex_signature
        Fórmula: string_to_sign = timestamp + "." + raw_body
        """
        if not sig_header or not secret:
            return False, "Cabecera X-Plows-Signature o secreto no configurado en Odoo."

        try:
            parts = dict(pair.split('=', 1) for pair in sig_header.split(','))
            timestamp = parts.get('t')
            incoming_sig = parts.get('v1')
        except Exception:
            return False, "Formato de cabecera X-Plows-Signature inválido."

        if not timestamp or not incoming_sig:
            return False, "Faltan componentes 't' o 'v1' en la firma recibida."

        raw_body_str = raw_body_bytes.decode('utf-8')
        string_to_sign = f"{timestamp}.{raw_body_str}".encode('utf-8')
        expected_sig = hmac.new(secret.encode('utf-8'), string_to_sign, hashlib.sha256).hexdigest().lower()

        if not hmac.compare_digest(incoming_sig.lower(), expected_sig):
            return False, "Firma HMAC-SHA256 no válida."

        return True, None

    @http.route('/plows/webhook/receiver', type='json', auth='public', methods=['POST'], csrf=False)
    def receive_plows_webhook(self, **kw):
        """
        Endpoint receptor de notificaciones Webhook desde la API de Plows POS.
        1. Valida cabeceras X-Plows-Signature y X-Plows-Event-Id.
        2. Verifica firma HMAC-SHA256.
        3. Verifica idempotencia por event_id en plows.pos.webhook.log.
        4. Despacha la sincronización de la entidad específica.
        """
        httprequest = request.httprequest
        raw_body_bytes = httprequest.data
        sig_header = httprequest.headers.get('X-Plows-Signature')
        event_id = httprequest.headers.get('X-Plows-Event-Id')

        # Obtener payload parseado
        try:
            payload = request.jsonrequest or json.loads(raw_body_bytes.decode('utf-8'))
        except Exception:
            payload = {}

        if not event_id and payload.get('event_id'):
            event_id = payload.get('event_id')

        if not event_id:
            return {'status': 'error', 'message': 'Cabecera X-Plows-Event-Id faltante.'}

        # 1. Obtener secreto configurado en Odoo
        secret = request.env['ir.config_parameter'].sudo().get_param('plows_pos_connector.webhook_secret', default='')
        
        # 2. Verificación de Seguridad HMAC (Si hay secreto configurado)
        if secret:
            is_valid, err_msg = self._verify_hmac_signature(raw_body_bytes, sig_header, secret)
            if not is_valid:
                _logger.warning(f"Intento de Webhook rechazado por seguridad en event_id {event_id}: {err_msg}")
                return {'status': 'error', 'message': err_msg}

        # 3. Idempotencia: Verificar si event_id ya existe en la bitácora
        log_model = request.env['plows.pos.webhook.log'].sudo()
        existing_log = log_model.search([('event_id', '=', event_id)], limit=1)
        if existing_log:
            _logger.info(f"Webhook event_id {event_id} omitido por duplicado (idempotencia).")
            return {
                'status': 'success',
                'event_id': event_id,
                'action': 'already_processed',
                'log_id': existing_log.id
            }

        # 4. Crear registro en bitácora local
        entity_type = payload.get('entity_type', 'ping')
        entity_id = str(payload.get('entity_id', ''))
        change_type = payload.get('change_type', 'created')
        tenant_id = payload.get('tenant_id', 1)

        log_record = log_model.create({
            'event_id': event_id,
            'tenant_id': tenant_id,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'change_type': change_type,
            'raw_payload': raw_body_bytes.decode('utf-8', errors='ignore'),
            'state': 'pending',
            'received_at': fields.Datetime.now()
        })

        # Caso especial: Ping test
        if entity_type == 'ping':
            log_record.write({'state': 'processed', 'processed_at': fields.Datetime.now()})
            return {
                'status': 'success',
                'event_id': event_id,
                'action': 'ping_ack'
            }

        # 5. Despachar sincronización bajo demanda (Pull On Demand)
        try:
            sync_job_model = request.env['plows.pos.sync.job'].sudo()
            sync_job_model._sync_single_entity(entity_type, entity_id, change_type)
            log_record.write({
                'state': 'processed',
                'processed_at': fields.Datetime.now()
            })
            _logger.info(f"Webhook event_id {event_id} ({entity_type} {entity_id}) procesado exitosamente.")
            return {
                'status': 'success',
                'event_id': event_id,
                'action': 'processed'
            }
        except Exception as e:
            error_text = str(e)
            _logger.error(f"Error procesando Webhook event_id {event_id}: {error_text}")
            log_record.write({
                'state': 'failed',
                'error_message': error_text
            })
            return {
                'status': 'error',
                'event_id': event_id,
                'message': f"Error en sincronización: {error_text}"
            }
