/**
 * La navegacion entre vistas.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * `main.js` mezclaba el arranque, la sesión, la cabecera y la navegacion. Con
 * el módulo de incidencias y el de mantenimiento, la navegacion dejo de ser
 * "cambiar el texto de la pestaña": cada vista tiene su ciclo de vida (la
 * camara del escaner, el polling de la campana, el timer del recibo). Eso es
 * un TEMA con sus propias reglas, y mezclarlo con el arranque hace que
 * cualquiera de los dos crezca hasta ser inmanejable.
 *
 * EL CICLO DE VIDA DE UNA VISTA
 * ----------------------------
 * Al cambiar de vista, la anterior se DESTRUYE: sin eso, la camara del
 * escaner sigue encendida, el polling de la campana sigue preguntando y el
 * timer del recibo sigue corriendo. Cada vista devuelve `{ elemento,
 * destruir? }` y el `destruir` es donde suelta lo que tiene.
 */

import { vistaAuditoria } from './vistas/auditoria.js';
import { vistaCompras } from './vistas/compras.js';
import { vistaConsultas } from './vistas/consultas.js';
import { vistaEscanear } from './vistas/escanear.js';
import { vistaExistencia } from './vistas/existencia.js';
import { vistaIncidencias } from './vistas/incidencias.js';
import { vistaInicio } from './vistas/inicio.js';
import { vistaInventario, vistaDispositivo } from './vistas/inventario.js';
import { vistaMovimientos } from './vistas/movimientos.js';
import { vistaNuevo } from './vistas/nuevo.js';
import { vistaProductos } from './vistas/productos.js';
import { vistaUsuarios } from './vistas/usuarios.js';
import { reemplazar } from './dom.js';
import { permisosDe, setModulo, setVistaActual, estado } from './estado.js';
import { MODULOS, modulosDe, pestanasDelModulo } from './modulos.js';
import { actualizarCabeceraUsuario } from './cabecera.js';

/** El registro de vistas. La clave es la que viaja en el hash de la URL. */
export const VISTAS = {
  inicio: { etiqueta: 'Inicio', construir: vistaInicio },
  escanear: { etiqueta: 'Escanear', construir: vistaEscanear },
  inventario: { etiqueta: 'Inventario', construir: vistaInventario },
  existencia: { etiqueta: 'Existencia', construir: vistaExistencia },
  movimientos: { etiqueta: 'Movimientos', construir: vistaMovimientos },
  incidencias: { etiqueta: 'Incidencias', construir: vistaIncidencias },
  consultas: { etiqueta: 'Consultas', construir: vistaConsultas },
  productos: { etiqueta: 'Productos', construir: vistaProductos },
  compras: { etiqueta: 'Compras', construir: vistaCompras },
  auditoria: { etiqueta: 'Auditoria', construir: vistaAuditoria },
  nuevo: { etiqueta: 'Nuevo', construir: vistaNuevo },
  usuarios: { etiqueta: 'Usuarios', construir: vistaUsuarios },
};

const contenido = document.getElementById('contenido');
const cabecera = document.getElementById('cabecera');

/**
 * Muestra una vista.
 *
 * @param {string} nombre    La clave de la vista.
 * @param {object} [opciones]
 * @param {number} [opciones.deviceId]  Para la ficha de un equipo.
 */
export function mostrarVista(nombre, opciones = {}) {
  // Si se cambia de vista, la anterior se destruye. Una vista puede tener
  // recursos que no se limpian solos: la camara del escaner, por ejemplo, que
  // sigue encendida si no se apaga explicitamente.
  if (estado.vistaActual?.destruir) {
    try {
      estado.vistaActual.destruir();
    } catch (error) {
      // Que un fallo al limpiar no impida mostrar la vista siguiente: el
      // operador necesita trabajar aunque la camara no se haya podido apagar.
      console.error('No se pudo limpiar la vista anterior:', error);
    }
  }

  if (nombre === 'dispositivo') {
    // El id del equipo puede venir plano (`deviceId`) o dentro de los
    // filtros (cuando el salto viene del tablero, que manda todo en
    // `filtros`).
    const deviceId = opciones.deviceId ?? opciones.filtros?.deviceId;
    const vista = vistaDispositivo(deviceId, {
      alVolver: () => mostrarVista('inventario'),
      // Editar y dar de baja son acciones de `devices:edit`. La ficha decide si
      // muestra los botones con este dato; el backend los vuelve a exigir igual.
      permisos: permisosDe(),
      alEliminar: () => mostrarVista('inventario'),
    });
    setVistaActual(vista, nombre);
  } else {
    const definicion = VISTAS[nombre];
    if (!definicion) return;
    const vista = definicion.construir({
      alAbrirDispositivo: (id) => mostrarVista('dispositivo', { deviceId: id }),
      permisos: permisosDe(),
      // El módulo va a la vista: el tablero y los filtros dependen de si se
      // está trabajando en Inventario o en Service Desk.
      modulo: estado.modulo,
      // Los filtros de entrada: cuando se hace clic en un KPI del tablero, la
      // vista se abre YA filtrada. El operador no tiene que reconstruir en
      // el filtro lo que el tablero le acaba de decir.
      filtros: opciones.filtros ?? null,
    });
    setVistaActual(vista, nombre);
  }

  // Toda vista devuelve `{ elemento, destruir? }`. Aceptar también un elemento
  // suelto evita que una vista nueva rompa por no conocer el contrato, pero el
  // contrato nuevo es el que se escribe.
  const elemento = estado.vistaActual?.elemento ?? estado.vistaActual;

  reemplazar(contenido, elemento);

  // La app empezo: la navegacion y el selector de módulo vuelven.
  cabecera.classList.remove('cabecera--lanzador');

  // La app YA ARRANCO. El vigilante de `arranque.js` mira está marca para no
  // pisar una app que ya está pintada: sin ella, un arranque lento (celular
  // con 4G de segundo plano, o un contenedor que tarda en servir los módulos)
  // agotaba el límite de 12 segundos y reemplazaba la pantalla por "La app no
  // pudo arrancar" cuando la app estaba funcionando.
  document.body.dataset.appLista = '1';

  // `aria-current` en la pestaña activa, y el estado del historial del
  // navegador para que el botón "atras" del celular funcione.
  for (const boton of cabecera.querySelectorAll('[data-vista]')) {
    if (boton.dataset.vista === nombre) boton.setAttribute('aria-current', 'page');
    else boton.removeAttribute('aria-current');
  }

  if (window.location.hash !== `#${nombre}`) {
    history.pushState({ vista: nombre }, '', `#${nombre}`);
  }
}

/**
 * Cambia de módulo y vuelve al tablero de ese módulo.
 *
 * @param {string} módulo
 * @param {object} usuario
 */
export function cambiarModulo(modulo, usuario) {
  if (!modulosDe(usuario).includes(modulo)) return;
  // `setModulo` deja el módulo en el `body`: el CSS lo usa para pintar con
  // el color del módulo (azul Inventario, ambar Service Desk).
  setModulo(modulo);
  // Las pestañas dependen del módulo: hay que repintarlas o la app queda
  // mostrando las del módulo anterior.
  actualizarCabeceraUsuario();
  mostrarVista('inicio');
}

/**
 * La vista de la ficha de un equipo.
 *
 * Vive acá (y no en `VISTAS`) porque NO es una pestaña: es el destino de
 * "Ver la ficha" desde el inventario o desde un escaneo. Va aparte para que
 * el `hash` de la URL no la confunda con una sección.
 */
export const VISTA_DISPOSITIVO = 'dispositivo';
