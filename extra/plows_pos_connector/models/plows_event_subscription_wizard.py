# -*- coding: utf-8 -*-
from odoo import models, fields, api, _

class PlowsEventSubscriptionWizard(models.TransientModel):
    _name = 'plows.pos.event.subscription.wizard'
    _description = 'Asistente Modal para Suscripción de Eventos en Tiempo Real'

    event_products = fields.Boolean(string='Productos')
    event_customers = fields.Boolean(string='Clientes')
    event_suppliers = fields.Boolean(string='Proveedores')
    event_warehouses = fields.Boolean(string='Almacenes / Sucursales')
    event_employees = fields.Boolean(string='Personal / Empleados')
    event_taxes = fields.Boolean(string='Impuestos')
    event_payments = fields.Boolean(string='Métodos de Pago')
    event_closures = fields.Boolean(string='Cierres de Caja')
    event_tickets = fields.Boolean(string='Ventas / Tickets')

    @api.model
    def default_get(self, fields_list):
        res = super(PlowsEventSubscriptionWizard, self).default_get(fields_list)
        params = self.env['ir.config_parameter'].sudo()

        res.update({
            'event_products': params.get_param('plows_pos_connector.webhook_event_products', default='True') == 'True',
            'event_customers': params.get_param('plows_pos_connector.webhook_event_customers', default='True') == 'True',
            'event_suppliers': params.get_param('plows_pos_connector.webhook_event_suppliers', default='True') == 'True',
            'event_warehouses': params.get_param('plows_pos_connector.webhook_event_warehouses', default='True') == 'True',
            'event_employees': params.get_param('plows_pos_connector.webhook_event_employees', default='True') == 'True',
            'event_taxes': params.get_param('plows_pos_connector.webhook_event_taxes', default='True') == 'True',
            'event_payments': params.get_param('plows_pos_connector.webhook_event_payments', default='True') == 'True',
            'event_closures': params.get_param('plows_pos_connector.webhook_event_closures', default='True') == 'True',
            'event_tickets': params.get_param('plows_pos_connector.webhook_event_tickets', default='True') == 'True',
        })
        return res

    def action_save_subscription(self):
        """ Guarda la selección de tópicos en ir.config_parameter y llama al endpoint de suscripción en Plows API """
        self.ensure_one()
        params = self.env['ir.config_parameter'].sudo()

        params.set_param('plows_pos_connector.webhook_event_products', str(self.event_products))
        params.set_param('plows_pos_connector.webhook_event_customers', str(self.event_customers))
        params.set_param('plows_pos_connector.webhook_event_suppliers', str(self.event_suppliers))
        params.set_param('plows_pos_connector.webhook_event_warehouses', str(self.event_warehouses))
        params.set_param('plows_pos_connector.webhook_event_employees', str(self.event_employees))
        params.set_param('plows_pos_connector.webhook_event_taxes', str(self.event_taxes))
        params.set_param('plows_pos_connector.webhook_event_payments', str(self.event_payments))
        params.set_param('plows_pos_connector.webhook_event_closures', str(self.event_closures))
        params.set_param('plows_pos_connector.webhook_event_tickets', str(self.event_tickets))

        # Disparar actualización hacia la API si hay configuración de conexión
        config_settings = self.env['res.config.settings'].create({})
        return config_settings.action_register_webhook_config()
