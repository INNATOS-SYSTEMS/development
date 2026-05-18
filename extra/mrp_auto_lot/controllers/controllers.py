# from odoo import http


# class MrpAutoLot(http.Controller):
#     @http.route('/mrp_auto_lot/mrp_auto_lot', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/mrp_auto_lot/mrp_auto_lot/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('mrp_auto_lot.listing', {
#             'root': '/mrp_auto_lot/mrp_auto_lot',
#             'objects': http.request.env['mrp_auto_lot.mrp_auto_lot'].search([]),
#         })

#     @http.route('/mrp_auto_lot/mrp_auto_lot/objects/<model("mrp_auto_lot.mrp_auto_lot"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('mrp_auto_lot.object', {
#             'object': obj
#         })

