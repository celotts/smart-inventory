/**
 * El QR de la etiqueta de un equipo.
 *
 * QUE HACE
 * --------
 * La ficha del componente y su formulario de edicion muestran el codigo de la
 * etiqueta como TEXTO (`INV-a1b2c3d4`) y, al lado, la imagen escaneable. El
 * texto sirve para leerlo y copiarlo; la imagen sirve para lo que de verdad
 * importa en un deposito: reimprimir la etiqueta, verificar de un vistazo que
 * el equipo es este, o pasarselo a otro operador para que lo escanee.
 *
 * POR QUE EL SVG LO GENERA EL BACKEND
 * ----------------------------------
 * El frontend no tiene ni una dependencia: ni un CDN, ni un `package.json` con
 * librerias. Meter un generador de QR en JS seria agregar un algoritmo entero
 * que mantener (mas de 200 lineas) para dibujar cuadritos. En el backend,
 * `qrcode` es Python puro: no compila nada y pesa 46 KB.
 *
 * POR QUE VA INLINE Y NO EN EL `src` DE UN `<img>`
 * -------------------------------------------------
 * Dos motivos, y los dos importan:
 *
 *  1. La API exige el token en el header `Authorization`, y un `<img>` no
 *     puede mandarlo: pediria el recurso y recibiria un 401.
 *
 *  2. Un `data:` URL (la forma de meter el SVG en el `src` sin tocar la CSP)
 *     esta BLOQUEADO por la politica de seguridad de esta app: `default-src
 *     'self'` sin `img-src` explicito la prohibe, y la consola lo dice:
 *     "violates the following Content Security Policy directive". Relajar la
 *     CSP para que entre una imagen seria cambiar la regla que protege a la
 *     app entera por un detalle de la vista.
 *
 *     Un SVG INLINE no es una imagen externa: es parte del documento, y la
 *     CSP no lo alcanza. Ademas se ve nitido a cualquier tamaño y se puede
 *     estilar con CSS.
 *
 * SEGURIDAD DEL PARSEO
 * --------------------
 * El SVG viene del backend de la app, por un canal autenticado: no es
 * contenido de terceros. Igual se parsea con `DOMParser` —que NO ejecuta
 * scripts, a diferencia de `innerHTML`— y se borra cualquier `<script>` que
 * viniera dentro, por principio y no por necesidad.
 */

import { crear } from './dom.js';
import { api } from './api.js';

/**
 * Devuelve el elemento con el QR.
 *
 * El elemento existe al toque y la imagen aparece cuando llega: la QR se
 * pide por red y no tiene sentido bloquear la ficha esperandola.
 *
 * @param {number} deviceId
 * @param {string} texto     El codigo de la etiqueta, para el texto alternativo.
 * @param {number} [tamano]  Lado en px.
 */
export function imagenQr(deviceId, texto, tamano = 180) {
  const contenedor = crear('div', { clase: 'qr-etiqueta' });

  api
    .pedir(`/devices/${deviceId}/qr`, { comoTexto: true })
    .then((svg) => {
      const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');

      // Un SVG mal formado no lanza: `DOMParser` devuelve un documento con un
      // `<parsererror>` adentro. Sin este chequeo, se insertaria el error de
      // parseo en la ficha.
      const raiz = doc.documentElement;
      if (!raiz || raiz.nodeName.toLowerCase() !== 'svg' || doc.querySelector('parsererror')) {
        throw new Error('El backend no devolvio un SVG valido.');
      }

      // Nada de scripts. Es codigo de nuestro propio backend, pero un SVG no
      // tiene por que traerlos.
      for (const script of raiz.querySelectorAll('script')) script.remove();

      const nodo = document.importNode(raiz, true);
      nodo.setAttribute('width', String(tamano));
      nodo.setAttribute('height', String(tamano));
      nodo.setAttribute('role', 'img');
      nodo.setAttribute('aria-label', `Codigo QR de la etiqueta ${texto}`);

      contenedor.replaceChildren(nodo);
    })
    .catch(() => {
      // El QR es una ayuda, no el dato: si falla, el codigo en texto sigue
      // estando al lado y el equipo sigue siendo identificable. No se avisa
      // porque seria un error por cada equipo sin imagen, en cada ficha.
      contenedor.classList.add('qr-etiqueta--vacia');
    });

  return contenedor;
}
