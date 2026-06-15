{
    'name': "Manejo de Lotes y Caducidades en MRP",

    'summary': "Identificación de componentes formulados y manejo de lotes de forma automática, fechas de caducidad, etc.",

    'description': """
Módulo para identificar componentes formulados y manejar lotes automáticamente.
    """,
    'author': "INNATOS",
    'website': "https://www.innatos.com.mx",
    # Categories can be used to filter modules in modules listing
    'category': 'Manufacturing/Manufacturing',
    'version': '19.0.1.0',

    # any module necessary for this one to work correctly
    'depends': ['base', 'base_setup', 'product', 'mrp', 'stock'],

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/views.xml',
        'views/res_config_settins_views.xml',
        'wizard/mrp_production_split_views.xml',
    ],
    'license': 'LGPL-3',
}

