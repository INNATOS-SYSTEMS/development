#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  Plows POS Connector — Script de Reset de Catálogos y Sincronización
=============================================================================
PROPÓSITO:
  Limpia todos los datos sincronizados desde Plows POS y restablece las
  secuencias de sincronización a cero.

USO:
  Ejecutar desde el directorio raíz de Odoo con el shell interactivo:

    odoo-bin shell -c /ruta/a/odoo.conf -d <nombre_base_datos> \
      < /ruta/a/plows_pos_connector/scripts/reset_sync_data.py

  O desde el shell de Odoo ya abierto:
    exec(open('/ruta/al/script/reset_sync_data.py').read())

ADVERTENCIA:
  ⚠️  Este script ELIMINA datos permanentemente.
  ⚠️  Crea un respaldo de la base de datos antes de ejecutarlo.
  ⚠️  Los registros de Odoo nativos (sale.order, res.partner, etc.) que
      tengan x_id_pos asignado serán DESVINCULADOS (se borra el campo),
      NO eliminados — para preservar registros que puedan tener otro uso.
  ⚠️  Los registros PROPIOS del módulo (plows.pos.closure, sync jobs, logs)
      sí se eliminan completamente.

=============================================================================
"""

import logging
_logger = logging.getLogger('plows.reset')

# ─── Confirmación de seguridad ────────────────────────────────────────────────
CONFIRM = True   # Cambiar a False para solo SIMULAR (dry-run sin borrar nada)

# ─── Opciones de reset (activar/desactivar secciones) ────────────────────────
RESET_SYNC_JOBS     = True   # Eliminar historial de jobs y logs de sincronización
RESET_CLOSURES      = True   # Eliminar cortes de caja, movimientos y desvincular tickets POS
RESET_CATALOGS      = True   # Limpiar x_id_pos de productos, contactos, almacenes, empleados
RESET_TAX_RULES     = True   # Eliminar reglas de impuestos POS (plows.pos.tax.rule)
RESET_PAYMENT_RULES = False  # Eliminar reglas de pago POS (precaución: afecta mapeo de diarios)
RESET_SEQUENCES     = True   # Restablecer secuencias de folio a 1

# ─── Output helpers ───────────────────────────────────────────────────────────
BOLD   = "\033[1m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RST    = "\033[0m"

def h(title):
    print(f"\n{BOLD}{CYAN}{'─' * 62}{RST}")
    print(f"{BOLD}{CYAN}  {title}{RST}")
    print(f"{BOLD}{CYAN}{'─' * 62}{RST}")

def ok(msg):   print(f"  {GREEN}✔{RST}  {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RST}  {msg}")
def info(msg): print(f"  {CYAN}•{RST}  {msg}")
def dry(msg):  print(f"  {YELLOW}[DRY-RUN]{RST} {msg}")

# =============================================================================
print(f"\n{BOLD}{RED}{'=' * 62}")
print(f"  PLOWS POS — RESET DE DATOS DE SINCRONIZACIÓN")
print(f"{'=' * 62}{RST}")

if not CONFIRM:
    warn("Modo DRY-RUN activo — no se modificará ningún dato. Solo conteos.")
print()

# ─── 1. Historial de Jobs y Logs ──────────────────────────────────────────────
if RESET_SYNC_JOBS:
    h("1/5 — Historial de Sincronización y Logs")

    logs = env['plows.pos.sync.log'].search([])
    jobs = env['plows.pos.sync.job'].search([])

    info(f"Logs encontrados:  {len(logs)}")
    info(f"Jobs encontrados:  {len(jobs)}")

    if CONFIRM:
        logs.unlink()
        ok(f"Eliminados {len(logs)} registros de log  (plows.pos.sync.log)")
        jobs.unlink()
        ok(f"Eliminados {len(jobs)} jobs de sincronización (plows.pos.sync.job)")
        env.cr.commit()
    else:
        dry("Se eliminarían todos los logs y jobs de sincronización")

# ─── 2. Cortes de Caja, Movimientos y Tickets ────────────────────────────────
if RESET_CLOSURES:
    h("2/5 — Cortes de Caja, Movimientos y Tickets de Venta")

    movements = env['plows.pos.closure.movement'].search([])
    tickets   = env['sale.order'].search([('x_id_pos', '!=', False)])
    closures  = env['plows.pos.closure'].search([])

    info(f"Movimientos de caja chica:  {len(movements)}")
    info(f"Tickets de venta (POS):     {len(tickets)}")
    info(f"Cortes de caja:             {len(closures)}")
    warn("Los sale.order NO se eliminan — solo se limpia x_id_pos y x_closure_id")

    if CONFIRM:
        movements.unlink()
        ok(f"Eliminados {len(movements)} movimientos  (plows.pos.closure.movement)")

        ticket_count = len(tickets)
        tickets.write({'x_id_pos': False, 'x_closure_id': False})
        ok(f"Desvinculados {ticket_count} tickets de venta (x_id_pos + x_closure_id = False)")

        closures.unlink()
        ok(f"Eliminados {len(closures)} cortes de caja  (plows.pos.closure)")
        env.cr.commit()
    else:
        dry("Se eliminarían movimientos y closures; tickets solo desvinculados")

# ─── 3. Catálogos Maestros ────────────────────────────────────────────────────
if RESET_CATALOGS:
    h("3/5 — Catálogos Maestros (limpiar x_id_pos)")

    products  = env['product.product'].search([('x_id_pos', '!=', False)])
    partners  = env['res.partner'].search([('x_id_pos', '!=', False)])
    locations = env['stock.location'].search([('x_id_pos', '!=', False)])
    employees = env['hr.employee'].search([('x_id_pos', '!=', False)])

    info(f"Productos con x_id_pos:   {len(products)}")
    info(f"Contactos con x_id_pos:   {len(partners)}")
    info(f"Almacenes con x_id_pos:   {len(locations)}")
    info(f"Empleados con x_id_pos:   {len(employees)}")
    warn("Los registros nativos NO se eliminan — solo se limpia el campo x_id_pos")

    if CONFIRM:
        products.write({'x_id_pos': False, 'x_sync_status': False, 'x_last_sync_date': False})
        ok(f"Limpiado x_id_pos en {len(products)} productos  (product.product)")

        partners.write({'x_id_pos': False})
        ok(f"Limpiado x_id_pos en {len(partners)} contactos  (res.partner)")

        locations.write({'x_id_pos': False, 'x_warehouse_code': False})
        ok(f"Limpiado x_id_pos en {len(locations)} ubicaciones  (stock.location)")

        employees.write({'x_id_pos': False})
        ok(f"Limpiado x_id_pos en {len(employees)} empleados  (hr.employee)")

        env.cr.commit()
    else:
        dry("Se limpiaría x_id_pos en productos, contactos, almacenes y empleados")

# ─── 4. Reglas de Mapeo ───────────────────────────────────────────────────────
if RESET_TAX_RULES:
    h("4/5 — Reglas de Impuestos POS")

    tax_rules = env['plows.pos.tax.rule'].search([])
    info(f"Reglas de impuestos encontradas: {len(tax_rules)}")

    if CONFIRM:
        tax_rules.unlink()
        ok(f"Eliminadas {len(tax_rules)} reglas de impuesto  (plows.pos.tax.rule)")
        env.cr.commit()
    else:
        dry("Se eliminarían las reglas de impuesto POS")

if RESET_PAYMENT_RULES:
    h("4b — Reglas de Métodos de Pago POS")

    payment_rules = env['plows.pos.payment.rule'].search([])
    info(f"Reglas de pago encontradas: {len(payment_rules)}")

    if CONFIRM:
        payment_rules.unlink()
        ok(f"Eliminadas {len(payment_rules)} reglas de pago  (plows.pos.payment.rule)")
        env.cr.commit()
    else:
        dry("Se eliminarían las reglas de pago POS")
else:
    info("Reglas de pago: OMITIDAS (RESET_PAYMENT_RULES = False) — edita el script para activarlas")

# ─── 5. Secuencias ────────────────────────────────────────────────────────────
if RESET_SEQUENCES:
    h("5/5 — Restablecimiento de Secuencias a 1")

    SEQUENCE_CODES = [
        ('plows.pos.sync.job',  'Jobs de Sincronización'),
        ('plows.pos.closure',   'Cortes de Caja'),
        ('plows.pos.expense',   'Egresos'),
        ('plows.pos.inventory', 'Inventario'),
    ]

    for code, label in SEQUENCE_CODES:
        seq = env['ir.sequence'].search([('code', '=', code)], limit=1)
        if seq:
            info(f"'{label}' ({code}): número actual → {seq.number_next_actual}")
            if CONFIRM:
                seq.write({'number_next_actual': 1})
                ok(f"Secuencia '{label}' restablecida → próximo folio: {seq.prefix}0001")
            else:
                dry(f"Se restablecería la secuencia '{label}' a 1")
        else:
            warn(f"Secuencia '{code}' no encontrada en la BD — omitida")

    if CONFIRM:
        env.cr.commit()

# ─── Resumen Final ────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'=' * 62}")
if CONFIRM:
    print(f"{GREEN}  ✔  RESET COMPLETADO EXITOSAMENTE{RST}")
else:
    print(f"{YELLOW}  ℹ  DRY-RUN COMPLETADO — ningún dato fue modificado{RST}")
print(f"{BOLD}{'=' * 62}{RST}\n")

if CONFIRM:
    print("  Próximos pasos sugeridos:")
    print("  1. Verifica el Tablero:  Plows POS → Tablero")
    print("  2. Inicia sincronización: Monitoreo → Historial de Sincronización → Nuevo → Iniciar Sincronización")
    print("  3. Confirma que los catálogos se repueblan correctamente\n")
