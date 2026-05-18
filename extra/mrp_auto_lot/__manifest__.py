{
    'name': "Manejo de Lotes y Caducidades en MRP",

    'summary': "Identificación de componentes formulados y manejo de lotes de forma automática, fechas de caducidad, etc.",

    'description': """
Módulo para identificar componentes formulados y manejar lotes automáticamente.
    """,

    'author': "INNATOS",
    'website': "https://www.innatos.com.mx",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Manufacturing/Manufacturing',
    'version': '19.0.1.0',

    # any module necessary for this one to work correctly
    'depends': ['base', 'product', 'mrp', 'stock'],

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/views.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
    ],
}

