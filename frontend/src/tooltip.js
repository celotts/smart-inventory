/**
 * Tooltips accesibles.
 *
 * QUE NO ES ESTO
 * --------------
 * El atributo `title` de HTML. El `title` nativo:
 *
 *   - No aparece en un celular (iOS y Android lo ignoran al tocar).
 *   - Tarda mas de un segundo en aparecer y no se puede controlar.
 *   - No se muestra al navegar con el teclado, que es como operan los lectores
 *     de pantalla y los usuarios que no usan el mouse.
 *   - Aparece ENCIMA de la ventana del navegador, fuera de la pagina, con su
 *     propio estilo, que no sigue el tema de la app.
 *
 * Este tooltip es un elemento de la pagina: se ve al pasar el mouse Y al
 * enfocar con el teclado, se puede dar con el toque, y esta enlazado con
 * `aria-describedby`, asi que un lector de pantalla lo anuncia como
 * descripcion del control.
 *
 * LA REGLA DE ORO
 * ---------------
 * Un tooltip NUNCA es la unica forma de saber que hace un control. Es un
 * extra: el nombre accesible del control tiene que seguir siendo completo
 * sin el (`aria-label` o el texto visible). Por eso aca el tooltip se usa
 * para EXPLICAR, no para nombrar.
 *
 * UN SOLO ELEMENTO PARA TODA LA APP
 * ---------------------------------
 * Un tooltip por elemento seria un nodo en el DOM por cada control con
 * descripcion. Este usa uno solo, que se mueve de lugar. Menos DOM, menos
 * Layout y un solo lugar donde esta el estilo.
 */

import { crear } from './dom.js';

let elementoTooltip = null;
let destinoActual = null;
let temporizadorOcultar = null;
let contador = 0;

const RETARDO_APARECER = 250;
const DURACION_TACTIL = 2600;

/** El tooltip compartido. Se crea la primera vez que hace falta. */
function obtenerTooltip() {
  if (elementoTooltip) return elementoTooltip;

  elementoTooltip = crear('div', {
    id: 'tooltip-app',
    clase: 'tooltip',
    // `aria-hidden`: el tooltip VISUAL es un adorno. La descripcion que lee
    // el lector de pantalla es el texto oculto del control (el `span` con
    // `sr-only`), que existe siempre. Si el tooltip tambien fuera legible,
    // el lector anunciaria el texto DOS veces.
    atributos: { role: 'tooltip', 'aria-hidden': 'true' },
  });
  // El tooltip vive en el body y NO se lee solo: esta oculto para los lectores
  // hasta que tiene contenido, que es lo que hace `aria-describedby` (un
  // elemento vacio no describe nada).
  elementoTooltip.hidden = true;

  document.body.appendChild(elementoTooltip);
  return elementoTooltip;
}

/**
 * Coloca el tooltip junto al destino, dentro de la ventana.
 *
 * Se posiciona con `position: fixed` y coordenadas de pantalla: asi no hay
 * que preocuparse por los ancestros con `transform` o `overflow`, que es
 * donde se rompe un tooltip posicionado en el flujo normal.
 */
function posicionar(destino) {
  const tooltip = obtenerTooltip();
  const caja = destino.getBoundingClientRect();

  // Se mide primero para poder decidir si va arriba o abajo.
  const alto = tooltip.offsetHeight || 40;
  const ancho = tooltip.offsetWidth || 200;

  let top = caja.top - alto - 8;
  // Sin espacio arriba (barra del navegador, o el elemento pegado al techo),
  // va abajo.
  if (top < 8) top = caja.bottom + 8;

  let left = caja.left + caja.width / 2 - ancho / 2;
  // Nunca se sale de la ventana: un tooltip cortado no es un tooltip.
  left = Math.max(8, Math.min(left, window.innerWidth - ancho - 8));

  tooltip.style.top = `${Math.round(top)}px`;
  tooltip.style.left = `${Math.round(left)}px`;
}

function mostrar(destino, texto) {
  const tooltip = obtenerTooltip();

  // Un tooltip a la vez: si se pasa el mouse de un control a otro, el
  // anterior se esconde.
  ocultar();

  destinoActual = destino;
  tooltip.textContent = texto;
  tooltip.hidden = false;

  posicionar(destino);
}

function ocultar() {
  const tooltip = obtenerTooltip();
  destinoActual = null;
  tooltip.hidden = true;
  tooltip.textContent = '';
}

function programarOcultar() {
  if (temporizadorOcultar) clearTimeout(temporizadorOcultar);
  temporizadorOcultar = setTimeout(ocultar, 120);
}

/**
 * Le pone descripcion a un control.
 *
 * @param {HTMLElement} elemento  El control (boton, campo, tarjeta).
 * @param {string} texto          La descripcion.
 * @returns {HTMLElement} El mismo elemento, para encadenar.
 */
export function conTooltip(elemento, texto) {
  if (!texto) return elemento;

  // LA DESCRIPCION REAL: un texto oculto, con `aria-describedby` fijo.
  //
  // Va SIEMPRE, no solo cuando el tooltip se muestra. Un lector de pantalla
  // tiene que poder leer la explicacion de un control sin que nadie pase el
  // mouse por encima: `aria-describedby` que aparece y desaparece con el
  // tooltip hace que el texto se anuncie a destiempo, o no se anuncie.
  const idDescripcion = `descripcion-${(contador += 1)}`;
  const descripcion = crear('span', {
    id: idDescripcion,
    clase: 'sr-only',
    texto,
  });
  // Se pone como HERMANO del control, no dentro: dentro de un boton, el
  // texto pasaria a formar parte de su nombre accesible, y el boton se
  // anunciaria como "Cerrar sesion, Cerrar la sesion en este dispositivo".
  (elemento.parentElement || document.body).appendChild(descripcion);
  elemento.setAttribute('aria-describedby', idDescripcion);

  const mostrarConRetraso = () => {
    if (temporizadorOcultar) clearTimeout(temporizadorOcultar);
    temporizadorOcultar = setTimeout(() => mostrar(elemento, texto), RETARDO_APARECER);
  };

  elemento.addEventListener('mouseenter', mostrarConRetraso);
  elemento.addEventListener('mouseleave', programarOcultar);
  elemento.addEventListener('focus', mostrarConRetraso);
  elemento.addEventListener('blur', ocultar);

  // Toque: en un celular no hay hover. Se muestra al tocar y se esconde solo.
  elemento.addEventListener('touchstart', () => {
    mostrar(elemento, texto);
    if (temporizadorOcultar) clearTimeout(temporizadorOcultar);
    temporizadorOcultar = setTimeout(ocultar, DURACION_TACTIL);
  }, { passive: true });

  return elemento;
}

/**
 * Un boton de solo icono, con su nombre accesible y su descripcion.
 *
 * El nombre va en `aria-label` (lo lee el lector de pantalla) y la descripcion
 * va en el tooltip (lo ve el operador al pasar el mouse). Un boton de icono
 * sin `aria-label` es un boton que el lector de pantalla anuncia como
 * "boton", y no hay forma de saber que hace.
 *
 * @param {object} opciones
 * @param {string} opciones.nombre        Nombre accesible. Ej: 'Cerrar sesion'.
 * @param {string} [opciones.descripcion]  Texto del tooltip.
 * @param {string} [opciones.clase]
 * @param {string} [opciones.ruta]         Path del SVG.
 * @param {number} [opciones.tamano]
 * @returns {HTMLElement}
 */
export function botonIcono({ nombre, descripcion, clase = 'boton', ruta, tamano = 18 }) {
  const boton = crear('button', {
    clase,
    atributos: {
      type: 'button',
      'aria-label': nombre,
      title: descripcion || nombre,
    },
  });

  if (ruta) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', String(tamano));
    svg.setAttribute('height', String(tamano));
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('aria-hidden', 'true');

    const camino = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    camino.setAttribute('d', ruta);
    svg.appendChild(camino);

    boton.appendChild(svg);
  }

  if (descripcion) conTooltip(boton, descripcion);
  return boton;
}
