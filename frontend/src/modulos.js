/**
 * Los dos módulos de la app: Inventario y Service Desk.
 *
 * QUE SON DOS Módulos Y NO UN TABLERO CON DOS BLOQUES
 * ---------------------------------------------------
 * Inventario y Service Desk responden a preguntas distintas, las tienen
 * personas distintas y se usan en lugares distintos: el depósito y el taller.
 * Meterlos en la misma pantalla hace que cada usuario lea la mitad que no le
 * importa, y que la que le importa quede tapada.
 *
 * LA ENTRADA DEPENDE DEL ROL
 * --------------------------
 * - OPERATOR:  trabaja en el depósito. Ve Solo Inventario.
 * - TECHNICIAN: trabaja en el taller. Ve Solo Service Desk.
 * - ADMIN:      ve los dos, y elige en la pantalla de entrada.
 *
 * QUE PASA CON LAS Pestañas
 * --------------------------
 * El módulo decide QUE Pestañas se ven. Un técnico no ve "Productos" (el
 * maestro de SKUs) ni "Compras": le son datos, no tareas. Y un operador no ve
 * "Auditoría": no tiene permiso, y una pestaña que siempre da 403 es ruido.
 *
 * EL DASHBOARD
 * ------------
 * Cada módulo tiene su tablero. El de Inventario pregunta "donde está cada
 * equipo y que repongo"; el de Service Desk, "que casos tengo abiertos y
 * cuales se me van a vencer". Son preguntas distintas: un solo tablero con
 * las dos respuestas es la versión peor de las dos.
 */

export const MODULOS = {
  inventario: {
    clave: 'inventario',
    etiqueta: 'Inventario',
    titulo: 'Inventario',
    descripcion: 'Equipos, stock y movimientos',
    detalle:
      'Donde está cada equipo, cuanto vale, que hay que reponer y que se movio hoy.',
    icono: 'M20 7l-8-4-8 4-8 4zM5 7l-8-4-8 4-8 4z',
    // `existencia`: el stock por rubro y por ubicación. Va después de
    // `inventario` (que es el equipo) y antes de `movimientos` (que es el
    // historial): primero donde está cada equipo, depois cuanto hay de cada
    // rubro.
    //
    // `usuarios` está en los DOS módulos a propósito: administrar usuarios
    // no es una tarea del depósito ni del taller, es de la casa, y tiene que
    // estar disponible desde los dos. (Cuando no estaba en ninguna de las
    // dos listas, la pantalla de usuarios era inalcanzable: el admin no
    // tenía por dónde dar de alta a nadie.)
    vistas: ['inicio', 'escanear', 'inventario', 'existencia', 'movimientos', 'productos', 'compras', 'nuevo', 'usuarios'],
    dashboard: 'inventario',
  },
  helpdesk: {
    clave: 'helpdesk',
    etiqueta: 'Service Desk',
    titulo: 'Service Desk',
    descripcion: 'Incidencias, SLA y taller',
    detalle:
      'Los problemas reportados, los que se vencen y el trabajo del taller: mantenimiento y compras atrasadas.',
    icono: 'M12 8v4M12 16h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z',
    // La flota SI está en el Service Desk: el técnico tiene que ver el equipo
    // que está fallando para diagnosticarlo. Lo que no está es el maestro de
    // productos: eso es tarea del depósito.
    //
    // `usuarios` está en los dos módulos (ver la nota del módulo Inventario):
    // administrar usuarios es de la casa, no del taller.
    vistas: ['inicio', 'incidencias', 'inventario', 'consultas', 'auditoria', 'usuarios'],
    dashboard: 'helpdesk',
  },
  escanear: {
    clave: 'escanear',
    etiqueta: 'Escanear',
    titulo: 'Escanear',
    descripcion: 'Lectura de códigos de barras y QR',
    icono: 'M3 7l9 12 9-12 9-12 9zM3 7l9 12 9-12 9-12 9z',
    vistas: ['escanear'],
    dashboard: 'escanear',
  },
};

/**
 * Los módulos que un rol puede usar.
 *
 * EL ROL Y NO LOS Permisos
 * ------------------------
 * El rol decide QUE Módulos SE OFRECEN, porque el módulo es una decisión
 * operativa ("yo trabajo el depósito" / "yo trabajo el taller"), no una
 * capacidad puntual.
 *
 * Los permisos siguen siendo la fuente de verdad de lo que se PUEDE hacer
 * dentro de un módulo: un ADMIN que elige Service Desk no puede tocar los
 * precios de los productos, porque "Productos" no está en las pestañas de ese
 * módulo y el backend lo rechaza igual.
 *
 * El fallback por permisos cubre un rol desconocido: se ofrece lo que los
 * permisos permitan, en vez de una pantalla vacía.
 */
const MODULOS_POR_ROL = {
  ADMIN: ['inventario', 'helpdesk'],
  TECHNICIAN: ['helpdesk'],
  OPERATOR: ['inventario'],
};

export function modulosDe(usuario) {
  const rol = (usuario?.role_name ?? '').toUpperCase();
  const porRol = MODULOS_POR_ROL[rol];
  if (porRol) return porRol;

  // Rol desconocido: se decide por los permisos que envió el servidor.
  const permisos = usuario?.permisos ?? [];
  const disponibles = [];
  if (permisos.includes('devices:read')) disponibles.push('inventario');
  if (permisos.includes('incidents:read')) disponibles.push('helpdesk');
  return disponibles.length ? disponibles : ['inventario'];
}

/**
 * Las pestañas de un módulo.
 *
 * Vive acá y no en `navegacion.js` para no hacer un ciclo: la cabecera
 * necesita saber que pestañas se ven, y `navegacion.js` necesita repintar la
 * cabecera. Con la función acá, ninguno de los dos importa al otro.
 *
 * @param {string} módulo
 * @returns {Set<string>}
 */
export function pestanasDelModulo(modulo) {
  const definicion = MODULOS[modulo] ?? MODULOS.inventario;
  return new Set(definicion.vistas);
}

/**
 * El módulo que se abre por defecto para un rol.
 *
 * Con un solo módulo, no hay nada que elegir: se entra directo. Con dos, el
 * admin elige en la pantalla de entrada.
 */
export function moduloPorDefecto(usuario) {
  const disponibles = modulosDe(usuario);
  return disponibles[0] ?? 'inventario';
}
