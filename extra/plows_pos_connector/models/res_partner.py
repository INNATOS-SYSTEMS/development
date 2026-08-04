# -*- coding: utf-8 -*-
from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_id_pos = fields.Char(string='ID Cliente/Proveedor Plows POS', index=True, copy=False)
