# -*- coding: utf-8 -*-
from odoo import models, fields

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    x_id_pos = fields.Char(string='ID Empleado Plows POS', index=True, copy=False)
