/**
 * Los dos modulos de la app: Inventario y Service Desk.
 *
 * QUE SON DOS MODULOS Y NO UN TABLERO CON DOS BLOQUES
 * ---------------------------------------------------
 * Inventario y Service Desk responden a preguntas distintas, las tienen
 * personas distintas y se usan en lugares distintos: el deposito y el taller.
 * Meterlos en la misma pantalla hace que cada usuario lea la mitad que no le
 * importa, y que la que le importa quede tapada.
 *
 * LA ENTRADA DEPENDE DEL ROL
 * --------------------------
 * - OPERATOR:  trabaja en el deposito. Ve SOLO Inventario.
 * - TECHNICIAN: trabaja en el taller. Ve SOLO Service Desk.
 * - ADMIN:      ve los dos, y elige en la pantalla de entrada.
 *
 * QUE PASA CON LAS PESTANAS
 * --------------------------
 * El modulo decide QUE PESTANAS se ven. Un tecnico no ve "Productos" (el
 * maestro de SKUs) ni "Compras": le son datos, no tareas. Y un operador no ve
 * "Auditoria": no tiene permiso, y una pestana que siempre da 403 es ruido.
 *
 * EL DASHBOARD
 * ------------
 * Cada modulo tiene su tablero. El de Inventario pregunta "donde esta cada
 * equipo y que repongo"; el de Service Desk, "que casos tengo abiertos y
 * cuales se me van a vencer". Son preguntas distintas: un solo tablero con
 * las dos respuestas es la version peor de las dos.
 */

export const MODULOS = {
  inventario: {
    clave: 'inventario',
    etiqueta: 'Inventario',
    titulo: 'Inventario',
    descripcion: 'Equipos, stock y movimientos',
    detalle:
      'Donde esta cada equipo, cuanto vale, que hay que reponer y que se movio hoy.',
    icono: 'M20 7l-8-4-8 4-8 4zM5 7l-8-4-8 4-8 4z',
    vistas: ['inicio', 'escanear', 'inventario', 'movimientos', 'productos', 'compras', 'nuevo'],
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
    // La flota SI esta en el Service Desk: el tecnico tiene que ver el equipo
    // que esta fallando para diagnosticarlo. Lo que no esta es el maestro de
    // productos: eso es tarea del deposito.
    vistas: ['inicio', 'incidencias', 'inventario', 'consultas', 'auditoria'],
    dashboard: 'helpdesk',
  },
};

/**
 * Los modulos que un rol puede usar.
 *
 * EL ROL Y NO LOS PERMISOS
 * ------------------------
 * El rol decide QUE MODULOS SE OFRECEN, porque el modulo es una decision
 * operativa ("yo trabajo el deposito" / "yo trabajo el taller"), no una
 * capacidad puntual.
 *
 * Los permisos siguen siendo la fuente de verdad de lo que se PUEDE hacer
 * dentro de un modulo: un ADMIN que elige Service Desk no puede tocar los
 * precios de los productos, porque "Productos" no esta en las pestanas de ese
 * modulo y el backend lo rechaza igual.
 *
 * El fallback por permisos cubre un rol desconocido: se ofrece lo que los
 * permisos permitan, en vez de una pantalla vacia.
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

  // Rol desconocido: se decide por los permisos que envio el servidor.
  const permisos = usuario?.permisos ?? [];
  const disponibles = [];
  if (permisos.includes('devices:read')) disponibles.push('inventario');
  if (permisos.includes('incidents:read')) disponibles.push('helpdesk');
  return disponibles.length ? disponibles : ['inventario'];
}

/**
 * Las pestanas de un modulo.
 *
 * Vive aca y no en `navegacion.js` para no hacer un ciclo: la cabecera
 * necesita saber que pestanas se ven, y `navegacion.js` necesita repintar la
 * cabecera. Con la funcion aca, ninguno de los dos importa al otro.
 *
 * @param {string} modulo
 * @returns {Set<string>}
 */
export function pestanasDelModulo(modulo) {
  const definicion = MODULOS[modulo] ?? MODULOS.inventario;
  return new Set(definicion.vistas);
}

/**
 * El modulo que se abre por defecto para un rol.
 *
 * Con un solo modulo, no hay nada que elegir: se entra directo. Con dos, el
 * admin elige en la pantalla de entrada.
 */
export function moduloPorDefecto(usuario) {
  const disponibles = modulosDe(usuario);
  return disponibles[0] ?? 'inventario';
}
