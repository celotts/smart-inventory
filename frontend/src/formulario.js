/**
 * Campos de formulario con validacion en linea.
 *
 * QUE HACE ESTO Y POR QUE NO BASTA CON `avisar()`
 * -------------------------------------------------
 * Un formulario que avisa el error en un cartel que desaparece a los 4
 * segundos deja al operador con la duda de QUE campo esta mal. Peor: si
 * escribio 12 campos, tiene que recorrerlos todos adivinando.
 *
 * Aqui el error va:
 *
 *   1. Debajo del campo, junto a lo que esta mal.
 *   2. Con el campo marcado (`aria-invalid="true"` y un borde rojo): el
 *      operador ve el problema sin leer.
 *   3. Con el foco movido al primer campo con error: el teclado va solo al
 *      lugar donde hay que corregir.
 *   4. Con el error ANUNCIADO (`role="alert"`), para el lector de pantalla.
 *
 * EL CONTRATO
 * -----------
 * `campoTexto`, `campoSelect` y `campoNumero` devuelven un objeto con:
 *
 *   - `campo`   el `<label>` completo, para agregar al formulario.
 *   - `input`   el control.
 *   - `error`   el `<p>` del error (oculto hasta que hay algo que decir).
 *   - `marcar(mensaje)`  muestra el error y marca el control.
 *   - `limpiar()`        saca el error.
 *   - `invalidar(mensaje)`  atajo de `marcar` para uso en el `catch`.
 *
 * El control y el texto de error quedan enlazados con `aria-describedby`, que
 * es lo que hace que un lector de pantalla lea el error al enfocar el campo.
 */

import { crear } from './dom.js';
import { conTooltip } from './tooltip.js';

/**
 * El tooltip va en la ETIQUETA del campo, no en el control.
 *
 * POR QUE NO EN EL CONTROL
 * ------------------------
 * Un tooltip en un `<input>` desaparece en cuanto el operador hace clic: esta
 * escribiendo, y lo que necesita es leer la ayuda SIN perder el foco. En un
 * celular, ademas, el tooltip nativo no aparece nunca.
 *
 * La etiqueta es lo que el operador mira ANTES de hacer clic, y lo que el
 * lector de pantalla anuncia junto al campo. Ahi el tooltip aporta.
 *
 * Y la ayuda que de verdad importa —como se usa el campo— esta SIEMPRE
 * visible, debajo, y enlazada con `aria-describedby`.
 */

/** Contador para los ids unicos de los errores. */
let contador = 0;

/**
 * Construye el bloque de un campo: etiqueta, control, ayuda y error.
 *
 * @param {object} opciones
 * @param {string} opciones.etiqueta   Texto de la etiqueta.
 * @param {HTMLElement} opciones.control
 * @param {string} [opciones.ayuda]    Texto de ayuda (queda siempre visible).
 * @param {boolean} [opciones.requerido]
 * @returns {object} El contrato del campo.
 */
function construirCampo({ etiqueta, control, ayuda, requerido = false, descripcion }) {
  const idCampo = `campo-${(contador += 1)}`;
  const idError = `${idCampo}-error`;

  // El `id` explicito y el `for` de la etiqueta: la etiqueta envuelve al
  // control (asociacion implicita, que ya funciona), pero el par
  // `for`/`id` es lo que permite que un lector de pantalla salte al control
  // con el atajo de formulario, y que las pruebas puedan apuntar a un campo
  // concreto.
  if (!control.id) control.id = idCampo;

  const textoEtiqueta = crear('span', {
    clase: 'campo__etiqueta',
    texto: requerido ? `${etiqueta} *` : etiqueta,
  });

  const nodoAyuda = ayuda
    ? crear('p', { clase: 'campo__ayuda', texto: ayuda, id: `${idError}-ayuda` })
    : null;

  const nodoError = crear('p', {
    clase: 'campo__error',
    id: idError,
    atributos: { role: 'alert' },
  });
  nodoError.hidden = true;

  const campo = crear('label', { clase: 'campo' });
  campo.htmlFor = control.id;
  campo.append(textoEtiqueta, control);
  if (nodoAyuda) campo.append(nodoAyuda);
  campo.append(nodoError);

  if (requerido) control.setAttribute('aria-required', 'true');

  // El tooltip de la etiqueta: la descripcion corta de que es el campo. La
  // ayuda larga va debajo, siempre visible.
  if (descripcion) conTooltip(textoEtiqueta, descripcion);

  // El `aria-describedby` se arma con las partes que existen: la ayuda y/o el
  // error. El error se agrega cuando aparece.
  const descritos = [];
  if (nodoAyuda) descritos.push(nodoAyuda.id);
  const sincronizarDescritos = () => {
    const ids = descritos.filter((id) => id !== idError);
    if (!nodoError.hidden) ids.push(idError);
    if (ids.length) control.setAttribute('aria-describedby', ids.join(' '));
    else control.removeAttribute('aria-describedby');
  };
  sincronizarDescritos();

  const marcar = (mensaje) => {
    nodoError.textContent = mensaje;
    nodoError.hidden = false;
    control.setAttribute('aria-invalid', 'true');
    descritos.push(idError);
    sincronizarDescritos();
  };

  const limpiar = () => {
    nodoError.textContent = '';
    nodoError.hidden = true;
    control.removeAttribute('aria-invalid');
    const i = descritos.indexOf(idError);
    if (i !== -1) descritos.splice(i, 1);
    sincronizarDescritos();
  };

  // Cuando el usuario corrige el campo, el error se va: dejarlo puesto
  // mientras escribe es ruido.
  control.addEventListener('input', () => {
    if (control.getAttribute('aria-invalid') === 'true') limpiar();
  });

  return { campo, input: control, control, error: nodoError, marcar, limpiar, invalidar: marcar };
}

/**
 * Campo de texto.
 *
 * @param {object} opciones
 * @param {string} opciones.etiqueta
 * @param {string} [opciones.placeholder]
 * @param {string} [opciones.ayuda]
 * @param {boolean} [opciones.requerido]
 * @param {string} [opciones.tipo]        'text' | 'email' | 'tel' | 'date' | 'password'
 * @param {string} [opciones.autocomplete]
 * @param {string} [opciones.inputmode]
 * @param {boolean} [opciones.mono]
 * @param {number} [opciones.maxlength]
 * @returns {object}
 */
export function campoTexto({
  etiqueta,
  placeholder = '',
  ayuda,
  descripcion,
  requerido = false,
  tipo = 'text',
  autocomplete = 'off',
  inputmode,
  mono = false,
  maxlength,
  name,
}) {
  const atributos = {
    type: tipo,
    placeholder,
    autocomplete,
    autocapitalize: 'none',
    autocorrect: 'off',
    spellcheck: 'false',
  };
  if (inputmode) atributos.inputmode = inputmode;
  if (maxlength) atributos.maxlength = String(maxlength);
  if (name) atributos.name = name;

  const control = crear('input', {
    clase: `campo__control${mono ? ' mono' : ''}`,
    atributos,
  });

  return construirCampo({ etiqueta, control, ayuda, requerido, descripcion });
}

/**
 * Campo numerico.
 *
 * @param {object} opciones
 * @param {string} opciones.etiqueta
 * @param {string} [opciones.ayuda]
 * @param {string} [opciones.placeholder]
 * @param {number} [opciones.min]
 * @param {number} [opciones.max]
 * @param {number} [opciones.step]
 * @param {boolean} [opciones.requerido]
 * @returns {object}
 */
export function campoNumero({
  etiqueta,
  ayuda,
  descripcion,
  placeholder = '',
  min,
  max,
  step,
  requerido = false,
  name,
}) {
  const atributos = { type: 'number', placeholder, autocomplete: 'off', inputmode: 'numeric' };
  if (min !== undefined) atributos.min = String(min);
  if (max !== undefined) atributos.max = String(max);
  if (step !== undefined) atributos.step = String(step);
  if (name) atributos.name = name;

  const control = crear('input', { clase: 'campo__control', atributos });

  return construirCampo({ etiqueta, control, ayuda, requerido, descripcion });
}

/**
 * Campo de seleccion.
 *
 * @param {object} opciones
 * @param {string} opciones.etiqueta
 * @param {Array<{valor: string, texto: string}>} opciones.opciones
 * @param {string} [opciones.ayuda]
 * @param {boolean} [opciones.requerido]
 * @param {string} [opciones.vacio] Texto de la primera opcion (sin valor).
 * @returns {object}
 */
export function campoSelect({ etiqueta, opciones = [], ayuda, descripcion, requerido = false, vacio, name }) {
  const atributos = { autocomplete: 'off' };
  if (name) atributos.name = name;

  const control = crear('select', { clase: 'campo__control', atributos });

  if (vacio !== undefined) {
    control.append(crear('option', { texto: vacio, atributos: { value: '' } }));
  }
  for (const opcion of opciones) {
    control.append(
      crear('option', { texto: opcion.texto, atributos: { value: opcion.valor } }),
    );
  }

  return construirCampo({ etiqueta, control, ayuda, requerido, descripcion });
}

/**
 * El error que se muestra arriba del formulario, resumiendo todos los
 * problemas.
 *
 * Un formulario largo con tres campos con error: el operador tiene que
 * enterarse de que hay un problema antes de llegar al final. El resumen dice
 * cuantos hay y Which son, y el foco va al primero.
 *
 * @param {Array<object>} campos  Los campos con error.
 * @returns {HTMLElement|null}
 */
export function resumenDeErrores(campos) {
  const conError = campos.filter((c) => c.control.getAttribute('aria-invalid') === 'true');
  if (!conError.length) return null;

  const caja = crear('div', { clase: 'formulario__resumen', atributos: { role: 'alert' } });
  caja.append(
    crear('p', {
      clase: 'formulario__resumen-titulo',
      texto:
        conError.length === 1
          ? 'Falta corregir 1 campo:'
          : `Faltan corregir ${conError.length} campos:`,
    }),
  );

  const lista = crear('ul', { clase: 'formulario__resumen-lista' });
  for (const campo of conError) {
    const mensaje = campo.error.textContent || 'Revisar este campo';
    const item = crear('li');
    const enlace = crear('button', {
      clase: 'enlace',
      texto: mensaje,
      atributos: { type: 'button' },
    });
    // El boton lleva el foco al campo: el operador no tiene que buscarlo.
    enlace.addEventListener('click', () => {
      campo.control.focus();
      campo.control.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    item.appendChild(enlace);
    lista.appendChild(item);
  }
  caja.appendChild(lista);

  return caja;
}

/**
 * Agrega la capacidad de marcar error a un control YA ARMADO.
 *
 * PARA QUE ESTA JUNTO A `campoTexto`
 * ----------------------------------
 * Los formularios que ya existian en la app estan armados a mano, con su
 * `crear('input')` y su `crear('label')`. Migrarlos todos al constructor
 * seria un cambio grande con riesgo de romper algo que funciona; lo que hace
 * falta es la parte de VALIDACION, que es la que se estaba haciendo con
 * avisos flotantes.
 *
 * Con esto, un formulario existente gana: el error debajo del campo, el
 * control marcado con `aria-invalid`, y el foco en el primer problema.
 *
 * @param {HTMLElement} control
 * @returns {{marcar: Function, limpiar: Function, nodo: HTMLElement}}
 */
export function errorDe(control, descripcion) {
  const contenedor = control.closest('.campo') || control.parentElement || control;
  const idError = `error-${(contador += 1)}`;

  // El tooltip va en la etiqueta del campo (la que el operador mira antes de
  // hacer clic), no en el control: en el control desaparece en cuanto el
  // operador empieza a escribir.
  if (descripcion) {
    const etiqueta = contenedor.querySelector('.campo__etiqueta');
    if (etiqueta) conTooltip(etiqueta, descripcion);
  }

  const nodo = crear('p', { clase: 'campo__error', id: idError, atributos: { role: 'alert' } });
  nodo.hidden = true;

  // El error va DESPUES del control (y despues de la ayuda, si la hay).
  const ayuda = contenedor.querySelector('.campo__ayuda');
  if (ayuda && ayuda.nextSibling) contenedor.insertBefore(nodo, ayuda.nextSibling);
  else contenedor.appendChild(nodo);

  const marcar = (mensaje) => {
    nodo.textContent = mensaje;
    nodo.hidden = false;
    control.setAttribute('aria-invalid', 'true');
    const descritos = (control.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
    if (!descritos.includes(idError)) descritos.push(idError);
    control.setAttribute('aria-describedby', descritos.join(' '));
  };

  const limpiar = () => {
    nodo.textContent = '';
    nodo.hidden = true;
    control.removeAttribute('aria-invalid');
    const descritos = (control.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
    control.setAttribute(
      'aria-describedby',
      descritos.filter((id) => id !== idError).join(' '),
    );
  };

  control.addEventListener('input', () => {
    if (control.getAttribute('aria-invalid') === 'true') limpiar();
  });

  // El mismo contrato que `campoTexto`, para que `resumenDeErrores` pueda
  // trabajar con los dos: un formulario armado a mano y uno con el
  // constructor se comportan igual.
  return { marcar, limpiar, nodo, control, error: nodo };
}
