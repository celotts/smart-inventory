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
 * Pila de dialogos abiertos.
 *
 * POR QUE HACE FALTA
 * -----------------
 * Un dialogo de confirmacion puede abrirse ENCIMA de otro (el formulario de
 * edicion pide confirmacion al guardar). Con dos modales abiertos, Escape
 * cerraba los dos de una: el operador percia el formulario y se queda con
 * la pantalla de fondo y un aviso de error sin saber por que.
 *
 * Con la pila, cada modal sabe si es el de arriba: Escape y el atrape de foco
 * solo los aplica el ultimo, que es el unico que el operador esta viendo.
 */
const pila = [];

/** El ancho de la barra de scroll, para compensarla cuando se bloquea. */
let anchoBarra = 0;

/**
 * Bloquea el scroll del fondo mientras hay un modal abierto.
 *
 * POR QUE
 * -------
 * Con el modal abierto, la pagina de atras sigue bajando con el rueda del
 * mouse: el operador ve el modal quieto y el fondo moviendose debajo, que se
 * lee como "la app se colgo".
 *
 * El `padding-right` con el ancho de la barra compensa el espacio que deja de
 * ocupar: sin eso, la pagina se desplaza unos pixeles al abrir el modal, y
 * con ella todo el contenido (formularios, listas, el cursor del operador).
 *
 * Se hace por CONTADOR y no por una bandera: puede haber dos modales
 * apilados (el de edicion pide confirmacion al guardar), y el scroll se
 * desbloquea recien cuando se cierra el ULTIMO.
 */
function bloquearScroll() {
  if (pila.length > 0) return;

  const estilos = getComputedStyle(document.body);
  anchoBarra = window.innerWidth - document.documentElement.clientWidth;

  if (anchoBarra > 0) {
    document.body.style.paddingRight = `${anchoBarra}px`;
  }
  document.body.classList.add('modal-abierto');
}

function desbloquearScroll() {
  if (pila.length > 0) return;

  document.body.classList.remove('modal-abierto');
  document.body.style.paddingRight = '';
  anchoBarra = 0;
}

/** Si el modal es el de arriba, es el unico que el operador ve. */
function esSuperior(modal) {
  return pila[pila.length - 1] === modal;
}

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
 * @param {Function}[opciones.alCerrar]   Recibe `()`. Se llama cuando el modal
 *                                        se cierra de verdad (no cuando se
 *                                        cancela un guardado). Sirve para
 *                                        guardar el estado de un formulario.
 * @param {boolean}[opciones.avanzarAlCerrar]  Si es true, se avisa antes de
 *                                        cerrar y el cierre se cancela si el
 *                                        operador no confirma. Es lo que evita
 *                                        perder diez campos escritos.
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
  alCerrar,
  avanzarAlCerrar = false,
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
  pila.push(modal);
  bloquearScroll();

  // LA FOTO DE LOS VALORES
  //
  // Para saber si el operador ESCRIBIO algo, se compara cada control con
  // el valor que tenia cuando se abrio el modal. Asi el aviso de "hay
  // cambios sin guardar" aparece SOLO si hay cambios de verdad: antes
  // aparecia siempre, y abrir un formulario y tocar el fondo preguntaba
  // "descartar lo que escribiste" con el formulario vacio.
  const valoresAlAbrir = new Map();
  for (const control of modal.querySelectorAll('input, select, textarea')) {
    valoresAlAbrir.set(control, control.value);
  }

  /** Hay cambios sin guardar. */
  function hayCambiosSinGuardar() {
    for (const [control, valorInicial] of valoresAlAbrir) {
      if (control.value !== valorInicial) return true;
    }
    return false;
  }

  const focusables = modal.querySelectorAll(FOCUSEABLES);
  const primero = focusables[0];
  const ultimo = focusables[focusables.length - 1];

  function atraparFoco(e) {
    if (e.key !== 'Tab') return;
    // Solo el modal de arriba atrapa el foco: si no, el de abajo seguiria
    // Honorando su Tab y el foco saltaria del modal nuevo al viejo.
    if (!esSuperior(modal)) return;
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
    // Escape cierra SOLO el modal de arriba. Con un formulario de edicion
    // abierto debajo, cerrar los dos de una dejaba al operador en la pagina
    // de fondo sin saber que habia pasado.
    if (e.key === 'Escape' && esSuperior(modal)) cerrar();
  }

  let cerrado = false;

  /**
   * Cierra el modal.
   *
   * @param {boolean} [forzar]  Cierra sin preguntar, aunque haya cambios sin
   *                             guardar. Lo usa el guardado exitoso: despues
   *                             de guardar, preguntar "¿descartar los
   *                             cambios?" no tiene sentido.
   */
  function cerrar(forzar = false) {
    if (cerrado) return;

    // SOLO `true` descarta el aviso.
    //
    // `cerrar` se pasa directo como manejador de un boton: `addEventListener`
    // le pasa el EVENTO como primer argumento, y un evento es truthy. Con un
    // `if (forzar)` normal, el clic de "Cancelar" se tomaba como un "si,
    // descartá todo" y el aviso nunca aparecia.
    const descartar = forzar === true;

    // AVISO DE CAMBIOS SIN GUARDAR
    //
    // Un modal de edicion con diez campos, y el operador escribio cinco y
    // aprieto "Cancelar" (o Escape, o el clic afuera). Se perdio trabajo sin
    // avisar, y el operador no se da cuenta hasta que lo reescribe.
    //
    // El aviso se hace con un modal encima, porque la confirmacion tiene dos
    // respuestas y una needs attention: un `confirm()` del navegador rompe el
    // tema y el foco.
    if (avanzarAlCerrar && !descartar && hayCambiosSinGuardar()) {
      const btnPerder = crear('button', {
        clase: 'boton boton--peligro',
        texto: 'Descartar los cambios',
        atributos: { type: 'button' },
      });
      const btnSeguir = crear('button', {
        clase: 'boton boton--suave',
        texto: 'Seguir editando',
        atributos: { type: 'button' },
      });

      abrirDialogo({
        titulo: 'Hay cambios sin guardar',
        mensaje: 'Cerrar ahora descarta lo que escribiste.',
        nota: 'Si querés conservarlo, volve a abrir la edicion.',
        clase: 'modal--alerta',
        acciones: [btnSeguir, btnPerder],
        alAbrir: (_aviso, cerrarAviso) => {
          btnSeguir.addEventListener('click', () => cerrarAviso());
          btnPerder.addEventListener('click', () => {
            cerrarAviso();
            cerrar(true);
          });
        },
      });
      return;
    }

    cerrado = true;
    modal.remove();
    const i = pila.indexOf(modal);
    if (i !== -1) pila.splice(i, 1);
    desbloquearScroll();
    document.removeEventListener('keydown', alEscape);
    document.removeEventListener('keydown', atraparFoco);
    alCerrar?.();
    // El foco vuelve a donde estaba: si no, al cerrarse el modal el operador
    // queda con el foco en el body y el próximo Tab lo manda al principio de la
    // pagina, no al escaner que tiene delante.
    focoPrevio?.focus?.();
  }

  modal.addEventListener('keydown', atraparFoco);
  document.addEventListener('keydown', alEscape);

  // TOCAR EL FONDO CIERRA EL MODAL
  //
  // Es la afordancia que todo el mundo espera, y es la que evita que un
  // modal sin boton de cerrar deje al operador atrapado: en un celular no
  // hay tecla Escape, y sin boton no hay salida.
  //
  // Solo cierra si el clic fue en el FONDO (el `target` es el overlay), no
  // dentro del contenido: un clic en un campo de texto no cierra nada.
  //
  // Pasa por `cerrer()`, que ya pregunta por los cambios sin guardar: un
  // formulario con datos escritos avisa antes de perderlos.
  modal.addEventListener('click', (evento) => {
    if (evento.target !== modal) return;
    if (!esSuperior(modal)) return;
    cerrar();
  });

  primero?.focus();
  alAbrir?.(modal, cerrar);

  return cerrar;
}

let _contador = 0;
function contador() {
  _contador += 1;
  return _contador;
}

/**
 * Pide confirmacion y devuelve una promesa.
 *
 * @param {object} opciones
 * @param {string} opciones.titulo
 * @param {string} [opciones.mensaje]
 * @param {string} [opciones.nota]
 * @param {string} [opciones.textoConfirmar]  Texto del boton de confirmar.
 * @param {string} [opciones.textoCancelar]   Texto del boton de cancelar.
 * @param {string} [opciones.clase]           Clase extra del modal.
 * @param {boolean}[opciones.peligro]         Boton de confirmar en rojo.
 * @returns {Promise<boolean>} `true` si el operador confirmo.
 */
export function confirmar({
  titulo,
  mensaje,
  nota,
  textoConfirmar = 'Confirmar',
  textoCancelar = 'Cancelar',
  clase = 'modal--confirmacion',
  peligro = false,
}) {
  return new Promise((resolver) => {
    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: textoCancelar,
      atributos: { type: 'button' },
    });
    const btnAceptar = crear('button', {
      clase: `boton${peligro ? ' boton--peligro' : ''}`,
      texto: textoConfirmar,
      atributos: { type: 'button' },
    });

    abrirDialogo({
      titulo,
      mensaje,
      nota,
      clase,
      acciones: [btnCancelar, btnAceptar],
      alAbrir: (_modal, cerrar) => {
        btnCancelar.addEventListener('click', () => {
          resolver(false);
          cerrar();
        });
        btnAceptar.addEventListener('click', () => {
          resolver(true);
          cerrar();
        });
      },
    });
  });
}