from odoo import models, fields, api, _
# pyrefly: ignore [missing-import]
from odoo.exceptions import UserError

class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def _prepare_stock_lot_values(self):
        self.ensure_one()
        # Identificar si es Caso 1: exactamente un único lote de componente con fecha de caducidad
        raw_move_lines = self.move_raw_ids.move_line_ids.filtered(lambda ml: ml.lot_id)
        component_lots = raw_move_lines.mapped('lot_id')
        valid_component_lots = component_lots.filtered(lambda l: l.expiration_date)

        is_case_1 = len(valid_component_lots) == 1
        existing_lot = False
        if is_case_1:
            target_name = valid_component_lots[0].name
            existing_lot = self.env['stock.lot'].search([
                ('name', '=', target_name),
                ('product_id', '=', self.product_id.id),
                ('company_id', 'in', [self.company_id.id, False])
            ], limit=1)

        # Si es Caso 1 y ya existe el lote, no consumimos la secuencia global
        if is_case_1 and existing_lot:
            name = existing_lot.name
            seq_name = existing_lot.global_sequence_val
        else:
            # Consumir la secuencia global
            seq_name = self.env['ir.sequence'].next_by_code('mrp_auto_lot.global.lot')
            if not seq_name:
                raise UserError(_("La secuencia global de lotes (mrp_auto_lot.global.lot) no está configurada."))
            if is_case_1:
                name = valid_component_lots[0].name
            else:
                name = seq_name

        return {
            'product_id': self.product_id.id,
            'name': name,
            'global_sequence_val': seq_name,
            'production_id': self.id,
        }

    def action_generate_serial(self):
        self.ensure_one()
        # Identificar si es Caso 1
        raw_move_lines = self.move_raw_ids.move_line_ids.filtered(lambda ml: ml.lot_id)
        component_lots = raw_move_lines.mapped('lot_id')
        valid_component_lots = component_lots.filtered(lambda l: l.expiration_date)

        if len(valid_component_lots) == 1:
            target_name = valid_component_lots[0].name
            existing_lot = self.env['stock.lot'].search([
                ('name', '=', target_name),
                ('product_id', '=', self.product_id.id),
                ('company_id', 'in', [self.company_id.id, False])
            ], limit=1)
            if existing_lot:
                # Asignar directamente el lote existente sin generar uno nuevo
                if hasattr(self, 'lot_producing_id'):
                    self.write({'lot_producing_id': existing_lot.id})
                if hasattr(self, 'lot_producing_ids'):
                    self.write({'lot_producing_ids': [(4, existing_lot.id)]})
                return

        return super(MrpProduction, self).action_generate_serial()


    suggest_lot_split = fields.Boolean(
        string="Sugerir división de lotes",
        compute="_compute_suggest_lot_split",
        store=False
    )

    # CAMBIO: Se modifica el depend para recalcular el split sugerido cuando cambian 
    # los lotes reservados o cantidades en las líneas del componente.
    @api.depends('state', 'move_raw_ids.move_line_ids.lot_id', 'move_raw_ids.move_line_ids.quantity')
    def _compute_suggest_lot_split(self):
        for production in self:
            if production.state != 'confirmed':
                production.suggest_lot_split = False
                continue
            split_data = production._get_formulated_lots_split_data()
            production.suggest_lot_split = bool(split_data)

    # CAMBIO: Se reestructura este método para pre-configurar dinámicamente N particiones
    # proporcionales a los N lotes detectados, en lugar de una partición binaria fija de 2.
    def action_suggest_lot_split(self):
        self.ensure_one()
        split_data = self._get_formulated_lots_split_data()
        
        if not split_data:
            raise UserError(_("No hay sugerencia de división para esta orden de producción."))
        
        # Generamos una orden parcial por cada lote reservado en la proporción correspondiente
        detailed_vals = []
        for detail in split_data['split_details']:
            detailed_vals.append((0, 0, {
                'quantity': detail['quantity'],
                'lot_id': detail['lot'].id,  # CAMBIO: Asociamos el lote del componente de esta partición
                'user_id': self.user_id.id or self.env.user.id,
                'date': self.date_start,
            }))

        wizard = self.env['mrp.production.split'].with_context(custom_split_quantities=True).create({
            'production_id': self.id,
            'warning_message': split_data['warning_message'],
            'production_detailed_vals_ids': detailed_vals
        })
        
        action = self.env['ir.actions.actions']._for_xml_id('mrp.action_mrp_production_split')
        action['res_id'] = wizard.id
        action['context'] = {
            'custom_split_quantities': True,
            'split_details': [
                {
                    'lot_id': detail['lot'].id,
                    'quantity': detail['quantity'],
                }
                for detail in split_data['split_details']
            ],
        }

        return action

    # CAMBIO: Se redefinió la lógica de filtrado de este método.
    # Anteriormente se filtraban y comparaban fechas de caducidad diferentes para sugerir el split.
    # Ahora, por reglas de negocio de trazabilidad para el Caso 1, el split se sugiere de manera
    # incondicional basandose en que el componente unico consuma múltiples lotes físicos diferentes, 
    # sin importar las fechas de caducidad.
    def _get_formulated_lots_split_data(self):
        self.ensure_one()
        # Iterar todos los componentes (formulados o no)
        raw_moves = self.move_raw_ids
        
        multi_lot_moves_data = []
        for move in raw_moves:
            # Obtener los lotes consumidos o reservados en las líneas de movimiento
            move_lines = move.move_line_ids.filtered(lambda ml: ml.lot_id)
            if not move_lines:
                continue

            lot_data = {}
            for ml in move_lines:
                lot = ml.lot_id
                qty = ml.quantity or ml.reserved_uom_qty or 0.0
                if lot not in lot_data:
                    lot_data[lot] = {
                        'qty': 0.0
                    }
                lot_data[lot]['qty'] += qty

            # CAMBIO: Se evalúa puramente si hay más de un lote físico reservado en el componente,
            # sin filtrar por fechas de vencimiento ni validar si difieren.
            if len(lot_data) > 1:
                multi_lot_moves_data.append({
                    'move': move,
                    'lot_data': lot_data,
                })

        # REGLAS DE NEGOCIO ACTUALIZADAS:
        # 1. Si la orden tiene más de un componente que consume múltiples lotes, entonces el warning NO va (se permite fabricar).
        # 2. Si la orden tiene exactamente 1 componente que consume múltiples lotes, entonces el warning SI va y se sugiere el split.
        if len(multi_lot_moves_data) == 1:
            item = multi_lot_moves_data[0]
            move = item['move']
            lot_data = item['lot_data']
            
            # Calcular el factor de proporción para calcular la cantidad de producto terminado
            total_component_qty = sum(data['qty'] for data in lot_data.values())
            ratio = 1.0
            if total_component_qty > 0:
                ratio = self.product_qty / total_component_qty

            # CAMBIO: Se generan N particiones proporcionales a cada lote individual reservado,
            # garantizando un split dinámico de N órdenes parciales consistentes.
            split_details = []
            sorted_lot_data = sorted(lot_data.items(), key=lambda x: x[0].id)
            
            assigned_qty = 0.0
            for idx, (lot, data) in enumerate(sorted_lot_data):
                if idx == len(sorted_lot_data) - 1:
                    # El último lote se queda con el remanente exacto para evitar errores de redondeo de flotantes
                    lot_finished_qty = self.product_qty - assigned_qty
                else:
                    lot_finished_qty = data['qty'] * ratio
                    assigned_qty += lot_finished_qty
                
                if lot_finished_qty > 0:
                    split_details.append({
                        'lot': lot,
                        'quantity': lot_finished_qty,
                    })

            if len(split_details) > 1:
                warning_html = _(
                    "<strong>Aviso de Trazabilidad:</strong> El componente <strong>%s</strong> "
                    "tiene reservado stock de múltiples lotes. "
                    "Para evitar la mezcla de lotes, se ha pre-configurado la división de la orden "
                    "de manera proporcional a las cantidades de cada lote."
                ) % move.product_id.display_name

                return {
                    'split_details': split_details,
                    'warning_message': warning_html,
                }
        return False

    def _post_inventory(self, cancel_backorder=False):
        res = super(MrpProduction, self)._post_inventory(cancel_backorder=cancel_backorder)
        for production in self:
            if production.lot_producing_ids:
                # Asegurar que el lote terminado esté vinculado a esta MO
                production.lot_producing_ids.filtered(lambda l: not l.production_id).write({
                    'production_id': production.id
                })
                # Ejecutar el cálculo y trazabilidad final basados en los componentes reales consumidos
                for lot in production.lot_producing_ids:
                    lot._calculate_mrp_expiration_dates()
        return res