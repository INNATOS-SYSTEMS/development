# -*- coding: utf-8 -*-
{
    'name': 'Plows POS Connector',
    'version': '19.0.1.1.0',
    'category': 'Sales/Point of Sale',
    'summary': 'Sincronizador e integrador financiero entre Plows POS y el ORM de Odoo 19.',
    'description': """
Plows POS Connector
===================
Este módulo actúa como cliente financiero mediante arquitectura Pull.
Se conecta a las APIs expuestas por Plows POS para descargar y sincronizar:
- Catálogos Maestros (Productos, Clientes, Proveedores, Sucursales y Empleados).
- Flujos de Ingresos y Egresos Consolidados.
- Cierres de Inventario y valoración física/financiera directa al costo promedio (Odoo 19 product.value).
    """,
    'author': 'Plows POS',
    'website': 'https://www.plowsware.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'product',
        'stock',
        'account',
        'analytic',
        'sale',
        'purchase',
        'hr',
        'website_sale',
        'point_of_sale',
    ],

    'data': [
        # Security — load first so models have access rules
        'security/plows_security.xml',
        'security/ir.model.access.csv',
        # Data
        'data/cron_data.xml',
        'data/sequences_data.xml',
        # Views — actions before menus
        'views/res_config_settings_views.xml',
        'views/product_views.xml',
        'views/partner_views.xml',
        'views/stock_location_views.xml',
        'views/hr_employee_views.xml',
        'views/plows_mapping_views.xml',
        'views/plows_field_mapping_views.xml',
        'views/plows_sync_job_views.xml',
        'views/plows_pos_sync_log_views.xml',
        'views/plows_pos_closure_views.xml',
        'views/pos_order_views.xml',
        'views/plows_pos_expense_views.xml',
        'views/plows_pos_inventory_views.xml',
        'views/plows_dashboard_views.xml',
        'views/plows_webhook_log_views.xml',
        'views/plows_event_subscription_wizard_views.xml',
        # Menus — always last
        'views/plows_menus.xml',

    ],

    'assets': {
        'web.assets_backend': [
            'plows_pos_connector/static/src/js/plows_sync_bus.js',
            'plows_pos_connector/static/src/components/pos_sync_dashboard/pos_sync_dashboard.js',
            'plows_pos_connector/static/src/components/pos_sync_dashboard/pos_sync_dashboard.xml',
            'plows_pos_connector/static/src/components/pos_sync_dashboard/pos_sync_dashboard.scss',
        ],
    },

    'installable': True,
    'application': True,
    'auto_install': False,
}
