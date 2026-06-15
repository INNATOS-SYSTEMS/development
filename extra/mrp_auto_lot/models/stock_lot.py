from odoo import models, fields, api, _
from datetime import date
from dateutil.relativedelta import relativedelta

class StockLot(models.Model):
    _inherit = 'stock.lot'

    production_id = fields.Many2one('mrp.production', string='Producción', copy=False)

    global_sequence_val = fields.Char(
        string='Secuencia Global', 
        copy=False, 
        readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('mrp_auto_lot.global.lot')
    )

    @api.depends('product_id')
    def _compute_name(self):
        for lot in self:
            if not lot.name:
                lot.name = lot.global_sequence_val or self.env['ir.sequence'].next_by_code('mrp_auto_lot.global.lot')

    @api.model_create_multi
    def create(self, vals_list):
        """
        Sobrescritura de creación para gestionar la secuencia global  
        y el cálculo de fechas de caducidad.
        """
        for vals in vals_list:
            # Tarea B: Asignar secuencia global si el nombre viene vacío
            if not vals.get('name') or vals.get('name') == _('New'):
                
                vals['name'] = self.env['ir.sequence'].next_by_code('mrp_auto_lot.global.lot') or _('New')

        lots = super(StockLot, self).create(vals_list)

        # Si el lote se creó vinculado a una MO, calcular fechas
        for lot in lots:
            if lot.production_id:
                lot._calculate_mrp_expiration_dates()
        
        return lots

    def _calculate_mrp_expiration_dates(self):
        """
        Lógica principal para determinar la caducidad basada en componentes 
        formulados o años adicionales (excepciones).
        """
        self.ensure_one()
        if not self.production_id:
            return

        # 1. Obtener componentes consumidos marcados como 'Formulados'
        # Se asume el campo is_formulated de la Tarea A.
        formulated_moves = self.production_id.move_raw_ids.filtered(
            lambda m: m.product_id.product_tmpl_id.is_formulated
        )

        # Extraer lotes de los componentes formulados
        component_lots = formulated_moves.move_line_ids.mapped('lot_id')
        # Filtrar solo aquellos que tengan fecha de caducidad definida
        valid_lots = component_lots.filtered(lambda l: l.expiration_date)

        if valid_lots:
            # Caso 1: Herencia de Lote (Un solo lote formulado consumido)
            if len(valid_lots) == 1:
                # Heredar el número de lote únicamente si es un solo elemento total en el BOM (materia prima)
                if len(self.production_id.move_raw_ids) == 1:
                    self.name = valid_lots[0].name
                self.expiration_date = valid_lots[0].expiration_date
            
            # Caso 2: Múltiples formulados (logica de Fecha Menor)
            else:
                min_date = min(valid_lots.mapped('expiration_date'))
                self.expiration_date = min_date
            
            # Autocalcular fechas auxiliares restando márgenes del producto
            self._update_date_values(self.expiration_date)

        else:
            # Caso Excepcional: Sin componentes formulados
            # Recuperar parámetro de la compañía (campo creado en la vista Settings)
            years = self.env.company.additional_expiration_years or 0
            
            all_component_lots = self.production_id.move_raw_ids.move_line_ids.mapped('lot_id')
            all_valid_lots = all_component_lots.filtered(lambda l: l.expiration_date)
            
            if all_valid_lots:
                min_date = min(all_valid_lots.mapped('expiration_date'))
                self.expiration_date = min_date + relativedelta(years=years)
            else:
                # Fecha de fabricación (actual) + años adicionales si no hay lotes con fechas
                self.expiration_date = date.today() + relativedelta(years=years)
                
            self._update_date_values(self.expiration_date)

    def _update_date_values(self, base_expiration_date):
        """
        Resta los días de margen configurados en el producto terminado.
        """
        product = self.product_id
        if base_expiration_date:
            self.use_date = base_expiration_date - relativedelta(days=product.use_time or 0)
            self.removal_date = base_expiration_date - relativedelta(days=product.removal_time or 0)
            self.alert_date = base_expiration_date - relativedelta(days=product.alert_time or 0)