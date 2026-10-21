/**
 * La etiqueta del producto: su QR y su código de barras.
 *
 * QUE ES ESTO
 * -----------
 * Un producto del maestro ("una notebook tal modelo: hay cinco en el
 * depósito") tiene su propia etiqueta, distinta de la etiqueta del equipo:
 * la del equipo es la que ya existe, con el `qr_code` del componente y su
 * serial. Está es la de la CAJA, la del rack, la del estante: lo que se
 * imprime y se pega para reconocer el producto sin abrir el inventario.
 *
 * LOS DOS Códigos
 * ---------------
 * - El QR (`PRD-<sku>` por defecto) se escanea con la camara del celular.
 * - El código de barras (Code 128, con el SKU) se escanea con un lector
 *   lineal, que es lo que hay en el mostrador de un depósito.
 *
 * Los dos los dibuja el BACKEND, en SVG. Ver `src/modules/catalog/qr.py` y
 * `src/modules/catalog/barcode.py` del backend: es código de la etiqueta, y
 * la etiqueta tiene que salir Idéntica en la pantalla y en la impresora.
 *
 * POR QUE EL SVG VA INLINE
 * ------------------------
 * La API exige el token en el header `Authorization`, y un `<img>` no puede
 * mandarlo: pediria el recurso y recibiria un 401. Además la CSP de la app
 * prohibe los `data:` URL. Un SVG inline es parte del documento: no lo
 * alcanza la CSP, y se ve nitido a cualquier tamaño.
 *
 * El SVG se parsea con `DOMParser` —que NO ejecuta scripts, a diferencia de
 * `innerHTML`— y se le borra cualquier `<script>` que viniera dentro, por
 * principio y no por necesidad.
 */

import { crear } from './dom.js';
import { api } from './api.js';
import { svgComoNodo as svgSaneado } from './svg-seguro.js';

/**
 * Pide un SVG a la API y lo devuelve como nodo del documento.
 *
 * @param {string} ruta      Ruta relativa a /api. Ej: '/catalog/products/7/qr'
 * @param {string} alt       Texto alternativo, para el lector de pantalla.
 * @returns {Promise<Element>}
 */
async function svgComoNodo(ruta, alt) {
  const svgTexto = await api.pedir(ruta, { comoTexto: true });

  // El SVG pasa por el saneador: sin scripts, sin manejadores de evento, sin
  // referencias a recursos de otros servidores. Es codigo de nuestro propio
  // backend, pero un SVG no tiene por que traer esas cosas.
  //
  // El import se renombra (`svgSaneado`) porque esta función tambien se
  // llama `svgComoNodo`: dos declaraciones con el mismo nombre en el mismo
  // modulo son un `SyntaxError`, y un `SyntaxError` en un modulo aborta el
  // arranque de la app entera.
  const nodo = svgSaneado(svgTexto);
  nodo.setAttribute('role', 'img');
  nodo.setAttribute('aria-label', alt);
  return nodo;
}

/**
 * Correlaciona un contenedor con la aparicion en pantalla.
 *
 * LA LISTA DE PRODUCTOS
 * ---------------------
 * La lista puede tener cincuenta productos, y cada uno con su QR: cincuenta
 * peticiones a la API para mostrar imagenes que el operador todavía no miro.
 * Con este observador, la imagen se pide cuando el producto entra en la
 * pantalla (o está a punto de entrar), no antes.
 *
 * @param {Element} contenedor
 * @param {Function} alEntrar
 */
export function cuandoVisible(contenedor, alEntrar) {
  if (!('IntersectionObserver' in window)) {
    alEntrar();
    return;
  }

  const observador = new IntersectionObserver(
    (entradas) => {
      for (const entrada of entradas) {
        if (entrada.isIntersecting) {
          observador.disconnect();
          alEntrar();
          return;
        }
      }
    },
    { rootMargin: '250px' },
  );

  observador.observe(contenedor);
}

/**
 * El QR del producto, en un contenedor listo para insertar.
 *
 * @param {number} productoId
 * @param {string} texto       El código que codifica, para el texto alternativo.
 * @param {number} [tamaño]    Lado en px.
 * @param {boolean} [perezoso] Si es true, la imagen se pide recien cuando el
 *                             contenedor aparece en pantalla.
 * @returns {HTMLElement}
 */
export function imagenQrProducto(productoId, texto, tamano = 120, { perezoso = false } = {}) {
  const contenedor = crear('div', { clase: 'qr-etiqueta qr-etiqueta--producto' });
  contenedor.style.width = `${tamano}px`;
  contenedor.style.height = `${tamano}px`;

  if (perezoso) {
    cuandoVisible(contenedor, () => cargarQr(contenedor, productoId, texto, tamano));
  } else {
    cargarQr(contenedor, productoId, texto, tamano);
  }

  return contenedor;
}

function cargarQr(contenedor, productoId, texto, tamano) {
  svgComoNodo(`/catalog/products/${productoId}/qr`, `Codigo QR del producto ${texto}`)
    .then((nodo) => {
      nodo.setAttribute('width', String(tamano));
      nodo.setAttribute('height', String(tamano));
      contenedor.replaceChildren(nodo);
    })
    .catch(() => {
      // El QR es una ayuda, no el dato: si falla, el código en texto sigue
      // estando al lado. No se avisa, porque seria un error por cada producto
      // sin imagen, en cada lista.
      contenedor.classList.add('qr-etiqueta--vacia');
    });
}

/**
 * El código de barras del producto, en un contenedor listo para insertar.
 *
 * @param {number} productoId
 * @param {string} texto
 * @param {number} [ancho]     Ancho en px.
 * @returns {HTMLElement}
 */
export function imagenBarcodeProducto(productoId, texto, ancho = 280) {
  const contenedor = crear('div', { clase: 'codigo-barras' });

  svgComoNodo(`/catalog/products/${productoId}/barcode`, `Codigo de barras del producto ${texto}`)
    .then((nodo) => {
      nodo.setAttribute('width', String(ancho));
      nodo.removeAttribute('height');
      nodo.style.width = `${ancho}px`;
      nodo.style.height = 'auto';
      contenedor.replaceChildren(nodo);
    })
    .catch(() => {
      contenedor.classList.add('codigo-barras--vacio');
    });

  return contenedor;
}

/**
 * El SVG crudo de los dos códigos, para imprimir.
 *
 * La ventana de impresión no puede pedir nada a la API: no tiene el token.
 * Por eso se Trae el SVG ya descargado y se lo escribe tal cual.
 *
 * @param {number} productoId
 * @returns {Promise<{qr: string, barcode: string}>}
 */
export async function svgsDeEtiqueta(productoId) {
  const [qr, codigoBarras] = await Promise.all([
    api.pedir(`/catalog/products/${productoId}/qr`, { comoTexto: true }),
    api.pedir(`/catalog/products/${productoId}/barcode`, { comoTexto: true }),
  ]);
  return { qr, barcode: codigoBarras };
}
