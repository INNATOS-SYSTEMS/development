# from odoo import http


# class PlowsPosConnector(http.Controller):
#     @http.route('/plows_pos_connector/plows_pos_connector', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/plows_pos_connector/plows_pos_connector/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('plows_pos_connector.listing', {
#             'root': '/plows_pos_connector/plows_pos_connector',
#             'objects': http.request.env['plows_pos_connector.plows_pos_connector'].search([]),
#         })

#     @http.route('/plows_pos_connector/plows_pos_connector/objects/<model("plows_pos_connector.plows_pos_connector"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('plows_pos_connector.object', {
#             'object': obj
#         })

