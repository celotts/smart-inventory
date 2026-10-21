/**
 * Saneado de SVG antes de meterlo en la pagina.
 *
 * POR QUE EXISTE ESTE ARCHIVO
 * ---------------------------
 * El backend genera los SVG del QR y del codigo de barras, y son nuestro.
 * Pero un SVG no es una imagen: es un documento, y un documento puede llevar
 * `<script>`, manejadores `onclick`, `<foreignObject>` con HTML dentro, o
 * `<image href="https://tercero/...">` (una peticion a un servidor ajeno con
 * la IP del operador).
 *
 * Hoy el codigo quita los `<script>` y ya. Eso alcanza PARA NUESTRO SVG,
 * pero alcanza por casualidad, no por regla: en cuanto un dia el backend
 * meta el nombre del producto dentro del SVG (es el caso natural: la etiqueta
 * deberia mostrar el nombre), un producto con un nombre que rompa el XML
 * seria un problema, y un SVG con `<a href>` o un `onload` seria un problema
 * mayor.
 *
 * QUE HACE
 * --------
 * - Parsea con `DOMParser` en modo XML (no HTML: asi no se "repara" solo).
 * - Rechaza el SVG si no es un SVG o si el parseo fallo.
 * - Quita `<script>`, `<foreignObject>`, `<iframe>`, `<object>`, `<embed>`,
 *   `<use>` (puede traer contenido externo por `href`) y `<image>` (que
 *   puede pedir un recurso de otro servidor).
 * - Quita TODOS los atributos que empiezan por `on` (`onclick`, `onload`,
 *   `onerror`...).
 * - Quita los `href`/`xlink:href` que no sean `#...` (los internos).
 * - Quita los `style` con `url(` o `expression(` (un CSS puede cargar una
 *   imagen externa o ejecutar codigo en motores viejos).
 *
 * Lo que se devuelve es un `<svg>` listo para `importNode`.
 */

/** Las etiquetas que no tienen por que estar en un SVG de etiqueta. */
const ETIQUETAS_VEDADAS = [
  'script',
  'foreignobject',
  'iframe',
  'object',
  'embed',
  'use',
  'image',
  'audio',
  'video',
  'animate',
  'set',
  'handler',
];

/** Los atributos `on...` (manejadores de evento). */
const ATRIBUTO_EVENTO = /^on/i;

/** `style` con `url(` o `expression(`. */
const CSS_PELIGROSO = /(url\s*\(|expression\s*\()/i;

/**
 * Parsea y sanea un SVG.
 *
 * @param {string} texto  El SVG completo, tal cual lo devuelve la API.
 * @returns {Element}      El elemento `<svg>` ya saneado.
 * @throws {Error}         Si no es un SVG valido.
 */
export function sanearSvg(texto) {
  const doc = new DOMParser().parseFromString(texto, 'image/svg+xml');

  // Un SVG mal formado NO lanza: `DOMParser` devuelve un documento con un
  // `<parsererror>` adentro. Sin este chequeo, se insertaria el error de
  // parseo en la etiqueta.
  const raiz = doc.documentElement;
  if (!raiz || raiz.nodeName.toLowerCase() !== 'svg' || doc.querySelector('parsererror')) {
    throw new Error('El backend no devolvio un SVG válido.');
  }

  // 1. Las etiquetas vedadas.
  for (const etiqueta of ETIQUETAS_VEDADAS) {
    for (const nodo of raiz.querySelectorAll(etiqueta)) nodo.remove();
  }

  // 2. Los atributos de cada elemento.
  //
  // `querySelectorAll('*')` no trae al elemento raiz, asi que se recorre
  // tambien el raiz a mano.
  const elementos = [raiz, ...raiz.querySelectorAll('*')];
  for (const elemento of elementos) {
    for (const atributo of [...elemento.attributes]) {
      const nombre = atributo.name.toLowerCase();
      const valor = atributo.value || '';

      if (ATRIBUTO_EVENTO.test(nombre)) {
        elemento.removeAttribute(atributo.name);
        continue;
      }

      if ((nombre === 'href' || nombre === 'xlink:href') && !valor.startsWith('#')) {
        // Solo se quedan los enlaces internos (los `use` con `#id`).
        elemento.removeAttribute(atributo.name);
        continue;
      }

      if (nombre === 'style' && CSS_PELIGROSO.test(valor)) {
        elemento.removeAttribute(atributo.name);
      }
    }
  }

  return raiz;
}

/**
 * Parsea, sanea y devuelve el SVG listo para insertar en `document`.
 *
 * @param {string} texto
 * @param {Document} destino  El documento donde se va a insertar
 *                            (`document` o el de la ventana de impresion).
 * @returns {Element}
 */
export function svgComoNodo(texto, destino = document) {
  const raiz = sanearSvg(texto);
  const nodo = destino.importNode(raiz, true);
  return nodo;
}
