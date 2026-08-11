# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
# pyrefly: ignore [missing-import]
from odoo.exceptions import UserError

class MrpProductionSplitLine(models.TransientModel):
    _inherit = 'mrp.production.split.line'

    # CAMBIO: Añadimos lot_id para guardar el lote del componente reservado correspondiente a cada partición.
    lot_id = fields.Many2one('stock.lot', string='Lote de Componente', readonly=True)


class MrpProductionSplit(models.TransientModel):
    _inherit = 'mrp.production.split'

    warning_message = fields.Html(string="Aviso de Trazabilidad", readonly=True)

    @api.depends('num_splits')
    def _compute_details(self):
        # Si proviene de la accion de split por lotes mixtos y pasamos las cantidades customizadas, evita que el compute nativo las sobrescriba.
        if self.env.context.get('custom_split_quantities'):
            return
        super()._compute_details()

    # CAMBIO: Sobrescribimos action_split para que al finalizar el fraccionamiento de Odoo,
    # re-asocie los lotes originales exactos de los componentes en las órdenes resultantes.
    # Esto evita que Odoo divida las reservas de componentes de forma mezclada/proporcional.
    def action_split(self):
        original_mo = self.production_id
        
        # 1. Obtener todas las reservas con lote del componente único antes de dividir
        raw_move_lines = original_mo.move_raw_ids.move_line_ids.filtered(lambda ml: ml.lot_id)
        lot_original_data = {}
        for ml in raw_move_lines:
            lot_original_data[ml.lot_id.id] = {
                'product_id': ml.product_id.id,
                'quantity': ml.quantity,
                'product_uom_id': ml.product_uom_id.id,
                'location_id': ml.location_id.id,
                'location_dest_id': ml.location_dest_id.id,
                'package_id': ml.package_id.id,
                'result_package_id': ml.result_package_id.id,
                'owner_id': ml.owner_id.id,
            }

        # 2. Guardar la asignación de lotes de cada línea del wizard
        detail_lots = []
        for detail in self.production_detailed_vals_ids:
            detail_lots.append({
                'lot_id': detail.lot_id.id,
                'quantity': detail.quantity,
            })

        # Registrar las MOs antes de ejecutar la partición
        production_group = original_mo.production_group_id
        old_production_ids = set(production_group.production_ids.ids) if production_group else set(original_mo.ids)

        # 3. Invocar al método nativo de split
        res = super(MrpProductionSplit, self).action_split()

        # 4. Obtener las MOs parciales resultantes
        new_production_ids = set(production_group.production_ids.ids) if production_group else set(original_mo.ids)
        all_productions = self.env['mrp.production'].browse(list(old_production_ids | new_production_ids))
        sorted_productions = all_productions.sorted(key=lambda p: (p.backorder_sequence or 0, p.id))

        # 5. Reasignar las reservas para que cada MO parcial contenga exactamente su lote correspondiente
        for production, detail_info in zip(sorted_productions, detail_lots):
            target_lot_id = detail_info['lot_id']
            if not target_lot_id:
                continue

            comp_data = lot_original_data.get(target_lot_id)
            if not comp_data:
                continue

            product_id = comp_data['product_id']
            # Encontrar el movimiento de materia prima de este componente en la orden parcial
            raw_move = production.move_raw_ids.filtered(lambda m: m.product_id.id == product_id and not m.additional)
            if raw_move:
                # Eliminar las líneas de reserva mezcladas/proporcionales creadas por Odoo
                raw_move.move_line_ids.unlink()

                # Calcular la cantidad a reservar asegurándonos de no exceder los límites
                qty_to_reserve = min(raw_move.product_uom_qty, comp_data['quantity'])

                if qty_to_reserve > 0:
                    # Crear la línea de reserva específica para este lote y orden parcial
                    self.env['stock.move.line'].create({
                        'move_id': raw_move.id,
                        'product_id': product_id,
                        'lot_id': target_lot_id,
                        'quantity': qty_to_reserve,
                        'product_uom_id': comp_data['product_uom_id'],
                        'location_id': comp_data['location_id'],
                        'location_dest_id': comp_data['location_dest_id'],
                        'package_id': comp_data['package_id'],
                        'result_package_id': comp_data['result_package_id'],
                        'owner_id': comp_data['owner_id'],
                    })

        return res
