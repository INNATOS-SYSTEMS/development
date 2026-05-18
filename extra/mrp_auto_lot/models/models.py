from odoo import models, fields, api, _
# pyrefly: ignore [missing-import]
from odoo.exceptions import UserError

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_formulated = fields.Boolean(
        string='Producto Formulado',
        help='Será usado para determinar la fecha de caducidad de un producto fabricado con este componente.'
    )

class StockLot(models.Model):
    _inherit = 'stock.lot'

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
        }

