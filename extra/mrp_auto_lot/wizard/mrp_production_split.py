# -*- coding: utf-8 -*-
from odoo import models, fields, api

class MrpProductionSplit(models.TransientModel):
    _inherit = 'mrp.production.split'

    warning_message = fields.Html(string="Aviso de Trazabilidad", readonly=True)

    @api.depends('num_splits')
    def _compute_details(self):
        # Si proviene de la accion de split por lotes mixtos y pasamos las cantidades customizadas, evita que el compute nativo las sobrescriba.
        if self.env.context.get('custom_split_quantities'):
            return
        super()._compute_details()
