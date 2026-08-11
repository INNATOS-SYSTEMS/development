# -*- coding: utf-8 -*-
from odoo import models, fields, api
from datetime import datetime

class PlowsPosClosure(models.Model):
    _name = 'plows.pos.closure'
    _description = 'Corte de Caja Plows POS'
    _order = 'closing_date desc, closing_time desc, id desc'

    name = fields.Char(string='Folio del Cierre', required=True, copy=False, default='Nuevo')
    x_id_pos = fields.Char(string='ID Cierre Plows POS', index=True, required=True, copy=False)
    session_number = fields.Char(string='Número de Sesión')
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

    ticket_ids = fields.One2many('pos.order', 'x_closure_id', string='Tickets de Venta')
    movement_ids = fields.One2many('plows.pos.closure.movement', 'closure_id', string='Movimientos de Caja Chica')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') in ['Nuevo', False, '']:
                closing_date = vals.get('closing_date')
                date_str = ''
                if closing_date:
                    if isinstance(closing_date, str):
                        date_str = closing_date.replace('-', '')
                    else:
                        date_str = closing_date.strftime('%Y%m%d')
                else:
                    date_str = fields.Date.today().strftime('%Y%m%d')

                # Calcular secuencia por fecha de cierre
                existing_count = self.search_count([('closing_date', '=', closing_date)]) if closing_date else 0
                seq = str(existing_count + 1).zfill(4)
                vals['name'] = f"CORTE/{date_str}/{seq}"
        return super(PlowsPosClosure, self).create(vals_list)
