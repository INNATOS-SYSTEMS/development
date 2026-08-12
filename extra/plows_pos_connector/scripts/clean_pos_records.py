#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de Limpieza Completa de Entidades de Punto de Venta (POS)
=============================================================================
PROPÓSITO:
  Elimina todas las órdenes de POS (pos.order), pagos (pos.payment),
  sesiones (pos.session), configuraciones de POS (pos.config) y cierres
  de caja (plows.pos.closure) creados/asociados para dejar la base de datos
  100% limpia para la nueva sincronización.
"""

def clean_all_pos_records(env):
    print("=================================================================")
    print(" INICIANDO LIMPIEZA DE ENTIDADES POS Y DEPENDENCIAS EN ODOO")
    print("=================================================================")

    # 1. Eliminar pagos de POS (pos.payment)
    payments = env['pos.payment'].search([])
    count_payments = len(payments)
    if count_payments > 0:
        payments.unlink()
    print(f"✔ Eliminados {count_payments} registros de pagos (pos.payment)")

    # 2. Eliminar líneas de orden y órdenes de POS (pos.order)
    orders = env['pos.order'].search([])
    count_orders = len(orders)
    if count_orders > 0:
        # Desvincular de cierres si existían
        orders.write({'x_closure_id': False})
        orders.unlink()
    print(f"✔ Eliminadas {count_orders} órdenes de venta POS (pos.order)")

    # 3. Eliminar / Cerrar sesiones de POS (pos.session)
    sessions = env['pos.session'].search([])
    count_sessions = len(sessions)
    for s in sessions:
        if s.state != 'closed':
            try:
                s.write({'state': 'closed'})
            except Exception:
                pass
    if count_sessions > 0:
        try:
            sessions.unlink()
            print(f"✔ Eliminadas {count_sessions} sesiones de POS (pos.session)")
        except Exception as e:
            print(f"⚠ No se pudieron eliminar algunas sesiones, se cerraron formalmente: {e}")

    # 4. Eliminar configuraciones de POS (pos.config)
    configs = env['pos.config'].search([])
    count_configs = len(configs)
    deleted_configs = 0
    for cfg in configs:
        try:
            cfg.unlink()
            deleted_configs += 1
        except Exception as e:
            print(f"  • Config '{cfg.name}' conservado por dependencias del sistema: {e}")
    print(f"✔ Eliminadas {deleted_configs} de {count_configs} configuraciones de POS (pos.config)")

    # 5. Eliminar movimientos de caja chica y cierres de caja (plows.pos.closure)
    movements = env['plows.pos.closure.movement'].search([])
    count_movs = len(movements)
    if count_movs > 0:
        movements.unlink()
    print(f"✔ Eliminados {count_movs} movimientos de caja (plows.pos.closure.movement)")

    closures = env['plows.pos.closure'].search([])
    count_closures = len(closures)
    if count_closures > 0:
        closures.unlink()
    print(f"✔ Eliminados {count_closures} cierres de caja (plows.pos.closure)")

    # 6. Limpiar historial de jobs y logs
    logs = env['plows.pos.sync.log'].search([])
    count_logs = len(logs)
    if count_logs > 0:
        logs.unlink()

    jobs = env['plows.pos.sync.job'].search([])
    count_jobs = len(jobs)
    if count_jobs > 0:
        jobs.unlink()

    print(f"✔ Limpiados {count_jobs} jobs y {count_logs} logs de sincronización")

    # Guardar cambios en la base de datos
    env.cr.commit()

    print("=================================================================")
    print(" LIMPIEZA DE POS COMPLETADA CON ÉXITO")
    print(" La base de datos está lista para ejecutar la sincronización.")
    print("=================================================================")

if __name__ == '__main__':
    if 'env' in globals():
        clean_all_pos_records(globals()['env'])
    else:
        print("Este script debe ejecutarse dentro del entorno u Odoo shell / Server Action.")
