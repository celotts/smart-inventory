/**
 * Tools disponibles para los agentes.
 * Cada tool mapea a una función de api.js o lógica local.
 * El agente decide QUE tool usar basandose en la query del usuario.
 */

import { Tool } from './base.js';
import { api } from '../api.js';

// ============================================================================
// TOOLS DE INVENTARIO (InventoryAgent)
// ============================================================================

export const ToolBuscarDispositivo = new Tool({
  nombre: 'buscar_dispositivo',
  descripcion: 'Busca un dispositivo por código (QR, serial, parcial). Devuelve info completa + stock.',
  parametros: {
    type: 'object',
    properties: {
      codigo: { type: 'string', description: 'Código QR, serial o parcial (ej: INV-a1b2, SN-DEMO-001)' },
    },
    required: ['codigo'],
  },
  ejecutar: async ({ codigo }) => api.resolverCodigo(codigo),
});

export const ToolListarDispositivos = new Tool({
  nombre: 'listar_dispositivos',
  descripcion: 'Lista dispositivos con filtros. Para consultas de conteo, stock, ubicación.',
  parametros: {
    type: 'object',
    properties: {
      limite: { type: 'integer', minimum: 1, maximum: 200, default: 50 },
      offset: { type: 'integer', minimum: 0, default: 0 },
      estado: { type: 'string', enum: ['IN_WAREHOUSE', 'IN_TRANSIT', 'INSTALLED', 'IN_REPAIR', 'PERMANENTLY_DISPOSED'] },
      ubicacion_id: { type: 'integer', minimum: 1 },
      modelo_id: { type: 'integer', minimum: 1 },
    },
  },
  ejecutar: async ({ limite = 50, offset = 0, estado, ubicacion_id, modelo_id }) =>
    api.listarDispositivos({ limite, offset, estado, ubicacion_id, modelo_id }),
});

export const ToolStockPorModelo = new Tool({
  nombre: 'stock_por_modelo',
  descripcion: 'Stock agregado por modelo/ubicación. Para alertas de stock bajo, reposición, dashboard.',
  parametros: {
    type: 'object',
    properties: {
      solo_bajo: { type: 'boolean', default: false, description: 'Solo modelos con stock <= min_stock' },
    },
  },
  ejecutar: async ({ solo_bajo = false }) => {
    const resp = await api.pedir('/stock/summary', { params: { solo_bajo: String(solo_bajo) } });
    return resp;
  },
});

export const ToolStockBajo = new Tool({
  nombre: 'stock_bajo',
  descripcion: 'Modelos con stock por debajo del mínimo. Para generar órdenes de compra.',
  parametros: { type: 'object', properties: {} },
  ejecutar: async () => api.pedir('/stock/low'),
});

export const ToolHistorialDispositivo = new Tool({
  nombre: 'historial_dispositivo',
  descripcion: 'Historial completo de movimientos de un dispositivo (instalaciones, reparaciones, traslados).',
  parametros: {
    type: 'object',
    properties: {
      device_id: { type: 'integer', minimum: 1 },
      limite: { type: 'integer', minimum: 1, maximum: 100, default: 50 },
    },
    required: ['device_id'],
  },
  ejecutar: async ({ device_id, limite = 50 }) => api.historial(device_id, { limite }),
});

export const ToolEscanear = new Tool({
  nombre: 'escanear',
  descripcion: 'Registra un escaneo (movimiento). Requiere código, tipo_movimiento, client_scan_id.',
  parametros: {
    type: 'object',
    properties: {
      codigo: { type: 'string' },
      tipo_movimiento: { type: 'string', enum: ['WAREHOUSE_ENTRY', 'OUT_TO_CLIENT', 'INSTALLATION', 'REPLACEMENT', 'WITHDRAWAL_TO_WORKSHOP', 'WAREHOUSE_REENTRY', 'DISPOSAL'] },
      client_scan_id: { type: 'string', format: 'uuid' },
      ubicacion_id: { type: 'integer', minimum: 1 },
      latitude: { type: 'number', minimum: -90, maximum: 90 },
      longitude: { type: 'number', minimum: -180, maximum: 180 },
      replaced_device_id: { type: 'integer', minimum: 1 },
      replacement_reason: { type: 'string', maxLength: 500 },
      observations: { type: 'string', maxLength: 1000 },
    },
    required: ['codigo', 'tipo_movimiento', 'client_scan_id'],
  },
  ejecutar: async (datos) => api.escanear(datos),
});

// ============================================================================
// TOOLS DE MANTENIMIENTO (MaintenanceAgent)
// ============================================================================

export const ToolMantenimientosPendientes = new Tool({
  nombre: 'mantenimientos_pendientes',
  descripcion: 'Lista mantenimientos PENDING, DUE_SOON, OVERDUE. Para dashboard de técnico.',
  parametros: {
    type: 'object',
    properties: {
      estado: { type: 'string', enum: ['PENDING', 'DUE_SOON', 'OVERDUE', 'ALL'], default: 'ALL' },
      tecnico_id: { type: 'string', format: 'uuid' },
      limite: { type: 'integer', minimum: 1, maximum: 200, default: 50 },
    },
  },
  ejecutar: async ({ estado = 'ALL', tecnico_id, limite = 50 }) =>
    api.pedir('/maintenance/pending', { params: { estado, tecnico_id, limite } }),
});

export const ToolPlanesMantenimiento = new Tool({
  nombre: 'planes_mantenimiento',
  descripcion: 'Planes de mantenimiento por modelo (intervalos, checklists).',
  parametros: {
    type: 'object',
    properties: {
      model_id: { type: 'integer', minimum: 1 },
    },
  },
  ejecutar: async ({ model_id }) => {
    const params = model_id ? { model_id } : {};
    return api.pedir('/maintenance/plans', { params });
  },
});

export const ToolCompletarMantenimiento = new Tool({
  nombre: 'completar_mantenimiento',
  descripcion: 'Marca mantenimiento como COMPLETED con resultados, checklist, costos.',
  parametros: {
    type: 'object',
    properties: {
      maintenance_id: { type: 'string', format: 'uuid' },
      result: { type: 'string', enum: ['OK', 'NEEDS_REPAIR', 'NEEDS_REPLACEMENT', 'OBSERVATION'] },
      observations: { type: 'string', maxLength: 1000 },
      checklist_results: { type: 'array', items: { type: 'object' } },
      labor_cost: { type: 'number', minimum: 0 },
      parts_cost: { type: 'number', minimum: 0 },
      next_scheduled_for: { type: 'string', format: 'date' },
    },
    required: ['maintenance_id', 'result'],
  },
  ejecutar: async (datos) => api.pedir(`/maintenance/${datos.maintenance_id}/complete`, { metodo: 'POST', cuerpo: datos }),
});

export const ToolGenerarMantenimientos = new Tool({
  nombre: 'generar_mantenimientos',
  descripcion: 'Ejecuta batch nocturno: crea mantenimientos pendientes según planes + installation_date.',
  parametros: { type: 'object', properties: {} },
  ejecutar: async () => api.pedir('/maintenance/generate', { metodo: 'POST' }),
});

// ============================================================================
// TOOLS DE COMPRAS (PurchasingAgent)
// ============================================================================

export const ToolCrearOrdenCompra = new Tool({
  nombre: 'crear_orden_compra',
  descripcion: 'Crea orden de compra con líneas (modelo, cantidad, costo). Estado: BORRADOR.',
  parametros: {
    type: 'object',
    properties: {
      vendor_id: { type: 'integer', minimum: 1 },
      lineas: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            model_id: { type: 'integer', minimum: 1 },
            cantidad: { type: 'integer', minimum: 1 },
            unit_cost: { type: 'number', minimum: 0 },
          },
          required: ['model_id', 'cantidad'],
        },
        minItems: 1,
      },
      expected_date: { type: 'string', format: 'date' },
      observations: { type: 'string', maxLength: 1000 },
    },
    required: ['vendor_id', 'lineas'],
  },
  ejecutar: async (datos) => api.pedir('/purchase-orders', { metodo: 'POST', cuerpo: datos }),
});

export const ToolListarOrdenesCompra = new Tool({
  nombre: 'listar_ordenes_compra',
  descripcion: 'Lista órdenes de compra con filtros (estado, proveedor, fechas).',
  parametros: {
    type: 'object',
    properties: {
      estado: { type: 'string', enum: ['BORRADOR', 'ENVIADA', 'CONFIRMADA', 'RECIBIDA', 'CANCELADA'] },
      vendor_id: { type: 'integer', minimum: 1 },
      desde: { type: 'string', format: 'date' },
      hasta: { type: 'string', format: 'date' },
    },
  },
  ejecutar: async (params) => api.pedir('/purchase-orders', { params }),
});

export const ToolRecibirOrdenCompra = new Tool({
  nombre: 'recibir_orden_compra',
  descripcion: 'Registra recepcion (parcial o total) de una orden. Crea devices + WAREHOUSE_ENTRY.',
  parametros: {
    type: 'object',
    properties: {
      po_id: { type: 'integer', minimum: 1 },
      lineas: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            po_line_id: { type: 'integer', minimum: 1 },
            cantidad_recibida: { type: 'integer', minimum: 1 },
            serial_numbers: { type: 'array', items: { type: 'string' } }, // Opcional: serials especificos
          },
          required: ['po_line_id', 'cantidad_recibida'],
        },
        minItems: 1,
      },
      observations: { type: 'string', maxLength: 1000 },
    },
    required: ['po_id', 'lineas'],
  },
  ejecutar: async (datos) => api.pedir(`/purchase-orders/${datos.po_id}/receive`, { metodo: 'POST', cuerpo: datos }),
});

export const ToolSugerirReorden = new Tool({
  nombre: 'sugerir_reorden',
  descripcion: 'Calcula sugerencia de compra basada en stock_levels: max_stock - available, redondeado a reorder_multiple.',
  parametros: { type: 'object', properties: {} },
  ejecutar: async () => api.pedir('/purchase-orders/suggest', { metodo: 'POST' }),
});

// ============================================================================
// TOOLS DE REPORTES (ReportingAgent)
// ============================================================================

export const ToolReporteOperativo = new Tool({
  nombre: 'reporte_operativo',
  descripcion: 'Genera reporte ejecutivo: stock, movimientos, mantenimientos, alertas. LLM redacta en prosa.',
  parametros: { type: 'object', properties: {} },
  ejecutar: async () => api.reporte(),
});

export const ToolEstadisticasInventario = new Tool({
  nombre: 'estadisticas_inventario',
  descripcion: 'KPIs numericos: total dispositivos, por estado, por ubicación, valor inventario, rotacion.',
  parametros: { type: 'object', properties: {} },
  ejecutar: async () => api.pedir('/reports/inventory-stats'),
});

export const ToolRotacionModelos = new Tool({
  nombre: 'rotacion_modelos',
  descripcion: 'Modelos con más movimientos (entradas/salidas) en periodo. Para detectar fast-movers.',
  parametros: {
    type: 'object',
    properties: {
      dias: { type: 'integer', minimum: 1, maximum: 365, default: 30 },
      top: { type: 'integer', minimum: 1, maximum: 50, default: 10 },
    },
  },
  ejecutar: async ({ dias = 30, top = 10 }) => api.pedir('/reports/rotation', { params: { dias, top } }),
});

export const ToolTiempoInstalacion = new Tool({
  nombre: 'tiempo_instalacion',
  descripcion: 'Promedio días desde OUT_TO_CLIENT hasta INSTALLATION por modelo/cliente.',
  parametros: {
    type: 'object',
    properties: {
      model_id: { type: 'integer', minimum: 1 },
      cliente_id: { type: 'integer', minimum: 1 },
      dias: { type: 'integer', minimum: 1, maximum: 365, default: 90 },
    },
  },
  ejecutar: async (params) => api.pedir('/reports/install-time', { params }),
});

// ============================================================================
// TOOLS GENERALES (Todos los agentes)
// ============================================================================

export const ToolConsultaSQL = new Tool({
  nombre: 'consulta_sql',
  descripcion: 'Ejecuta SELECT arbitrario (solo lectura) via LLM. Para queries complejas que no cubren otras tools.',
  parametros: {
    type: 'object',
    properties: {
      sql: { type: 'string', description: 'SELECT válido. Sin INSERT/UPDATE/DELETE/DDL.' },
      params: { type: 'array', items: { type: 'string' }, description: 'Parámetros para query parametrizada' },
    },
    required: ['sql'],
  },
  ejecutar: async ({ sql, params = [] }) => api.pedir('/ai/sql', { metodo: 'POST', cuerpo: { sql, params } }),
});

// Exportar todos
export const AllTools = [
  // Inventario
  ToolBuscarDispositivo,
  ToolListarDispositivos,
  ToolStockPorModelo,
  ToolStockBajo,
  ToolHistorialDispositivo,
  ToolEscanear,
  // Mantenimiento
  ToolMantenimientosPendientes,
  ToolPlanesMantenimiento,
  ToolCompletarMantenimiento,
  ToolGenerarMantenimientos,
  // Compras
  ToolCrearOrdenCompra,
  ToolListarOrdenesCompra,
  ToolRecibirOrdenCompra,
  ToolSugerirReorden,
  // Reportes
  ToolReporteOperativo,
  ToolEstadisticasInventario,
  ToolRotacionModelos,
  ToolTiempoInstalacion,
  // General
  ToolConsultaSQL,
];

// Registrar todos
AllTools.forEach(t => console.debug(`[ToolRegistry] Registrado: ${t.nombre}`));