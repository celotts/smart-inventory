/**
 * El estado compartido de la app.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * El usuario conectado y la vista actual los necesita el módulo de sesión
 * (que los cambia), el de navegacion (que los lee) y el de la cabecera (que
 * los muestra). Con los tres importing de `main.js`, `main.js` los importaria
 * de vuelta: un CICLO de módulos, que en ESM no rompe pero deja el estado en
 * `undefined` en el primer arranque, y no hay error que lo diga.
 *
 * ES UN Solo OBJETO, NO VARIABLES SUELTAS
 * ---------------------------------------
 * Con `export let usuario`, el que lo importa ve el valor actualizado (los
 * bindings son vivos), pero no puede cambiarlo. Con un objeto y funciones para
 * cambiarlo, el contrato es explicito y no depende de un detalle del
 * lenguaje.
 */

/** El usuario conectado (con sus permisos). */
export const estado = {
  usuario: null,
  vistaActual: null,
  vistaActualNombre: null,
  // El módulo en el que se está trabajando: `inventario` o `helpdesk`.
  // Decide que pestañas se ven y que tablero se pinta.
  modulo: 'inventario',
};

/** Cambia el módulo de trabajo. */
export function setModulo(modulo) {
  estado.modulo = modulo;
  // El `body` lleva el módulo: el CSS usa `body[data-módulo]` para pintar
  // con el color del módulo (azul Inventario, ambar Service Desk). Sin esto,
  // el color vive solo en los botones de la cabecera.
  document.body.dataset.modulo = modulo;
}

/** Guarda el usuario conectado. */
export function setUsuario(usuario) {
  estado.usuario = usuario ?? null;
}

/** Guarda la vista que está en pantalla. */
export function setVistaActual(vista, nombre) {
  estado.vistaActual = vista ?? null;
  if (nombre !== undefined) estado.vistaActualNombre = nombre;
}

/**
 * Que permisos tiene este usuario.
 *
 * Viene del login y del endpoint `/auth/yo`. Si todavía no se sabe (la sesión
 * se está restaurando), se cae al fallback por rol, que es una COPIA de
 * `core/permissions.py` del backend: sirve unicamente para pintar la UI antes
 * de que llegue la respuesta real.
 */
export function permisosDe(usuarioActual = estado.usuario) {
  const lista = usuarioActual?.permisos;
  if (Array.isArray(lista)) return lista;

  const porRol = {
    ADMIN: [
      'users:read', 'users:create', 'users:edit', 'users:delete', 'users:reset_password',
      'roles:read', 'roles:edit',
      'devices:read', 'devices:edit', 'movements:read', 'movements:create',
      'repairs:read', 'repairs:edit', 'repairs:assign',
      'incidents:read', 'incidents:create', 'incidents:manage',
      'ai:query', 'ai:analyze', 'ai:report', 'audit:read',
    ],
    TECHNICIAN: [
      'devices:read', 'repairs:read', 'repairs:edit',
      'incidents:read', 'incidents:create', 'incidents:manage',
      'ai:query', 'ai:analyze', 'ai:report',
    ],
    OPERATOR: [
      'devices:read', 'movements:read', 'movements:create',
      'incidents:read', 'incidents:create',
      'ai:query', 'ai:report',
    ],
  };

  return porRol[usuarioActual?.role_name] ?? [];
}
