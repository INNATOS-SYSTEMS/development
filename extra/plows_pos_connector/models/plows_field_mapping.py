# -*- coding: utf-8 -*-
from odoo import models, fields, api


class PlowsPosFieldMapping(models.Model):
    _name = 'plows.pos.field.mapping'
    _description = 'Plows POS Field Mapping Configuration'
    _order = 'pos_catalog asc, sequence asc, id asc'

    name = fields.Char(string='Nombre', compute='_compute_name', store=True)
    sequence = fields.Integer(string='Secuencia', default=10)
    pos_catalog = fields.Selection([
        ('products', 'Productos (product.template)'),
        ('product_variants', 'Variantes / Exhibiciones (product.product)'),
        ('customers', 'Clientes (res.partner)'),
        ('suppliers', 'Proveedores (res.partner)'),
        ('locations', 'Almacenes / Sucursales (stock.location)'),
        ('employees', 'Personal (hr.employee)'),
        ('taxes', 'Impuestos (plows.pos.tax.rule)'),
        ('payment_methods', 'Métodos de pago (pos.payment.method)'),
    ], string='Catálogo POS', required=True, index=True)

    json_key = fields.Char(string='Propiedad JSON (POS API)', required=True, help='Nombre de la clave en el payload JSON devuelto por Plows POS')
    json_key_desc = fields.Char(string='Descripción de la Propiedad')

    odoo_model_id = fields.Many2one(
        'ir.model', string='Modelo Destino Odoo', required=True, ondelete='cascade'
    )
    odoo_model_name = fields.Char(related='odoo_model_id.model', string='Técnico Modelo Odoo', readonly=True)

    odoo_field_id = fields.Many2one(
        'ir.model.fields', string='Campo Destino Odoo', required=True, ondelete='cascade',
        domain="[('model_id', '=', odoo_model_id)]"
    )
    odoo_field_name = fields.Char(related='odoo_field_id.name', string='Nombre Técnico Campo', readonly=True)

    is_active = fields.Boolean(string='Activo', default=True, help='Si está deshabilitado, esta propiedad se omitirá durante la sincronización')
    allow_create = fields.Boolean(string='Permitir Creación', default=True, help='Si se desmarca, el motor de sincronización omitirá la creación de nuevos registros en Odoo para este catálogo si no existen previamente por ID POS.')
    is_required = fields.Boolean(string='Requerido', default=False)
    default_fallback = fields.Char(string='Valor por Defecto (Fallback)')

    @api.depends('pos_catalog', 'json_key', 'odoo_field_id')
    def _compute_name(self):
        for rec in self:
            cat_label = dict(self._fields['pos_catalog'].selection).get(rec.pos_catalog, rec.pos_catalog or '')
            field_name = rec.odoo_field_id.field_description or rec.odoo_field_name or ''
            rec.name = f"[{cat_label}] {rec.json_key or ''} ➔ {field_name}"

    @api.onchange('pos_catalog')
    def _onchange_pos_catalog(self):
        catalog_model_map = {
            'products': 'product.template',
            'product_variants': 'product.product',
            'customers': 'res.partner',
            'suppliers': 'res.partner',
            'locations': 'stock.location',
            'employees': 'hr.employee',
            'taxes': 'plows.pos.tax.rule',
            'payment_methods': 'pos.payment.method',
        }
        target_model = catalog_model_map.get(self.pos_catalog)
        if target_model:
            model_obj = self.env['ir.model'].search([('model', '=', target_model)], limit=1)
            if model_obj:
                self.odoo_model_id = model_obj.id

    def init(self):
        super().init()
        self._ensure_default_mappings()

    @api.model
    def _ensure_default_mappings(self):
        """ Auto-inicializa los mapeos por defecto solo si la tabla está vacía. """
        if self._context.get('skip_ensure'):
            return
        self.env.cr.execute("SELECT 1 FROM plows_pos_field_mapping LIMIT 1")
        if self.env.cr.fetchone():
            return
        self.with_context(skip_ensure=True)._load_default_matrix()

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)

    def search_fetch(self, domain, field_names, offset=0, limit=None, order=None):
        if not self._context.get('skip_ensure'):
            self._ensure_default_mappings()
        return super().search_fetch(domain, field_names, offset=offset, limit=limit, order=order)

    @api.model
    def _load_default_matrix(self):
        """ Carga internamente la matriz de mapeos por defecto. """
        default_matrix = [
            # Catálogo: Productos (Plantilla Maestro) -> product.template
            ('products', 'product.template', 'pos_product_id', 'x_id_pos', 'ID Plows POS (Template)', True),
            ('products', 'product.template', 'pos_product_name', 'name', 'Nombre del Producto', True),
            ('products', 'product.template', 'pos_product_sku', 'default_code', 'Referencia Interna SKU', True),
            ('products', 'product.template', 'barcode', 'barcode', 'Código de Barras', True),
            ('products', 'product.template', 'description', 'description_sale', 'Descripción de Ventas', True),

            # Catálogo: Variantes / Exhibiciones -> product.product
            ('product_variants', 'product.product', 'prod_exhibicion_id', 'x_id_pos', 'ID Plows POS (Exhibición)', True),
            ('product_variants', 'product.product', 'plows_key', 'default_code', 'SKU / Clave Exhibición', True),
            
            # Catálogo: Clientes -> res.partner
            ('customers', 'res.partner', 'pos_customer_id', 'x_id_pos', 'ID Cliente POS', True),
            ('customers', 'res.partner', 'pos_customer_name', 'name', 'Nombre del Cliente', True),
            ('customers', 'res.partner', 'rfc', 'vat', 'RFC / Identificación Fiscal', True),
            ('customers', 'res.partner', 'customer_phone', 'phone', 'Teléfono de Contacto', False),

            # Catálogo: Proveedores -> res.partner
            ('suppliers', 'res.partner', 'pos_supplier_id', 'x_id_pos', 'ID Proveedor POS', True),
            ('suppliers', 'res.partner', 'pos_supplier_name', 'name', 'Nombre Comercial', True),
            ('suppliers', 'res.partner', 'vat', 'vat', 'RFC / Identificación Fiscal', True),
            ('suppliers', 'res.partner', 'phone', 'phone', 'Teléfono', True),

            # Catálogo: Ubicaciones -> stock.location
            ('locations', 'stock.location', 'pos_warehouse_id', 'x_id_pos', 'ID Sucursal POS', True),
            ('locations', 'stock.location', 'pos_warehouse_name', 'name', 'Nombre de la Ubicación', True),

            # Catálogo: Empleados -> hr.employee
            ('employees', 'hr.employee', 'pos_employee_id', 'x_id_pos', 'ID Empleado POS', True),
            ('employees', 'hr.employee', 'pos_employee_name', 'name', 'Nombre del Empleado', True),

            # Catálogo: Métodos de Pago -> pos.payment.method
            ('payment_methods', 'pos.payment.method', 'pos_payment_method_id', 'x_id_pos', 'ID Método de Pago POS', True),
            ('payment_methods', 'pos.payment.method', 'pos_payment_method', 'name', 'Nombre Método de Pago', True),
            ('payment_methods', 'pos.payment.method', 'status', 'active', 'Estado Activo', True),
        ]

        created_count = 0
        for cat, model_name, json_k, odoo_f, desc, is_req in default_matrix:
            model_obj = self.env['ir.model'].search([('model', '=', model_name)], limit=1)
            if not model_obj:
                continue
            field_obj = self.env['ir.model.fields'].search([
                ('model_id', '=', model_obj.id),
                ('name', '=', odoo_f)
            ], limit=1)
            if not field_obj:
                continue

            existing = self.search([
                ('pos_catalog', '=', cat),
                ('json_key', '=', json_k),
                ('odoo_model_id', '=', model_obj.id)
            ], limit=1)

            if not existing:
                self.create({
                    'pos_catalog': cat,
                    'json_key': json_k,
                    'json_key_desc': desc,
                    'odoo_model_id': model_obj.id,
                    'odoo_field_id': field_obj.id,
                    'is_active': True,
                    'is_required': is_req,
                })
                created_count += 1
        return created_count

    @api.model
    def action_load_default_mappings(self):
        """ Carga la matriz de mapeos predeterminados por defecto para todos los catálogos de Plows POS. """
        created_count = self._load_default_matrix()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Carga de Mapeos Completa',
                'message': f'Se generaron {created_count} mapeos de campos por defecto exitosamente.',
                'type': 'success',
                'sticky': False,
            }
        }
