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
        Sobrescritura de creación para gestionar la secuencia global,
        la herencia de lote en Caso 1 y evitar excepciones de unicidad.
        """
        lots = self.env['stock.lot']
        vals_to_create = []

        for vals in vals_list:
            product_id = vals.get('product_id')
            company_id = vals.get('company_id') or self.env.company.id
            name = vals.get('name')
            production_id = vals.get('production_id')

            # Recuperar el production_id del contexto o fallback a MO activa para el producto
            if not production_id:
                context = self.env.context
                if context.get('default_production_id'):
                    production_id = context.get('default_production_id')
                elif context.get('active_model') == 'mrp.production' and context.get('active_id'):
                    production_id = context.get('active_id')
                elif context.get('production_id'):
                    production_id = context.get('production_id')
                elif product_id:
                    # Fallback: buscar la MO activa (confirmada) más reciente de este producto
                    domain = [
                        ('product_id', '=', product_id),
                        ('state', '=', 'confirmed')
                    ]
                    active_mo = self.env['mrp.production'].search(domain, order='write_date desc', limit=1)
                    if active_mo:
                        production_id = active_mo.id

            # CASO 1: Identificar si aplica el Caso 1 para este lote en creación
            is_case_1 = False
            if production_id:
                production = self.env['mrp.production'].browse(production_id)
                raw_move_lines = production.move_raw_ids.move_line_ids.filtered(lambda ml: ml.lot_id)
                valid_component_lots = raw_move_lines.mapped('lot_id').filtered(lambda l: l.expiration_date)
                if len(valid_component_lots) == 1:
                    is_case_1 = True
                    # Si el nombre viene vacío o es 'New', lo heredamos del componente
                    if not name or name == _('New'):
                        name = valid_component_lots[0].name
                        vals['name'] = name
                    if not vals.get('production_id'):
                        vals['production_id'] = production_id

            # EVITAR EXCEPCIÓN DE DUPLICADO: Buscar si ya existe el lote para este producto en la compañía
            if name and product_id:
                existing_lot = self.search([
                    ('name', '=', name),
                    ('product_id', '=', product_id),
                    ('company_id', 'in', [company_id, False])
                ], limit=1)
                if existing_lot:
                    # Si ya existe, lo reutilizamos actualizando su relación con la MO actual
                    vals_to_write = {k: v for k, v in vals.items() if k not in ['name', 'product_id', 'company_id']}
                    if vals_to_write:
                        existing_lot.write(vals_to_write)
                    lots += existing_lot
                    continue

            # Si llegamos aquí, el lote NO existe en el sistema. Es la primera creación.
            if is_case_1:
                # Caso 1 por primera vez: consumimos la secuencia global para que la afecte (si no ha sido consumida)
                if not vals.get('global_sequence_val'):
                    seq_name = self.env['ir.sequence'].next_by_code('mrp_auto_lot.global.lot') or _('New')
                    vals['global_sequence_val'] = seq_name
            elif not name or name == _('New'):
                # Caso normal con nombre vacío: consumir secuencia global
                if not vals.get('global_sequence_val'):
                    seq_name = self.env['ir.sequence'].next_by_code('mrp_auto_lot.global.lot') or _('New')
                    vals['name'] = seq_name
                    vals['global_sequence_val'] = seq_name

            vals_to_create.append(vals)

        # Crear los lotes nuevos que no existían previamente
        if vals_to_create:
            created_lots = super(StockLot, self).create(vals_to_create)
            lots += created_lots

        # Si el lote se creó/reutilizó vinculado a una MO, calcular fechas
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

        # 1. Obtener todos los movimientos y líneas con lotes y caducidades asignadas
        all_raw_lines = self.production_id.move_raw_ids.move_line_ids.filtered(
            lambda ml: ml.lot_id and ml.lot_id.expiration_date
        )
        unique_component_lots = all_raw_lines.mapped('lot_id')

        if len(unique_component_lots) == 1:
            # CASO 1: Un solo componente de la BoM tiene lote/caducidad
            comp_lot = unique_component_lots[0]
            target_name = comp_lot.name
            
            # Recopilar todas las fechas del lote original del componente
            dates_vals = {
                'expiration_date': comp_lot.expiration_date,
                'use_date': comp_lot.use_date,
                'removal_date': comp_lot.removal_date,
                'alert_date': comp_lot.alert_date,
            }
            
            if self.name != target_name:
                # EVITAR EXCEPCIÓN DE DUPLICADO EN ESCRITURA:
                # Si el lote destino ya existe para este producto, vinculamos el existente y borramos el lote temporal.
                existing_lot = self.search([
                    ('name', '=', target_name),
                    ('product_id', '=', self.product_id.id),
                    ('company_id', 'in', [self.company_id.id, False])
                ], limit=1)
                
                if existing_lot:
                    production = self.production_id
                    
                    # 1. Reemplazar referencias en líneas de movimiento de producto terminado de la MO
                    finished_move_lines = production.move_finished_ids.move_line_ids.filtered(lambda ml: ml.lot_id == self)
                    if finished_move_lines:
                        finished_move_lines.write({'lot_id': existing_lot.id})
                        
                    # 2. Reemplazar referencias en la MO
                    if hasattr(production, 'lot_producing_id') and production.lot_producing_id == self:
                        production.write({'lot_producing_id': existing_lot.id})
                    if hasattr(production, 'lot_producing_ids') and self in production.lot_producing_ids:
                        production.write({'lot_producing_ids': [(3, self.id), (4, existing_lot.id)]})
                    
                    # 3. Copiar las fechas al lote existente
                    existing_lot.write(dates_vals)
                    
                    # 4. Eliminar este lote temporal para no dejar basura en la base de datos
                    self.unlink()
                    return
                else:
                    self.name = target_name
            
            # Escribir las fechas directamente heredadas del componente en el lote actual
            self.write(dates_vals)
        
        elif len(unique_component_lots) > 1:
            # Evaluar si hay componentes formulados
            formulated_lines = all_raw_lines.filtered(
                lambda ml: ml.product_id.product_tmpl_id.is_formulated
            )
            formulated_lots = formulated_lines.mapped('lot_id')
            
            if formulated_lots:
                # CASO 2: Múltiples lotes + Al menos uno formulado
                self.expiration_date = min(formulated_lots.mapped('expiration_date'))
            else:
                # CASO EXCEPCIONAL: Múltiples componentes con lote + Ninguno formulado
                years = self.env.company.additional_expiration_years or 0
                min_date = min(unique_component_lots.mapped('expiration_date'))
                self.expiration_date = min_date + relativedelta(years=years)
            
            self._update_date_values(self.expiration_date)
        else:
            # Sin lotes con caducidad en la BoM
            years = self.env.company.additional_expiration_years or 0
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