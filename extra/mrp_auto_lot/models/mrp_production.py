from odoo import models, fields, api, _
# pyrefly: ignore [missing-import]
from odoo.exceptions import UserError

class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def _prepare_stock_lot_values(self):
        self.ensure_one()
        name = self.env['ir.sequence'].next_by_code('mrp_auto_lot.global.lot')
        if not name:
            raise UserError(_("La secuencia global de lotes (mrp_auto_lot.global.lot) no está configurada."))
        return {
            'product_id': self.product_id.id,
            'name': name,
            'global_sequence_val': name,
            'production_id': self.id,
        }


    suggest_lot_split = fields.Boolean(
        string="Sugerir división de lotes",
        compute="_compute_suggest_lot_split",
        store=False
    )

    @api.depends('state')
    def _compute_suggest_lot_split(self):
        for production in self:
            production.suggest_lot_split = production.state == 'confirmed'

    def action_suggest_lot_split(self):
        self.ensure_one()
        split_data = self._get_formulated_lots_split_data()
        
        if not split_data:
            raise UserError(_("No hay sugerencia de división para esta orden de producción."))
        
        # Si aplican las reglas, se sugiere el aviso de trazabilidad y se pre-arma
        wizard = self.env['mrp.production.split'].with_context(custom_split_quantities=True).create({
            'production_id': self.id,
            'warning_message': split_data['warning_message'],
            'production_detailed_vals_ids': [
                (0, 0, {
                    'quantity': split_data['first_finished'],
                    'user_id': self.user_id.id or self.env.user.id,
                    'date': self.date_start,
                }),
                (0, 0, {
                    'quantity': split_data['remaining_qty'],
                    'user_id': self.user_id.id or self.env.user.id,
                    'date': self.date_start,
                })
            ]
        })
        
        action = self.env['ir.actions.actions']._for_xml_id('mrp.action_mrp_production_split')
        action['res_id'] = wizard.id
        action['context'] = {'custom_split_quantities': True}
        return action

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
                        'qty': 0.0,
                        'expiration_date': lot.expiration_date
                    }
                lot_data[lot]['qty'] += qty

            # Filtrar lotes que tienen fecha de caducidad
            valid_lots = [lot for lot, data in lot_data.items() if data['expiration_date']]
            if len(valid_lots) <= 1:
                continue

            # Verificar si tienen fechas de caducidad diferentes
            expiration_dates = {data['expiration_date'] for lot, data in lot_data.items() if data['expiration_date']}
            if len(expiration_dates) > 1:
                multi_lot_moves_data.append({
                    'move': move,
                    'lot_data': lot_data,
                    'expiration_dates': expiration_dates
                })

        # REGLAS DE NEGOCIO:
        # 1. Si la orden tiene más de un componente que consume múltiples lotes con caducidad diferente, entonces el warning NO va (se permite fabricar y se tomará la fecha menor entre todos).
        # 2. Si la orden tiene exactamente 1 componente (aún sin ser formulado) que consume múltiples lotes con caducidad diferente, entonces el warning SI va y se sugiere el split.
        if len(multi_lot_moves_data) == 1:
            item = multi_lot_moves_data[0]
            move = item['move']
            lot_data = item['lot_data']
            
            # Calcular el factor de proporción para calcular la cantidad de producto terminado
            total_component_qty = sum(data['qty'] for data in lot_data.values())
            ratio = 1.0
            if total_component_qty > 0:
                ratio = self.product_qty / total_component_qty

            # Ordenar por fecha de caducidad (la más próxima primero)
            sorted_lots = sorted(lot_data.items(), key=lambda item: item[1]['expiration_date'])

            first_lot, first_data = sorted_lots[0]
            first_finished = first_data['qty'] * ratio
            remaining_qty = self.product_qty - first_finished

            if first_finished > 0 and remaining_qty > 0:
                warning_html = _(
                    "<strong>Aviso de Trazabilidad:</strong> El componente <strong>%s</strong> "
                    "tiene reservado stock de múltiples lotes con diferentes fechas de caducidad. "
                    "Para evitar la mezcla de vencimientos, se ha pre-configurado la división de la orden "
                    "de manera proporcional a las cantidades de cada lote."
                ) % move.product_id.display_name

                return {
                    'first_finished': first_finished,
                    'remaining_qty': remaining_qty,
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