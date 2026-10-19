/**
 * Diálogo modal genérico.
 *
 * POR QUE ESTE ARCHIVO
 * --------------------
 * `main.js` tenía la mecánica de modal —crear, atrapar el foco, cerrar con
 * Escape, sacar del DOM— escrita dos veces, en el de cerrar sesión y en el de
 * login por sesión vencida. Agregar un tercero (código no encontrado) sin
 * factorizar era copiar la parte que más se rompe: el foco atrapado y el
 * `aria-modal` no fallan de forma visible, y sin ellos el teclado del operador
 * se va a la página de atrás por debajo del modal.
 *
 * `dialogo.js` no importa nada de `main.js`: las vistas también lo necesitan, y
 * `main.js` importa las vistas. Importar de arriba hacia abajo y de abajo hacia
 * arriba en el mismo par de archivos es un ciclo, y los ciclos en módulos ES
 * dejan variables en `undefined` sin avisar.
 */

import { crear } from './dom.js';

const FOCUSEABLES = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Abre un modal y devuelve una función para cerrarlo.
 *
 * @param {object} opciones
 * @param {string} opciones.titulo
 * @param {string} [opciones.mensaje]     Párrafo principal. Va por `textContent`.
 * @param {string} [opciones.nota]        Párrafo secondary, más chico.
 * @param {string} [opciones.clase]       Clase extra del modal.
 * @param {Array}  [opciones.acciones]    Elementos `<button>`, en orden.
 * @param {Array}  [opciones.cuerpo]      Nodos ya construidos, para cuando hace
 *                                        falta algo que no sea texto (un form).
 * @param {Function}[opciones.alAbrir]    Recibe `(modal, cerrar)`. Lo que se
 *                                        enganche con `cerrar` se puede cerrar.
 * @returns {() => void}
 */
export function abrirDialogo({
  titulo,
  mensaje,
  nota,
  cuerpo = [],
  clase = '',
  acciones = [],
  alAbrir,
}) {
  const focoPrevio = document.activeElement;

  const modal = crear('div', {
    clase: `modal ${clase}`.trim(),
    // Los atributos van en `atributos`: `crear` descarta en silencio cualquier
    // clave suelta, y un modal sin `role` ni `aria-modal` es invisible para el
    // lector de pantalla aunque se vea perfecto.
    atributos: { role: 'dialog', 'aria-modal': 'true' },
  });

  const idTitulo = `dialogo-titulo-${contador()}`;
  const idMensaje = `dialogo-mensaje-${contador()}`;

  const h = crear('h3', { id: idTitulo, texto: titulo });

  // La tarjeta blanca. El CSS la espera como `.modal__contenido` dentro del
  // overlay, y ningun modal la creaba: se veia el texto suelto sobre el fondo
  // oscurecido, sin caja, sin ancho y sin bordes.
  const tarjeta = crear('div', { clase: 'modal__contenido' });
  tarjeta.append(h);

  if (mensaje) {
    tarjeta.append(crear('p', { id: idMensaje, texto: mensaje }));
    modal.setAttribute('aria-describedby', idMensaje);
  }
  for (const nodo of cuerpo) if (nodo) tarjeta.append(nodo);
  if (nota) {
    tarjeta.append(crear('p', { clase: 'modal__nota', texto: nota }));
  }
  if (acciones.length) {
    tarjeta.append(crear('div', { clase: 'modal__acciones', hijos: acciones }));
  }

  modal.setAttribute('aria-labelledby', idTitulo);
  modal.append(tarjeta);

  document.body.append(modal);

  const focusables = modal.querySelectorAll(FOCUSEABLES);
  const primero = focusables[0];
  const ultimo = focusables[focusables.length - 1];

  function atraparFoco(e) {
    if (e.key !== 'Tab') return;
    // Con un solo focoable, el foco no tiene a donde ir y hay que evitar que
    // Tab lo saque del modal.
    if (focusables.length === 1) {
      e.preventDefault();
      primero.focus();
      return;
    }
    if (e.shiftKey && document.activeElement === primero) {
      e.preventDefault();
      ultimo.focus();
    } else if (!e.shiftKey && document.activeElement === ultimo) {
      e.preventDefault();
      primero.focus();
    }
  }

  function alEscape(e) {
    if (e.key === 'Escape') cerrar();
  }

  let cerrado = false;
  function cerrar() {
    if (cerrado) return;
    cerrado = true;
    modal.remove();
    document.removeEventListener('keydown', alEscape);
    document.removeEventListener('keydown', atraparFoco);
    // El foco vuelve a donde estaba: si no, al cerrarse el modal el operador
    // queda con el foco en el body y el próximo Tab lo manda al principio de la
    // pagina, no al escaner que tiene delante.
    focoPrevio?.focus?.();
  }

  modal.addEventListener('keydown', atraparFoco);
  document.addEventListener('keydown', alEscape);

  primero?.focus();
  alAbrir?.(modal, cerrar);

  return cerrar;
}

let _contador = 0;
function contador() {
  _contador += 1;
  return _contador;
}