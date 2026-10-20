/**
 * Los campos de atributo de una categoria y los helpers de campo que los
 * acompanian.
 *
 * POR QUE ESTE ARCHIVO APARTE
 * ---------------------------
 * Los atributos de una categoria (caducidad, lote, alto, ancho) los USA el
 * formulario de producto (para llenarlos) y el gestor de categorias (para
 * definirlos). Con el codigo en un solo archivo, uno de los dos lo arrastra
 * al otro: el gestor de categorias terminaba importando el formulario de
 * producto entero, y al revés.
 *
 * QUE SON LOS ATRIBUTOS
 * ---------------------
 * Cada categoria puede definir sus propios campos: una de monitores tiene
 * "alto" y "ancho", una de consumibles tiene "caducidad" y "lote". Los
 * VALORES de cada producto viven en `models.specifications` (un JSONB), y el
 * backend los valida contra esta definicion.
 */

import { crear } from '../dom.js';
import { conTooltip } from '../tooltip.js';

function etiquetaConDescripcion(texto, control, descripcion) {
  const etiqueta = crear('label', { clase: 'campo' });
  const textoEtiqueta = crear('span', { clase: 'campo__etiqueta', texto });
  etiqueta.append(textoEtiqueta, control);
  if (descripcion) conTooltip(textoEtiqueta, descripcion);
  return etiqueta;
}

/**
 * Los controles de los atributos de la categoria.
 *
 * Un Map de clave -> control. El Map es global al modulo porque el formulario
 * de alta y el de edicion existen a la vez en algunos momentos, y cada uno
 * pinta sus propios campos: con un Map por formulario habria que acordarse de
 * cual se esta leyendo al mandar.
 */
const controlesAtributos = new Map();

/**
 * Pinta los campos de los atributos de una categoria.
 *
 * QUE SON
 * -------
 * Cada categoria define sus propios campos: una de monitores tiene "alto" y
 * "ancho", una de consumibles tiene "caducidad" y "lote". El backend valida
 * los valores contra esa definicion, asi que el frontend no decide nada: solo
 * pide la definicion y arma el control que corresponde.
 *
 * @param {HTMLElement} zona      Donde se pintan los campos.
 * @param {string|number} categoriaId
 * @param {object} valores        Valores ya cargados (edicion).
 */
async function pintarAtributos(zona, categoriaId, valores = {}) {
  controlesAtributos.clear();
  zona.replaceChildren();

  if (!categoriaId) return;

  let atributos;
  try {
    atributos = await api.pedir(`/catalog/categories/${categoriaId}/attributes`);
  } catch (error) {
    // Los atributos son un extra: si no se pueden cargar, el producto se
    // guarda igual, sin sus atributos.
    console.warn('No se pudieron cargar los atributos:', error);
    return;
  }

  if (!atributos.length) return;

  const titulo = crear('p', { clase: 'campo__ayuda', texto: 'Atributos de la categoria' });
  zona.append(titulo);

  for (const atributo of atributos) {
    const valor = valores[atributo.key] ?? '';

    let control;
    if (atributo.data_type === 'select') {
      const opciones = Array.isArray(atributo.options)
        ? atributo.options
        : (atributo.options ? JSON.parse(atributo.options) : []);
      control = crear('select', { clase: 'campo__control' });
      control.append(
        crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true } }),
      );
      for (const opcion of opciones) {
        control.append(crear('option', { texto: String(opcion), atributos: { value: opcion } }));
      }
      if (valor !== '') control.value = String(valor);
    } else if (atributo.data_type === 'boolean') {
      control = crear('input', {
        clase: 'campo__control',
        atributos: { type: 'checkbox' },
      });
      control.checked = valor === true || String(valor).toLowerCase() === 'true';
    } else {
      control = crear('input', {
        clase: 'campo__control',
        atributos: {
          type: atributo.data_type === 'number' ? 'number' : atributo.data_type === 'date' ? 'date' : 'text',
          autocomplete: 'off',
          ...(valor !== '' && valor !== null ? { value: String(valor) } : {}),
        },
      });
    }

    const etiqueta = crear('span', {
      clase: 'campo__etiqueta',
      texto: atributo.label + (atributo.is_required ? ' *' : '') + (atributo.unit ? ` (${atributo.unit})` : ''),
    });

    zona.append(crear('label', { clase: 'campo', hijos: [etiqueta, control] }));
    controlesAtributos.set(atributo.key, control);
  }
}

/**
 * Los valores de los atributos que el operador lleno.
 *
 * Un control vacio NO se manda: el backend lo tomaria como "borrar el valor"
 * en la edicion, y en el alta seria un atributo sin valor.
 */
function leerAtributos() {
  const valores = {};
  for (const [clave, control] of controlesAtributos) {
    if (control.type === 'checkbox') {
      if (control.checked) valores[clave] = true;
      continue;
    }
    const valor = (control.value ?? '').toString().trim();
    if (valor !== '') valores[clave] = valor;
  }
  return valores;
}

export {
  controlesAtributos,
  etiquetaConDescripcion,
  leerAtributos,
  pintarAtributos,
};
