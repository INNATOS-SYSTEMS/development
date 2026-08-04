# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class PlowsPosDashboardController(http.Controller):

    @http.route('/plows_pos/dashboard/status', type='json', auth='user')
    def get_dashboard_status(self):
        """ Endpoint JSON-RPC para el componente Owl del Dashboard de Estado """
        return request.env['plows.pos.sync.dashboard'].get_dashboard_status()
