# -*- coding: utf-8 -*-
from odoo import models, fields

class PosOrderInherit(models.Model):
    _inherit = 'pos.order'

    x_id_pos = fields.Char(string='ID Ticket Plows POS', index=True, copy=False)
    x_closure_id = fields.Many2one('plows.pos.closure', string='Cierre de Caja POS', index=True)
    x_no_mov = fields.Char(string='Folio Movimiento POS')

    _sql_constraints = [
        ('x_id_pos_unique', 'unique(x_id_pos)', 'El ID de Ticket POS debe ser único por orden.')
    ]


class PosConfigInherit(models.Model):
    _inherit = 'pos.config'

    x_id_pos = fields.Char(string='ID Plows POS (Control/Almacén)', index=True, copy=False)
