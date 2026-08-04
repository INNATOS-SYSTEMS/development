# -*- coding: utf-8 -*-
from odoo import models, fields, api

class PlowsPosClosure(models.Model):
    _name = 'plows.pos.closure'
    _description = 'Corte de Caja Plows POS'
    _order = 'closing_date desc, closing_time desc'

    name = fields.Char(string='Folio del Cierre', required=True, copy=False, default='Nuevo')
    x_id_pos = fields.Integer(string='ID Cierre Plows POS', index=True, required=True, copy=False)
    session_number = fields.Integer(string='Número de Sesión')
    location_id = fields.Many2one('stock.location', string='Almacén / Ubicación stock', domain=[('x_id_pos', '!=', False)])
    closing_date = fields.Date(string='Fecha de Cierre')
    closing_time = fields.Char(string='Hora de Cierre')
    
    total_sales = fields.Float(string='Ventas Totales')
    total_refunds = fields.Float(string='Devoluciones Totales')
    total_shortage = fields.Float(string='Faltante/Sobrante')
    shortage_notes = fields.Text(string='Notas del Faltante')
    
    responsible_id = fields.Many2one('hr.employee', string='Responsable')
    
    card_total = fields.Float(string='Total Tarjeta')
    cash_total = fields.Float(string='Total Efectivo')
    transfers_total = fields.Float(string='Total Transferencia')
    checks_total = fields.Float(string='Total Cheques')
    other_total = fields.Float(string='Total Otros')
    
    notes = fields.Text(string='Notas')
    
    state = fields.Selection([
        ('draft', 'Planificado'),
        ('synced', 'Sincronizado'),
        ('failed', 'Fallo')
    ], string='Estado', default='draft', required=True)

    ticket_ids = fields.One2many('sale.order', 'x_closure_id', string='Tickets de Venta')
    movement_ids = fields.One2many('plows.pos.closure.movement', 'closure_id', string='Movimientos de Caja Chica')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('plows.pos.closure') or 'Nuevo'
        return super(PlowsPosClosure, self).create(vals_list)
