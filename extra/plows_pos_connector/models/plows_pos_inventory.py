# -*- coding: utf-8 -*-
from odoo import models, fields, api


class PlowsPosInventory(models.Model):
    _name = 'plows.pos.inventory'
    _description = 'Inventario Plows POS (Fase Futura)'
    _order = 'date desc, name desc'

    name = fields.Char(
        string='Folio',
        required=True,
        copy=False,
        default='Nuevo',
    )
    date = fields.Date(string='Fecha de Cierre')
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('synced', 'Sincronizado'),
        ('failed', 'Fallo'),
    ], string='Estado', default='draft', required=True)
    notes = fields.Text(string='Notas')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('plows.pos.inventory') or 'Nuevo'
        return super().create(vals_list)
