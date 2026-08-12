#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de limpieza de pos.config y re-sincronización de cierres para 2026-01-02
=============================================================================
"""

import sys
from datetime import date

def run_cleanup_and_sync(env, start_date_str='2026-01-02', end_date_str='2026-01-02'):
    print(f"=== INICIANDO LIMPIEZA DE POS.CONFIG Y RE-SINCRONIZACIÓN ({start_date_str} a {end_date_str}) ===")

    # 1. Buscar pos.config creados por Plows POS Sync (x_id_pos o nombre)
    configs = env['pos.config'].search(['|', ('x_id_pos', '=like', 'POS-CONFIG-%'), ('name', 'ilike', 'Plows POS Sync')])
    print(f"Encontradas {len(configs)} configuraciones pos.config para limpiar.")

    for cfg in configs:
        sessions = env['pos.session'].search([('config_id', '=', cfg.id)])
        orders = env['pos.order'].search([('config_id', '=', cfg.id)])
        print(f"  • Config: '{cfg.name}' (ID: {cfg.id}) -> {len(sessions)} sesiones, {len(orders)} ordenes.")
        
        # Desvincular o eliminar sesiones y ordenes asociadas si es necesario
        for s in sessions:
            try:
                if s.state != 'closed':
                    s.write({'state': 'closed'})
            except Exception as e:
                print(f"    Advertencia al cerrar sesión {s.id}: {e}")

    # 2. Eliminar o reiniciar los pos.config de prueba que no tengan ordenes bloqueantes
    for cfg in configs:
        orders_count = env['pos.order'].search_count([('config_id', '=', cfg.id)])
        if orders_count == 0:
            try:
                cfg.unlink()
                print(f"  ✔ pos.config '{cfg.name}' eliminado con éxito.")
            except Exception as e:
                print(f"  ⚠ No se pudo eliminar pos.config '{cfg.name}': {e}")
        else:
            print(f"  ℹ pos.config '{cfg.name}' conservado por contener {orders_count} órdenes.")

    env.cr.commit()

    # 3. Crear y ejecutar Job de Sincronización de Ingresos para el rango especificado
    print(f"\n=== CREANDO JOB DE SINCRONIZACIÓN DE INGRESOS PARA {start_date_str} a {end_date_str} ===")
    job = env['plows.pos.sync.job'].create({
        'name': f'Sync Cierres {start_date_str}',
        'start_date': start_date_str,
        'end_date': end_date_str,
    })

    processed, failed, log_lines = job._sync_incomes()
    env.cr.commit()

    print(f"\n=== RESULTADOS DE LA SINCRONIZACIÓN ===")
    print(f"Procesados: {processed}")
    print(f"Fallidos:   {failed}")
    print("\nLogs:")
    for line in log_lines:
        # Sanitizar etiquetas HTML para salida de consola limpia
        clean_line = line.replace('<h5>', '').replace('</h5>', '').replace('<p>', '').replace('</p>', '').replace('<b>', '').replace('</b>', '').replace('<ul>', '').replace('</ul>', '').replace('<li>', ' • ').replace('</li>', '')
        print(f"  {clean_line}")

    print("\n=== LIMPIEZA Y SINCRONIZACIÓN COMPLETADAS EXITOSAMENTE ===")

if __name__ == '__main__':
    # Si se ejecuta dentro de Odoo shell ('env' ya está en globals)
    if 'env' in globals():
        run_cleanup_and_sync(globals()['env'])
    else:
        print("Este script está diseñado para ejecutarse dentro del entorno u Odoo shell.")
