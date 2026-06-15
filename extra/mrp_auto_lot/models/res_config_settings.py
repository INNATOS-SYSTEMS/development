from odoo import models, fields, api, _

class ResCompany(models.Model):
    _inherit = 'res.company'

    additional_expiration_years = fields.Integer(
        string='Años adicionales de caducidad',
        help='Número de años adicionales que se le sumarán a la fecha de caducidad de los lotes fabricados.'
    )

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    additional_expiration_years = fields.Integer(
        string='Años adicionales de caducidad',
        related='company_id.additional_expiration_years',
        readonly=False,
        help='Número de años adicionales que se le sumarán a la fecha de caducidad de los lotes fabricados.'
    )