# from odoo import models, fields, api


# class plows_pos_connector(models.Model):
#     _name = 'plows_pos_connector.plows_pos_connector'
#     _description = 'plows_pos_connector.plows_pos_connector'

#     name = fields.Char()
#     value = fields.Integer()
#     value2 = fields.Float(compute="_value_pc", store=True)
#     description = fields.Text()
#
#     @api.depends('value')
#     def _value_pc(self):
#         for record in self:
#             record.value2 = float(record.value) / 100

