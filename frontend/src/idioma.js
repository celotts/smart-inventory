/**
 * El cambió de idioma de la INTERFAZ.
 *
 * QUE ES Y QUE NO ES
 * -------------------
 * Es el idioma de los textos de la app: los botones, las etiquetas, los
 * mensajes y los avisos.
 *
 * NO es el idioma de los DATOS: los SKUs, los nombres de los equipos, los
 * títulos de los casos y las descripciones que escribio el usuario se
 * quedan como están. Traducirlos seria inventar información que no existe
 * (y el nombre de un cliente es el nombre de un cliente, en el idioma en que
 * lo escribio su dueño).
 *
 * COMO SE APLICA
 * --------------
 * Todo el texto de la pantalla pasa por `crear()` (el helper que arma el
 * DOM), así que el traductor va ahí y alcanza a todo. Los textos estaticos
 * del `index.html` (el login, el pie de página) se recorren al arrancar.
 *
 * PARA QUE SIRVA DE VERDAD
 * -------------------------
 * Cambiar el idioma recarga la app: las vistas se pintan con textos que ya
 * están en el árbol, y traducirlos "en vivo" seria recorrer la pantalla
 * entera. La recarga es un instante y deja TODO coherente.
 */

import { IDIOMAS, TRADUCCIONES, leerIdiomaGuardado, setIdioma, traducir, idioma } from './i18n.js';

/**
 * Los textos estaticos del HTML, con su traduccion.
 *
 * El `data-i18n` del HTML es la clave en el diccionario; el texto que se ve
 * en espanol está en el elemento, y se usa como fallback si la clave no
 * está en el diccionario.
 */
// Los textos estaticos se traducen con el DICCIONARIO COMUN, no con una
// lista propia: así, toda frase del HTML que este en el diccionario se
// traduce (las pestañas del menú, el pie de página, el login), y no hay dos
// listas que se desincronicen.

/**
 * Traduce los textos ESTATICOS del HTML.
 *
 * Se recorren los nodos con texto propio: los que no tienen hijos (el texto
 * es de ellos) y los que tienen un texto directamente (los botones con un
 * ícono y un texto).
 */
function traducirEstaticos() {
  const raiz = document.body;
  if (!raiz) return;

  const recorrer = (nodo) => {
    for (const hijo of nodo.childNodes) {
      if (hijo.nodeType === Node.TEXT_NODE) {
        const texto = hijo.textContent.trim();
        const traduccion = TRADUCCIONES[texto];
        if (texto && traduccion) {
          hijo.textContent = traduccion;
        }
        continue;
      }
      if (hijo.nodeType !== Node.ELEMENT_NODE) continue;

      // Los atributos con texto.
      for (const atributo of ['title', 'aria-label', 'placeholder']) {
        const valor = hijo.getAttribute?.(atributo);
        const traduccion = TRADUCCIONES[valor];
        if (valor && traduccion) {
          hijo.setAttribute(atributo, traduccion);
        }
      }

      recorrer(hijo);
    }
  };

  recorrer(raiz);
}

/**
 * Pinta el botón de idioma y lo connects con el cambió.
 *
 * @param {Function} alCambiar  Se llama después de cambiar (para recargar).
 */
export function iniciarSelectorIdioma(alCambiar) {
  const boton = document.getElementById('btn-idioma');
  const texto = document.getElementById('btn-idioma-texto');
  if (!boton) return;

  const pintar = () => {
    const actual = idioma();
    const info = IDIOMAS.find((i) => i.clave === actual) ?? IDIOMAS[0];
    if (texto) texto.textContent = info.corto;
    boton.setAttribute('title', `Idioma / Language — ${info.etiqueta}`);
    boton.setAttribute('aria-label', `Cambiar el idioma / Change language (${info.etiqueta})`);
  };

  boton.addEventListener('click', () => {
    const siguiente = idioma() === 'es' ? 'en' : 'es';
    setIdioma(siguiente);
    pintar();
    alCambiar?.(siguiente);
  });

  pintar();
}

/**
 * Deja la app en el idioma guardado.
 *
 * Se llama al arrancar, ANTES de pintar la primera pantalla: si se hiciera
 * después, la pantalla apareceria en espanol y saltaria al ingles.
 */
export function aplicarIdiomaGuardado() {
  const guardado = leerIdiomaGuardado();
  if (guardado !== 'es') {
    setIdioma(guardado);
  }
  if (idioma() === 'en') {
    traducirEstaticos();
  }
  return idioma();
}
