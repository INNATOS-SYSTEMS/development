from odoo import models, fields, api, _

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_formulated = fields.Boolean(
        string='Producto Formulado',
        help='Será usado para determinar la fecha de caducidad de un producto fabricado con este componente.'
    )
