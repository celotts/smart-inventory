/**
 * Vista de escaneo - Rediseñada para mejor UX
 *
 * PRINCIPIOS DE DISEÑO (5 puntos implementados):
 * 1. CÁMARA PROTAGONISTA: Ocupa ~70% viewport, es la accion principal
 * 2. MANUAL COMO FALLBACK: Segmented control [Escanear] [Manual], manual colapsable
 * 3. AUTO-FOCO CÁMARA: Se enciende al entrar a la vista (si hay permiso)
 * 4. ENTER = PROCESAR: En input manual, Enter dispara igual que escanear
 * 5. TOOLTIPS + ARIA: Todos los botones tienen title + aria-label
 *
 * FUNCIONALIDAD PRESERVADA:
 * - Idempotencia con client_scan_id (UUID)
 * - Tipos de movimiento dinamicos desde /api/movement-types
 * - BarcodeDetector nativo (Chrome/Safari/Edge), fallback manual
 * - Geolocalizacion opcional
 * - Ficha rica: ubicación, historial, días instalado
 * - Estados: idle --> escaneando --> procesando --> éxito/error
 * - Accesibilidad: targets 48px+, labels ARIA, focus visible, alto contraste
 */

import { api } from '../api.js';
import { crearPaginacion } from '../paginacion.js';
import { crear, icono, ICONOS, ESTADOS, MOVIMIENTOS } from '../dom.js';
import { LectorQR, normalizarCodigo } from '../escaner.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { avisar } from '../avisos.js';
import { conTooltip } from '../tooltip.js';
import { descargarCsv } from '../descargas.js';

/** Tipos de movimiento - se cargan dinamicamente desde /api/movement-types */
let TIPOS_MOVIMIENTO = [];

export function vistaEscanear({ alAbrirDispositivo }) {
  const raiz = crear('div', { clase: 'vista-escanear' });

  // Estado local
  let tipoSeleccionado = 'OUT_TO_CLIENT';
  let scanIdPendiente = null;
  let envioEnCurso = false;
  let lector = null;
  let camaraIniciadaAuto = false;

  // Referencias DOM
  let zonaVacia = null;
  let mira = null;
  let botonCamara = null;
  let vacioIcono = null;
  let vacioTexto = null;
  let vacioPista = null;
  let timerSinLectura = null;
  let inputCodigo = null;
  // La lista del recibo de escaneos de la sesión.
  let listaRecibo = null;
  // TODOS los escaneos de la sesion, en una lista aparte.
  //
  // La lista del recibo muestra solo una pagina (el recibo crece con cada
  // escaneo y en un turno largo son cientos), pero el EXPORT tiene que
  // llevar todos: el CSV es el comprobante del conteo. Por eso el_array es
  // la verdad y la lista es solo lo que se ve.
  let itemsRecibo = [];
  let paginaRecibo = 0;
  let porPaginaRecibo = 25;
  let paginacionRecibo = null;
  // El chispoteo de "registrando…".
  let indicadorRegistro = null;
  let botonManual = null;
  let zonaResultado = null;
  let chipsContainer = null;
  let segmentoActivo = 'escanear'; // 'escanear' | 'manual'

  // Configuración: modo video visible (debug/demo) vs modo mira (producción)
  let mostrarVideo = false; // false = modo mira (solo overlay), true = video visible

  // ============================================================
  // CONSTRUCCION DE LA UI
  // ============================================================

  // ----- 1. Tarjeta: Tipo de movimiento (chips) -----
  const tarjetaTipo = crear('section', { clase: 'tarjeta tarjeta--accion' });
  chipsContainer = crear('div', {
    clase: 'chips-movimiento',
    role: 'radiogroup',
    'aria-label': 'Tipo de movimiento',
    id: 'chips-movimiento',
  });

  tarjetaTipo.append(
    crear('header', {
      clase: 'tarjeta__header',
      // Los hijos van DENTRO de las opciones. `crear(etiqueta, opciones)` no
      // tiene un tercer parámetro: pasarlos aparte los descarta en silencio y
      // el header sale vacío, sin título.
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: '¿Que estás haciendo?' }),
        crear('p', { clase: 'tarjeta__subtitulo', texto: 'Selecciona la accion antes de escanear' }),
      ],
    }),
    chipsContainer
  );

  // ----- 2. Tarjeta: Escaner (CÁMARA PROTAGONISTA) -----
  // El `<video>` NO se crea acá: lo crea `LectorQR` y se expone en
  // `elementoVideo`. Si la vista fabrica uno propio, quedan DOS `<video>` en el
  // marco: el de la vista, al que nunca se le asigna `srcObject`, y el del
  // lector, que tiene la imagen pero es el que la vista no muestra. El
  // resultado es un rectangulo negro con "cámara apagada" arriba y un video
  // encendido abajo, y ningún botón lo arregla porque el estado dice "on".
  //
  // `lector` se crea más abajo, cuando ya existe `marcoVideo`; el video se
  // resuelve en `iniciar()`/`apagarCamara()` con `lector.elementoVideo`.

  // Estado del marco: apagada | encendiendo | error.
  //
  // `getUserMedia` no es instantaneo: abre el permiso del navegador y, la
  // primera vez, espera a que el operador lo apruebe. Durante ese tiempo el
  // marco se ve igual que apagado, así que parece que el botón no funciono.
  // Con un giro y un texto que dice que está pasando, la espera se lee como
  // espera y no como fallo.
  zonaVacia = crear('div', { clase: 'escaner-vacio' });
  vacioIcono = crear('div', { clase: 'escaner-vacio__icono' });
  vacioIcono.append(icono(ICONOS.camara, { tamano: 48 }));
  vacioTexto = crear('p', { clase: 'escaner-vacio__texto', texto: 'Cámara apagada' });
  vacioPista = crear('p', {
    clase: 'escaner-vacio__hint',
    texto: 'Toca "Encender cámara" para escanear',
  });
  zonaVacia.append(vacioIcono, vacioTexto, vacioPista);

  /**
   * Pinta el estado del marco cuando no hay imagen.
   * @param {'apagada'|'encendiendo'|'error'} estado
   */
  function pintarVacio(estado) {
    zonaVacia.classList.toggle('escaner-vacio--cargando', estado === 'encendiendo');
    // La mira (las cuatro esquinas y la viñeta) solo tiene sentido sobre una
    // imagen real. Con la cámara apagada se dibujaba encima del mensaje y
    // dejaba el marco con un rectangon oscuro sin explicacion.
    mira.hidden = true;

    if (estado === 'encendiendo') {
      vacioTexto.textContent = 'Encendiendo cámara…';
      vacioPista.textContent = 'Acepta el permiso si el navegador lo pide.';
      vacioIcono.replaceChildren(crear('div', { clase: 'girador' }));
      return;
    }

    vacioIcono.replaceChildren(icono(estado === 'error' ? ICONOS.error : ICONOS.camara, { tamano: 48 }));

    if (estado === 'error') {
      vacioTexto.textContent = 'No se pudo encender la cámara';
      vacioPista.textContent = 'Revisa el permiso de cámara del navegador o usa el ingreso manual.';
    } else {
      vacioTexto.textContent = 'Cámara apagada';
      vacioPista.textContent = 'Toca "Encender cámara" para escanear';
    }
  }

  mira = document.createElement('div');
  mira.className = 'escaner-mira';

  /**
   * Que formatos puede leer ESTE navegador en ESTE equipo.
   *
   * Una etiqueta que no se lee casi siempre es una de dos cosas: el formato no
   * está entre los que el navegador decodifica (cada navegador y cada sistema
   * tienen una lista distinta), o el código está demasiado lejos o desenfocado.
   * Sin está línea, las dos se ven igual: nada pasa. Con ella, el operador ve
   * si su Code 128 está en la lista sin tener que adivinar.
   */
  const lineaFormatos = crear('p', { clase: 'escaner-formatos' });
  lineaFormatos.hidden = true;

  /** Nombres legibles de los formatos, para que no parezca un error de sistema. */
  const NOMBRES_FORMATO = {
    qr_code: 'QR',
    code_128: 'Code 128',
    code_39: 'Code 39',
    code_93: 'Code 93',
    ean_13: 'EAN-13',
    ean_8: 'EAN-8',
    upc_a: 'UPC-A',
    upc_e: 'UPC-E',
    itf: 'ITF',
    data_matrix: 'Data Matrix',
    aztec: 'Aztec',
    pdf417: 'PDF417',
  };

  function mostrarFormatos() {
    const nombres = lector.formatos.map((f) => NOMBRES_FORMATO[f] ?? f);
    lineaFormatos.textContent = `Lee: ${nombres.join(' · ')}`;
    lineaFormatos.hidden = nombres.length === 0;
  }

  const marcoVideo = crear('div', { clase: 'escaner-marco' });
  marcoVideo.append(zonaVacia, mira, lineaFormatos);

  // La leyenda de encuadre: dice QUE se tiene que encuadrar y COMO. Es la
  // diferencia entre un código de barras que se lee y uno que no.
  const leyendaGuia = crear('p', {
    clase: 'escaner-guia',
    texto: 'QR: encuadra la etiqueta dentro del cuadro. Código de barras: gira el celular en horizontal y llena la franja.',
  });

  // Botón cámara: toggle on/off - integrado en barra de controles
  const botonCamara = crear('button', {
    clase: 'boton boton--camara-toggle',
    atributos: {
      type: 'button',
      'aria-label': 'Alternar cámara del escaner',
      'aria-pressed': 'false',
      title: 'Encender / apagar cámara (Ctrl+Shift+C)',
    },
    hijos: [
      icono(ICONOS.camara, { tamano: 22 }),
      crear('span', { clase: 'boton__texto', texto: 'Encender cámara' }),
    ],
  });
  conTooltip(
    botonCamara,
    'Enciende o apaga la cámara. La cámara se queda encendida entre escaneos: podés pasar el celu sobre las etiquetas sin tocar la pantalla.',
  );

  // Toggle: modo video visible (debug) vs modo mira (producción)
  const botonToggleVideo = crear('button', {
    clase: 'boton boton--toggle-modo',
    atributos: {
      type: 'button',
      'aria-label': 'Alternar video visible / mira',
      'aria-pressed': 'false',
      title: 'Ctrl+Shift+V: Video visible / mira',
    },
    hijos: [
      icono(ICONOS.camara, { tamano: 18 }),
      crear('span', { clase: 'boton__texto-corto', texto: 'Video' }),
    ],
  });
  conTooltip(botonToggleVideo, 'Video visible (debug) / Solo mira (producción). Atajo: Ctrl+Shift+V');
  botonToggleVideo.addEventListener('click', () => {
    mostrarVideo = !mostrarVideo;
    botonToggleVideo.setAttribute('aria-pressed', mostrarVideo ? 'true' : 'false');
    botonToggleVideo.querySelector('span').textContent = mostrarVideo ? 'Mira' : 'Video';
    if (lector.activo) {
      lector.elementoVideo.hidden = !mostrarVideo;
      mira.hidden = mostrarVideo;
    }
    avisar(mostrarVideo ? 'Modo video visible (debug)' : 'Modo mira (producción)', 'info', 2000);
  });

  const tarjetaCamara = crear('section', { clase: 'tarjeta tarjeta--camara', id: 'tarjeta-escanear' });
  tarjetaCamara.append(
    crear('header', {
      clase: 'tarjeta__header',
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Escanear etiqueta' }),
        crear('p', { clase: 'tarjeta__subtitulo', texto: 'Apunta el código dentro del marco. El escaneo es automático.' }),
      ],
    }),
    marcoVideo,
    leyendaGuia,
    crear('div', { 
      clase: 'escaner-toolbar', 
      role: 'toolbar',
      'aria-label': 'Controles de escaneo',
      hijos: [
        botonCamara,
        botonToggleVideo,
      ]
    })
  );

  // ----- 3. Tarjeta: Ingreso Manual (COLAPSABLE) -----
  inputCodigo = crear('input', {
    clase: 'campo__control campo__control--codigo mono',
    atributos: {
      type: 'text',
      id: 'codigo-manual',
      inputmode: 'text',
      autocomplete: 'off',
      autocapitalize: 'characters',
      autocorrect: 'off',
      spellcheck: 'false',
      placeholder: 'INV-a1b2c3d4 o SER-123',
      'aria-label': 'Código de la etiqueta (manual)',
      'aria-describedby': 'ayuda-manual',
    },
  });

  botonManual = crear('button', {
    clase: 'boton boton--bloque boton--manual',
    texto: 'Registrar',
    atributos: {
      type: 'submit',
      title: 'Registrar código manualmente (Enter)',
      'aria-label': 'Registrar código ingresado manualmente',
    },
  });

  const ayudaManual = crear('p', {
    id: 'ayuda-manual',
    clase: 'campo__ayuda',
    texto: 'Para etiquetas sucias, rotas o si la cámara no enfoca. Pulsa Enter para registrar.',
  });

  const etiquetaManual = crear('label', { clase: 'campo' });
  etiquetaManual.htmlFor = 'codigo-manual';
  const textoEtiquetaManual = crear('span', { clase: 'campo__etiqueta', texto: 'Código de la etiqueta' });
  etiquetaManual.append(textoEtiquetaManual, inputCodigo, ayudaManual);
  conTooltip(
    textoEtiquetaManual,
    'El código de la etiqueta: el QR (INV-...) o el serial del fabricante. ' +
      'También acepta el código de un producto del maestro (PRD-...).',
  );

  const formularioManual = crear('form', {
    clase: 'formulario-manual',
    atributos: {
      onsubmit: (e) => {
        e.preventDefault();
        encolar(inputCodigo.value);
      },
    },
  });
  formularioManual.append(etiquetaManual, botonManual);

  const tarjetaManual = crear('section', {
    clase: 'tarjeta tarjeta--manual',
    id: 'tarjeta-manual',
    hidden: true, // Oculta por defecto - se muestra al cambiar segmento
  });
  tarjetaManual.append(
    crear('header', {
      clase: 'tarjeta__header',
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Ingreso manual' }),
        crear('p', { clase: 'tarjeta__subtitulo', texto: 'Alternativa si la cámara no lee la etiqueta.' }),
      ],
    }),
    formularioManual
  );

  // ----- 4. Segmented Control: [Escanear] [Manual] -----
  const segmentoEscanear = crear('button', {
    clase: 'segmento segmento--activo',
    role: 'tab',
    'aria-selected': 'true',
    'aria-controls': 'tarjeta-escanear',
    id: 'seg-escanear',
    atributos: {
      type: 'button',
      'aria-label': 'Activar modo escaner con cámara',
    },
    hijos: [icono(ICONOS.camara, { tamano: 18 }), crear('span', { texto: 'Escanear' })],
  });
  conTooltip(segmentoEscanear, 'Usa la camara para leer las etiquetas. Es el modo principal.');

  const segmentoManual = crear('button', {
    clase: 'segmento',
    role: 'tab',
    'aria-selected': 'false',
    'aria-controls': 'tarjeta-manual',
    id: 'seg-manual',
    atributos: {
      type: 'button',
      'aria-label': 'Activar modo ingreso manual',
    },
    hijos: [icono(ICONOS.teclado, { tamano: 18 }), crear('span', { texto: 'Manual' })],
  });
  conTooltip(segmentoManual, 'Escribe el código a mano. Para etiquetas sucias, rotas o si la camara no enfoca.');

  const segmentedControl = crear('div', {
    clase: 'segmented-control',
    role: 'tablist',
    'aria-label': 'Modo de ingreso',
  });
  segmentedControl.append(segmentoEscanear, segmentoManual);

  // ----- 5. Área de resultado (vive) -----
  zonaResultado = crear('div', {
    id: 'zona-resultado',
    clase: 'zona-resultado',
    'aria-live': 'polite',
  });

  // Ensamblar vista principal
  raiz.className = 'vista-escanear';
  raiz.append(
    tarjetaTipo,
    tarjetaCamara,
    segmentedControl,
    tarjetaManual,
    zonaResultado
  );

  // ============================================================
  // INICIALIZACION DEL LECTOR
  // ============================================================

  // El lector, en MODO CONTINUO: la camara sigue encendida después de leer y
  // el operador sigue escaneando. Es como se trabaja en un depósito: se pasa
  // el celular sobre una caja tras otra, sin tocar la pantalla.
  //
  // Antes el lector se apagaba en cada lectura y el operador tinha que
  // apretar "Encender camara" para cada etiqueta. En un conteo de 40 equipos
  // eso son 40 toques de más, y el operador termina escaneando con el
  // ingreso manual, que es más lento.
  lector = new LectorQR(marcoVideo, { continuo: true });

  // Verificar soporte y mostrar aviso si no hay cámara nativa
  const razon = LectorQR.razonNoDisponible();
  if (razon) {
    botonCamara.disabled = true;
    botonCamara.title = razon;
    botonCamara.setAttribute('aria-label', razon);
    tarjetaCamara.append(crear('p', { clase: 'campo__ayuda', texto: razon }));
  }

  // ============================================================
  // EVENTOS Y LÓGICA
  // ============================================================

  // --- Cambió de segmento (Escanear ↔ Manual) ---
  function cambiarSegmento(nuevo) {
    if (nuevo === segmentoActivo) return;

    segmentoActivo = nuevo;
    const esEscanear = nuevo === 'escanear';

    // Actualizar UI
    segmentoEscanear.classList.toggle('segmento--activo', esEscanear);
    segmentoEscanear.setAttribute('aria-selected', esEscanear);
    segmentoManual.classList.toggle('segmento--activo', !esEscanear);
    segmentoManual.setAttribute('aria-selected', !esEscanear);

    tarjetaCamara.hidden = !esEscanear;
    tarjetaManual.hidden = esEscanear;

    // Si vamos a escanear y no estaba iniciada, intentar auto-iniciar
    if (esEscanear && !camaraIniciadaAuto && !razon) {
      // Pequeño delay para que la transición termine
      setTimeout(() => alternarCamara(true), 150);
    }

    // Si vamos a manual, enfocar input
    if (!esEscanear) {
      setTimeout(() => inputCodigo.focus({ preventScroll: true }), 150);
    }
  }

  segmentoEscanear.addEventListener('click', () => cambiarSegmento('escanear'));
  segmentoManual.addEventListener('click', () => cambiarSegmento('manual'));

  // --- Cámara: encendido y apagado ---

  /**
   * Espera a que el `<video>` tenga algo que mostrar.
   *
   * @param {HTMLVideoElement} video
   * @param {number} ms  Tope de espera.
   * @returns {Promise<void>}
   */
  function primerFotograma(video, ms = 1500) {
    if (video.videoWidth > 0 && video.readyState >= 2) return Promise.resolve();

    return new Promise((resolver) => {
      const listo = () => {
        video.removeEventListener('loadeddata', listo);
        clearTimeout(tope);
        resolver();
      };
      const tope = setTimeout(() => {
        video.removeEventListener('loadeddata', listo);
        resolver();
      }, ms);
      video.addEventListener('loadeddata', listo);
    });
  }

  /**
   * Si pasan varios segundos sin leer nada, decirlo.
   *
   * El bucle de deteccion es silencioso por diseno: un frame sin código es lo
   * normal. Pero cuando la etiqueta no se lee NADA, el operador ve una camara
   * encendida y ninguna reaccion, y no hay forma de distinguir "todavía no
   * leyo" de "esto no lo va a leer nunca". Este aviso cubre las dos, sin
   * inventar un error.
   */
  function armarAvisoDeNadaDetectado() {
    if (timerSinLectura) clearTimeout(timerSinLectura);
    timerSinLectura = setTimeout(() => {
      if (lector.activo) {
        avisar(
          'No detecto ningún código. Acerca la etiqueta hasta que ocupe un quinto del ancho del marco. ' +
            'Si es un código de barras, gira el celular en horizontal: necesita ocupar el ancho de la franja, no un cuadrado. ' +
            'También podés usar el ingreso manual.',
          'alerta',
          6000,
        );
      }
    }, 6000);
  }

  /** El botón refleja el estado REAL de la cámara, no el último conocido. */
  function pintarBotonCamara(encendida) {
    const accion = encendida ? 'Apagar' : 'Encender';
    botonCamara.replaceChildren(
      icono(encendida ? ICONOS.error : ICONOS.camara, { tamano: 20 }),
      crear('span', { texto: `${accion} cámara` })
    );
    botonCamara.setAttribute('aria-pressed', encendida ? 'true' : 'false');
    botonCamara.title = `${accion} cámara (Ctrl+Shift+C)`;
    botonCamara.setAttribute('aria-label', `${accion} cámara del escaner`);
  }

  /**
   * El callback que el lector dispara cuando lee algo.
   *
   * La camara NO se apaga: en modo continuo el operador sigue escaneando.
   * El código se ENCOLA, porque puede leerse otro mientras el primero se
   * está registrando, y descartarlo seria perder un equipo del conteo.
   */
  function alLeerCodigo(codigo, formato) {
    inputCodigo.value = codigo;
    // La guia de encuadre se adapta al formato leído.
    ajustarGuia(formato);
    // Feedback visual: mostrar código leído en el marco (modo mira)
    if (!mostrarVideo) {
      mostrarCodigoEnMarco(codigo, formato);
    }
    encolar(codigo);
  }

  /**
   * Muestra el código leído en un panel lateral al marco (modo mira).
   * En modo mira (sin video), da feedback inmediato al lado del escáner.
   */
  let timeoutCodigoEnPanel = null;
  function mostrarCodigoEnPanel(codigo, formato) {
    if (timeoutCodigoEnPanel) clearTimeout(timeoutCodigoEnPanel);
    
    // Crear o reutilizar panel lateral
    let panel = document.getElementById('panel-codigo-leido');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'panel-codigo-leido';
      panel.className = 'panel-codigo-leido';
      // Insertar al lado del marco de video
      if (marcoVideo && marcoVideo.parentNode) {
        marcoVideo.parentNode.insertBefore(panel, marcoVideo.nextSibling);
      }
    }
    
    const nombreFormato = {
      qr_code: 'QR',
      code_128: 'Code 128',
      ean_13: 'EAN-13',
      ean_8: 'EAN-8',
      upc_a: 'UPC-A',
      upc_e: 'UPC-E',
      code_39: 'Code 39',
      code_93: 'Code 93',
      itf: 'ITF',
      data_matrix: 'Data Matrix',
      pdf417: 'PDF417',
      aztec: 'Aztec',
    }[formato] || formato;
    
    panel.innerHTML = `
      <div class="panel-codigo-leido__header">
        <span class="panel-codigo-leido__etiqueta">Último leído</span>
        <span class="panel-codigo-leido__formato">${nombreFormato}</span>
      </div>
      <div class="panel-codigo-leido__codigo">${codigo}</div>
    `;
    panel.hidden = false;
    
    // Auto-ocultar después de 3 segundos
    if (timeoutCodigoEnPanel) clearTimeout(timeoutCodigoEnPanel);
    timeoutCodigoEnPanel = setTimeout(() => {
      if (panel) panel.hidden = true;
    }, 3000);
  }

  /**
   * Adapta la guia de encuadre al formato del código.
   *
   * POR QUE
   * -------
   * Un QR tiene 33 módulos y uno de los lados. Un Code 128 de 16 caracteres
   * tiene unos 130 módulos: SIEMPRE de ANCHO. Para que un lector lo vea, el
   * código tiene que ocupar unos 400 pixeles de ancho en el cuadro de la
   * camara (medido: a 300 px deja de leerse).
   *
   * O sea: el operador tiene que acercarse CUATRO VECES más que para un QR. Y
   * la guia cuadrada de la app no puede encuadrar jamas un código de barras:
   * lo está encuadrando en una franja que no entra.
   *
   * Medido en este repo, con el Code 128 que genera la app:
   *   900 px -> se lee    600 px -> se lee    400 px -> se lee
   *   300 px -> NO se lee  240 px -> NO se lee
   *
   * @param {string} formato  'qr_code', 'code_128', 'ean_13'...
   */
  function ajustarGuia(formato) {
    const esLineal = Boolean(formato) && !formato.startsWith('qr') && !formato.startsWith('data_matrix') && !formato.startsWith('aztec') && !formato.startsWith('pdf417');
    marcoVideo.classList.toggle('escaner-marco--lineal', esLineal);
    if (leyendaGuia) {
      leyendaGuia.textContent = esLineal
        ? 'Código de barras: gira el celular en horizontal y llena la franja con la etiqueta.'
        : 'QR: encuadra la etiqueta dentro del cuadro.';
    }
  }

  /**
   * La cola de escaneos.
   *
   * POR QUE UNA COLA Y NO UN `if (envioEnCurso) return`
   * -----------------------------------------------
   * El operador pasa el celu sobre tres cajas en un segundo. Sin cola, los
   * dos últimos se pierden en silencio: el conteo queda corto y nadie sabe
   * por que. Con cola, se registran en orden.
   *
   * El tope de 20 es una red: si algo se rompe y la cola no baja, no se
   * acumula hasta que el celular se queda sin memoria.
   */
  const cola = [];
  let procesando = false;

  function encolar(codigo) {
    cola.push(codigo);
    if (cola.length > 20) cola.shift();
    procesarCola();
  }

  async function procesarCola() {
    if (procesando) return;
    procesando = true;
    try {
      while (cola.length) {
        const codigo = cola.shift();
        await procesar(codigo);
      }
    } finally {
      procesando = false;
    }
  }

  async function alternarCamara(forzarEncendido = false) {
    const quiereEncender = forzarEncendido || !lector.activo;

    if (!quiereEncender) {
      apagarCamara();
      return;
    }

    if (lector.activo) return; // Ya esta encendida

    botonCamara.disabled = true;
    zonaVacia.hidden = false;
    pintarVacio('encendiendo');
    try {
      await lector.iniciar(alLeerCodigo);
      // `play()` resuelve cuando la reproduccion arranca, no cuando hay imagen:
      // sin este espera el marco pasa de "encendiendo" a un rectangulo negro
      // durante un par de cuadros. El timeout evita que una camara que nunca
      // entrega fotogramas deje el girador girando para siempre.
      await primerFotograma(lector.elementoVideo);
      // Modo configurable: mostrarVideo = true (video visible tipo videollamada)
      //                          false = modo mira (solo overlay CSS)
      lector.elementoVideo.hidden = !mostrarVideo;
      zonaVacia.hidden = true;
      mira.hidden = mostrarVideo; // mira visible solo en modo mira
      mostrarFormatos();
      armarAvisoDeNadaDetectado();
      pintarBotonCamara(true);
      camaraIniciadaAuto = true;
    } catch (error) {
      // No se llama a `apagarCamara()` porque eso repintaria el marco como
      // "apagada" y perderia el motivo: el operador necesita leer por que no
      // se pudo, no ver el mismo cuadro de antes con un aviso al costado.
      lector.detener();
      lector.elementoVideo.hidden = true;
      zonaVacia.hidden = false;
      pintarVacio('error');
      pintarBotonCamara(false);
      // El aviso va para el operador; esto va para el que_programa: sin el, un
      // fallo de cámara es una pantalla con un texto y nada más.
      console.error('No se pudo encender la cámara:', error);
      avisar(error.message, 'error');
    } finally {
      botonCamara.disabled = false;
    }
  }

  /**
   * Vuelve a encender la cámara después de un código que no existe.
   *
   * En modo CONTINUO la camara nunca se apaga sola: sigue encendida entre
   * escaneos, y este método solo sirve para volver a encenderla si el
   * operador la apago (o si el navegador la cerró por un permiso).
   *
   * El lector tiene su propio filtro por código (3 segundos), así que no
   * hay que worries de que se lea la misma etiqueta en bucle mientras el
   * modal de la etiqueta de CAJA está abierto.
   */
  function rearmarCamara() {
    if (razon) return; // El navegador no puede abrir cámara: no tiene sentido.
    alternarCamara(true).catch(() => { /* ya se avisa en `alternarCamara` */ });
  }

  function apagarCamara() {
    lector.detener();
    if (timerSinLectura) {
      clearTimeout(timerSinLectura);
      timerSinLectura = null;
    }
    lector.elementoVideo.hidden = true;
    zonaVacia.hidden = false;
    pintarVacio('apagada');
    pintarBotonCamara(false);
    botonCamara.title = 'Encender cámara (Ctrl+Shift+C)';
    botonCamara.setAttribute('aria-label', 'Encender cámara del escaner');
  }

  botonCamara.onclick = () => alternarCamara();

  // --- Teclado: Ctrl+Shift+C = toggle cámara, Enter en input = procesar ---
  const atajoCamara = (e) => {
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'c') {
      e.preventDefault();
      if (segmentoActivo === 'escanear') alternarCamara();
    }
  };
  document.addEventListener('keydown', atajoCamara);

  // Enter en input manual = procesar (ya lo maneja el form submit, pero reforzamos)
  inputCodigo.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      encolar(inputCodigo.value);
    }
  });

  // --- Auto-apagar cámara al cambiar de pestaña/ventana ---
  const alOcultar = () => {
    if (document.hidden && lector.activo) apagarCamara();
  };
  document.addEventListener('visibilitychange', alOcultar);

  // --- Cargar tipos de movimiento dinamicos ---
  async function cargarTiposMovimiento() {
    try {
      const tipos = await api.obtenerTiposMovimiento();
      TIPOS_MOVIMIENTO = tipos;

      vaciar(chipsContainer);

      for (const t of tipos) {
        const chip = crear('button', {
          clase: `chip chip--movimiento ${t.valor === tipoSeleccionado ? 'chip--activo' : ''}`,
          role: 'radio',
          'aria-checked': t.valor === tipoSeleccionado,
          atributos: {
            type: 'button',
            title: t.descripcion || t.texto,
            'aria-label': `${t.texto}${t.descripcion ? ': ' + t.descripcion : ''}`,
          },
          hijos: [
            // `t.ícono` es el NOMBRE del ícono, no el path: sin el lookup se le pasa
            // 'buscar' como atributo `d` y el SVG sale con un error por consola.
            icono(ICONOS[t.icono] || ICONOS.buscar, { tamano: 16 }),
            crear('span', { texto: t.texto }),
          ],
        });

        chip.onclick = () => {
          tipoSeleccionado = t.valor;
          for (const c of chipsContainer.querySelectorAll('.chip')) {
            c.classList.toggle('chip--activo', c === chip);
            c.setAttribute('aria-checked', c === chip);
          }
        };

        chipsContainer.append(chip);
      }

      // Si no hay tipos, fallback a hardcodeados
      if (tipos.length === 0) throw new Error('Sin tipos');
    } catch {
      // Fallback: tipos hardcodeados usando constantes de dom.js
      const fallback = [
        { valor: 'OUT_TO_CLIENT', texto: 'Salida a cliente', icono: 'buscar', descripcion: 'El equipo sale del depósito hacia el cliente' },
        { valor: 'INSTALLATION', texto: 'Instalacion', icono: 'ubicacion', descripcion: 'El equipo se instala en sitio del cliente' },
        { valor: 'WAREHOUSE_REENTRY', texto: 'Reingreso a depósito', icono: 'refrescar', descripcion: 'El equipo vuelve al depósito desde cliente' },
        { valor: 'WITHDRAWAL_TO_WORKSHOP', texto: 'Retiro a taller', icono: 'relojito', descripcion: 'El equipo va a reparación' },
        { valor: 'REPLACEMENT', texto: 'Reemplazo', icono: 'refrescar', descripcion: 'Se reemplaza un equipo por otro' },
        { valor: 'WAREHOUSE_ENTRY', texto: 'Entrada a depósito', icono: 'buscar', descripcion: 'Nuevo equipo ingresa al depósito' },
        { valor: 'DISPOSAL', texto: 'Baja definitiva', icono: 'error', descripcion: 'El equipo se da de baja permanentemente' },
      ];

      vaciar(chipsContainer);
      for (const t of fallback) {
        const chip = crear('button', {
          clase: `chip chip--movimiento ${t.valor === tipoSeleccionado ? 'chip--activo' : ''}`,
          role: 'radio',
          'aria-checked': t.valor === tipoSeleccionado,
          atributos: {
            type: 'button',
            title: t.descripcion,
            'aria-label': `${t.texto}: ${t.descripcion}`,
          },
          hijos: [icono(ICONOS[t.icono] || ICONOS.buscar, { tamano: 16 }), crear('span', { texto: t.texto })],
        });
        chip.onclick = () => {
          tipoSeleccionado = t.valor;
          for (const c of chipsContainer.querySelectorAll('.chip')) {
            c.classList.toggle('chip--activo', c === chip);
            c.setAttribute('aria-checked', c === chip);
          }
        };
        chipsContainer.append(chip);
      }
    }
  }

  // --- Procesamiento del escaneo (manual o cámara) ---
  async function procesar(codigoCrudo) {
    if (envioEnCurso) return;

    const codigo = normalizarCodigo(codigoCrudo);
    if (!codigo) {
      avisar('El código viene vacío.', 'alerta');
      inputCodigo.focus({ preventScroll: true });
      return;
    }

    envioEnCurso = true;
    botonManual.disabled = true;
    // El indicador va en la tarjeta de la camara, NO en la zona de resultado:
    // reemplazar el resultado borra el recibo de la sesión.
    indicarRegistro(true);

    // La baja definitiva no tiene vuelta atras: el equipo sale del inventario.
    // Pide confirmacion. El resto de los movimientos NO la pide —el escaneo
    // es un flujo rápido, con una mano ocupada y a veces con guantes, y un
    // cartel en cada lectura lo volveria inservible—.
    if (tipoSeleccionado === 'DISPOSAL') {
      const ok = await confirmar({
        titulo: 'Confirmar baja definitiva',
        mensaje: `¿Dar de baja el equipo con el codigo ${codigo}? Deja de estar disponible en el inventario.`,
        textoConfirmar: 'Dar de baja',
        peligro: true,
      });
      if (!ok) {
        envioEnCurso = false;
        botonManual.disabled = false;
        botonCamara.disabled = false;
        zonaResultado.replaceChildren();
        inputCodigo.focus({ preventScroll: true });
        return;
      }
    }

    // El UUID se genera UNA vez por intento de escaneo.
    // Si la petición falla por red y el operador reintenta con el mismo código,
    // se reutiliza este y el backend lo reconoce como el mismo envió.
    if (!scanIdPendiente) scanIdPendiente = nuevoScanId();

    try {
      const posicion = await obtenerPosicion();

      const resultado = await api.escanear({
        codigo,
        tipo_movimiento: tipoSeleccionado,
        client_scan_id: scanIdPendiente,
        latitude: posicion.latitude,
        longitude: posicion.longitude,
      });

      // Solo se descarta el UUID cuando el backend confirmo.
      // Si respondió con `replay: true`, también es éxito: el envió ya estaba registrado.
      scanIdPendiente = null;
      inputCodigo.value = '';
      pintarResultado(resultado, codigo);
    } catch (error) {
      // El UUID se CONSERVA para el reintento, salvo que el error sea de
      // validación: un 4xx significa que la petición está mal y reenviarla
      // idéntica va a fallar igual. En ese caso se descarta para que el
      // operador pueda corregir y mandar una petición nueva.
      if (error.status >= 400 && error.status < 500) scanIdPendiente = null;

      // Un código que NO existe en el inventario.
      //
      // Un 404 NO es un fallo del sistema: es una etiqueta que no está dada de
      // alta. Es el caso más comun en un depósito —el operador escanea un equipo
      // de otra empresa, o uno dado de baja sin certificado nuevo— y con un
      // aviso que se va solo a los 4 segundos el operador lo pierde de vista
      // mientras sigue apuntando la cámara. Va en modal, que no se va solo, y
      // con el motivo del backend tal cual: el texto está escrito para el
      // operador, no para un developers.
      if (error.status === 404) {
        // En modo continuo, un modal por cada etiqueta desconocida CORTARIA el
        // escaneo: el operador no podria seguir pasando el celu porque la
        // pantalla de aviso está encima. El error va al recibo, que se lee
        // igual, y un aviso flotante lo anuncia.
        agregarErrorAlRecibo(codigo, error.message);
        avisar(error.message, 'error', 5000);
        return;
      }

      // La etiqueta de la CAJA: el código existe, pero es la de un PRODUCTO del
      // maestro y no la de un equipo. El backend responde 409 con el producto
      // y sus unidades; acá se las muestra al operador para que elija con
      // cual registrar el movimiento.
      const producto = productoDelError(error);
      if (producto) {
        mostrarCodigoDeProducto(producto, codigo, {
          alRegistrar: (unidad) => registrarUnidad(unidad),
          alCerrar: rearmarCamara,
        });
        return;
      }

      // El error va al recibo: es parte del conteo, no un cartel que se va.
      agregarErrorAlRecibo(codigo, error.message);
      avisar(error.message, 'error', 5000);
    } finally {
      envioEnCurso = false;
      botonManual.disabled = false;
      indicarRegistro();
    }
  }

  /**
   * Enciende o apaga el chispoteo de "registrando".
   *
   * Va en la tarjeta de la camara y no en la zona de resultado: el recibo de
   * escaneos tiene que seguir ahí mientras se registra el siguiente.
   */
  function indicarRegistro() {
    if (!indicadorRegistro) {
      indicadorRegistro = crear('p', { clase: 'escaner-registrando', atributos: { role: 'status' } });
      indicadorRegistro.hidden = true;
      tarjetaCamara.appendChild(indicadorRegistro);
    }
    indicadorRegistro.hidden = false;
    indicadorRegistro.textContent = 'Registro scaneado';
    setTimeout(() => { indicadorRegistro.hidden = true; }, 1500);
  }

  /**
 * Código que no existe en el inventario.
 *
 * @param {string}   motivo    Lo que respondió el servidor.
 * @param {string}   código    El código escaneado, para que el operador lo lea.
 * @param {Function} [alCerrar] Se dispara al cerrar, con y sin Escape.
 */
/**
 * Saca el producto de un error 409 del backend.
 *
 * El backend responde con `detail: { mensaje, producto }`: el `detalle` es un
 * objeto, no un string, y es lo único en la API que devuelve algo así. Por eso
 * se busca por `detalle.producto` y no por el texto: un 409 de transición
 * inválida (que si es un string) tiene que seguir siendo un error comun.
 *
 * @param {Error} error
 * @returns {object|null} El producto, o null si el error no es de este tipo.
 */
function productoDelError(error) {
  const detalle = error?.detalle?.detail ?? error?.detalle ?? null;
  return detalle?.producto ?? null;
}

/**
 * La etiqueta leida es la de un PRODUCTO del maestro, no la de un equipo.
 *
 * QUE SE MUESTRA
 * --------------
 * Los datos del producto y la lista de sus unidades, cada una con su serial,
 * su estado y donde está, y un botón para registrar el movimiento sobre esa.
 *
 * POR QUE NO ES UN ERROR
 * ----------------------
 * El operador leyo bien: el código existe. Lo que pasa es que la etiqueta es
 * de la CAJA, y un movimiento se registra sobre un EQUIPO. Decirle "error"
 * lo haria buscar un problema donde no lo hay.
 *
 * @param {object}   producto
 * @param {string}   código
 * @param {object}   opciones
 * @param {Function} opciones.alRegistrar  Recibe la unidad elegida.
 * @param {Function} [opciones.alCerrar]   Se dispara al cerrar.
 */
function mostrarCodigoDeProducto(producto, codigo, { alRegistrar, alCerrar }) {
  const unidades = producto.unidades || [];

  // El botón de cada unidad se arma ANTES de que el dialogo exista, así que
  // todavía no hay `cerrar`: se guarda acá y se completa en `alAbrir`.
  let cerrarModal = () => {};

  const lista = crear('ul', { clase: 'lista lista--unidades' });

  for (const unidad of unidades) {
    const estadoInfo = ESTADOS[unidad.current_status] ?? { texto: unidad.current_status, clase: 'insignia--baja' };

    const item = crear('li', { clase: 'lista__item' });

    const cuerpo = crear('div', { clase: 'lista__cuerpo' });
    cuerpo.append(
      crear('div', { clase: 'lista__titulo mono', texto: unidad.serial_number }),
      crear('div', {
        clase: 'lista__meta',
        texto: [estadoInfo.texto, unidad.ubicacion_nombre].filter(Boolean).join(' · '),
      }),
    );

    const btnRegistrar = crear('button', {
      clase: 'boton boton--chico',
      texto: 'Registrar',
      atributos: { type: 'button' },
    });

    btnRegistrar.addEventListener('click', async () => {
      btnRegistrar.disabled = true;
      btnRegistrar.textContent = 'Registrando…';
      try {
        await alRegistrar(unidad);
        cerrarModal();
      } catch {
        // El error ya se aviso con `avisar()`. El botón vuelve a estar usable:
        // el operador va a querer intentar con otra unidad.
        btnRegistrar.disabled = false;
        btnRegistrar.textContent = 'Registrar';
      }
    });

    item.append(cuerpo, btnRegistrar);
    lista.append(item);
  }

  const cuerpoModal = crear('div', { clase: 'producto-resuelto' });
  cuerpoModal.append(
    crear('div', {
      clase: 'producto-resuelto__datos',
      texto: `${producto.brand || ''} ${producto.model || ''} · SKU ${producto.sku || ''}`.trim(),
    }),
  );

  if (unidades.length) {
    cuerpoModal.append(lista);
  } else {
    cuerpoModal.append(
      crear('p', {
        clase: 'campo__ayuda',
        texto: 'Este producto no tiene ninguna unidad dada de alta todavía.',
      }),
    );
  }

  const boton = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Entendido',
    atributos: { type: 'button' },
  });

  abrirDialogo({
    titulo: 'Está etiqueta es de un producto, no de un equipo',
    mensaje:
      `El código ${codigo} es la etiqueta del producto ${producto.brand || ''} ${producto.model || ''}` +
      ` (SKU ${producto.sku || ''}). Elegi la unidad a la que corresponde el movimiento.`,
    cuerpo: [cuerpoModal],
    nota: 'Para mover varias unidades de una vez usa la vista Movimientos, que mueve por producto y cantidad.',
    clase: 'modal--alerta',
    acciones: [boton],
    alAbrir: (_modal, cerrar) => {
      cerrarModal = cerrar;
      boton.addEventListener('click', () => {
        cerrar();
        alCerrar?.();
      });
      _modal.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') alCerrar?.();
      });
    },
  });
}

// --- Pintar resultado exitoso ---
  function pintarResultado(resultado, codigo) {
    const geo = resultado.geo ?? {};
    const dispositivo = resultado.dispositivo ?? {};

    const estadoInfo = ESTADOS[dispositivo.current_status] ?? {
      texto: 'Estado desconocido',
      clase: 'insignia--baja',
    };

    const clase = geo.dentro_del_radio === false ? 'resultado--alerta' : 'resultado--ok';

    const bloque = crear('div', { clase: `resultado ${clase}` });

    const titulo = crear('div', { clase: 'resultado__titulo' });
    titulo.append(
      icono(resultado.replay ? ICONOS.refrescar : ICONOS.ok, { tamano: 18 }),
      crear('span', {
        texto: resultado.replay ? 'Ya estaba registrado' : 'Escaneo registrado',
      }),
    );

    const def = crear('dl', { clase: 'def' });
    const fila = (clave, valor, opciones = {}) => {
      const dd = crear('dd', { clase: opciones.clase ?? '' });
      if (opciones.insignia) {
        dd.append(crear('span', { clase: `insignia ${opciones.insignia}`, texto: valor }));
      } else {
        // textContent: el serial viene de la base y el código de una etiqueta
        // física. Ninguno de los dos puede interpretarse como HTML.
        dd.textContent = valor ?? '—';
      }
      def.append(crear('dt', { texto: clave }), dd);
    };

    fila('Código', codigo, { clase: 'mono' });
    fila('Serial', dispositivo.serial_number, { clase: 'mono' });
    fila('Equipo', [dispositivo.marca, dispositivo.modelo].filter(Boolean).join(' ') || null);
    fila('Ubicacion', dispositivo.ubicacion_nombre);
    fila('Estado', estadoInfo.texto, { insignia: estadoInfo.clase });

    // Alerta geo si fuera de radio
    if (geo.dentro_del_radio === false) {
      fila('⚠️ Ubicación', `Fuera de radio (${geo.distancia_metros?.toFixed(0) ?? '?'} m)`, { clase: 'mono' });
    }

    bloque.append(
      titulo,
      crear('p', { clase: 'resultado__mensaje', texto: resultado.mensaje }),
      def,
    );

    if (dispositivo.id) {
      bloque.append(
        crear('button', {
          clase: 'boton boton--suave boton--bloque',
          texto: 'Ver la ficha del componente',
          atributos: {
            type: 'button',
            title: 'Abrir ficha completa del dispositivo',
            'aria-label': 'Abrir ficha completa del dispositivo',
            onclick: () => alAbrirDispositivo(dispositivo.id),
          },
        }),
      );
    }

    // El resultado se SUMA al recibo de la sesión, no reemplaza el anterior.
    //
    // ANTES
    // ----
    // El panel se reemplazaba en cada escaneo y se borraba solo a los 8
    // segundos. En un conteo de 40 equipos, el operador no tenia forma de
    // saber cuantos habia escaneado ni revisar los anteriores: el último
    // escaneo tapaba al anterior, y a los 8 segundos no quedaba nada. Al
    // final del conteo no habia ningún comprobante de lo que se hizo.
    //
    // AHORA
    // ----
    // El recibo acumula: cada escaneo queda con su hora, su código y su
    // estado. Es el comprobante del turno, y el operador puede revisarlo
    // mientras sigue escaneando.
    agregarAlRecibo(bloque);
  }

  /**
   * Exporta el recibo de escaneos a CSV.
   *
   * El CSV se arma en el navegador: el recibo es de Está sesión y todavía no
   * está en el servidor. El separador es `;` y lleva BOM UTF-8, igual que los
   * exportes del backend, para que se abra bien en Excel en espanol.
   */
  function exportarRecibo() {
    if (!itemsRecibo.length) {
      avisar('Todavía no hay escaneos para exportar.', 'alerta');
      return;
    }

    const filas = [['Hora', 'Codigo', 'Serial', 'Equipo', 'Ubicacion', 'Estado', 'Resultado']];

    // Del más viejo al más nuevo: el CSV se lee en orden cronologico.
    // Se recorren TODOS los escaneos, no solo la página que se ve.
    for (const item of [...itemsRecibo].reverse()) {
      const hora = item.querySelector('.escaneo__hora')?.textContent ?? '';

      // Los pares etiqueta/valor de la ficha del equipo.
      const pares = {};
      const definiciones = item.querySelectorAll('dt');
      const valores = item.querySelectorAll('dd');
      definiciones.forEach((dt, i) => {
        pares[dt.textContent?.trim() ?? ''] = valores[i]?.textContent?.trim() ?? '';
      });

      const codigo = pares['Código'] || item.querySelector('.resultado__mensaje.mono')?.textContent?.trim() || '';
      const serial = pares['Serial'] || '';
      const equipo = pares['Equipo'] || '';
      const ubicacion = pares['Ubicacion'] || '';
      const estado = pares['Estado'] || '';
      const resultado = item.querySelector('.resultado__mensaje:not(.mono)')?.textContent?.trim() || '';

      filas.push([hora, codigo, serial, equipo, ubicacion, estado, resultado]);
    }

    const csv = '\ufeff' + filas.map((f) => f.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(';')).join('\r\n');
    descargarCsv('', 'escaneos.csv', csv);
  }

  /**
   * Agrega un ESCANEO FALLIDO al recibo de la sesión.
   *
   * @param {string} código
   * @param {string} mensaje  Lo que respondió el backend.
   */
  function agregarErrorAlRecibo(codigo, mensaje) {
    const bloque = crear('div', { clase: 'resultado resultado--error' });
    const titulo = crear('div', { clase: 'resultado__titulo' });
    titulo.append(
      icono(ICONOS.error, { tamano: 18 }),
      crear('span', { texto: 'No se pudo registrar' }),
    );
    bloque.append(
      titulo,
      crear('p', { clase: 'resultado__mensaje', texto: mensaje || 'Código desconocido.' }),
      crear('p', { clase: 'resultado__mensaje mono pequeno tenue', texto: codigo }),
    );
    agregarAlRecibo(bloque);
  }

  /**
   * Agrega un resultado al recibo de la sesión.
   *
   * @param {HTMLElement} bloque  El resultado a agregar.
   */
  function agregarAlRecibo(bloque) {
    if (!listaRecibo) {
      // La primera vez se arma la cabecera y la lista.
      listaRecibo = crear('ul', { clase: 'lista lista--escaneos' });

      const encabezadoRecibo = crear('div', { clase: 'flex-entre' });
      encabezadoRecibo.append(
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Escaneos de la sesión' }),
      );

      const btnLimpiar = crear('button', {
        clase: 'boton boton--suave boton--chico',
        texto: 'Limpiar',
        atributos: { type: 'button' },
      });
      btnLimpiar.addEventListener('click', () => {
        itemsRecibo = [];
        paginaRecibo = 0;
        listaRecibo?.replaceChildren();
        zonaResultado.replaceChildren();
        listaRecibo = null;
        paginacionRecibo = null;
      });
      conTooltip(btnLimpiar, 'Borra el recibo de escaneos de está sesión.');

      const btnExportar = crear('button', {
        clase: 'boton boton--suave boton--chico',
        texto: 'Exportar CSV',
        atributos: { type: 'button' },
      });
      btnExportar.addEventListener('click', () => exportarRecibo());
      conTooltip(
        btnExportar,
        'Descarga el recibo en CSV: la hora, el código y el estado de cada escaneo. ' +
          'Es el comprobante del conteo.',
      );

      encabezadoRecibo.append(btnExportar, btnLimpiar);

      const zonaPaginacion = crear('div', { estilo: 'margin-top:0.5rem' });
      paginacionRecibo = crearPaginacion({
        total: 0,
        limite: porPaginaRecibo,
        pagina: paginaRecibo,
        etiqueta: 'escaneos',
        alCambiarPagina: (nueva) => {
          paginaRecibo = nueva;
          pintarRecibo();
        },
        alCambiarLimite: (nuevo) => {
          porPaginaRecibo = nuevo;
          paginaRecibo = 0;
          pintarRecibo();
        },
      });
      zonaPaginacion.append(paginacionRecibo.elemento);

      zonaResultado.replaceChildren(encabezadoRecibo, listaRecibo, zonaPaginacion);
    }

    // El más nuevo arriba: es el que el operador está mirando.
    const item = crear('li', { clase: 'lista__item lista__item--escaneo' });
    const hora = new Date().toLocaleTimeString('es-AR', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    item.append(crear('span', { clase: 'escaneo__hora tabular', texto: hora }));
    item.appendChild(bloque);

    // El más nuevo arriba: es el que el operador está mirando.
    itemsRecibo.unshift(item);
    pintarRecibo();
  }

  /**
   * Pinta la pagina del recibo.
   *
   * La lista y el array son dos cosas: el array tiene TODOS los escaneos de
   * la sesion (es lo que se exporta) y la lista muestra solo la pagina
   * (es lo que se ve).
   */
  function pintarRecibo() {
    if (!listaRecibo) return;

    const desde = paginaRecibo * porPaginaRecibo;
    const hasta = Math.min(itemsRecibo.length, desde + porPaginaRecibo);
    listaRecibo.replaceChildren(...itemsRecibo.slice(desde, hasta));

    paginacionRecibo?.actualizar({
      total: itemsRecibo.length,
      limite: porPaginaRecibo,
      pagina: paginaRecibo,
    });
  }

  /**
   * Registra el movimiento sobre UNA unidad elegida por el operador.
   *
   * La etiqueta de la caja no dice que equipo es: el operador elige el
   * serial de la lista y se manda el escaneo de ESE equipo, con su propio
   * `qr_code`. Es el mismo camino que el de una etiqueta de equipo leida con
   * la camara, con un paso más: la eleccion.
   *
   * @param {{qr_code: string, serial_number: string}} unidad
   */
  async function registrarUnidad(unidad) {
    const codigo = unidad.qr_code || unidad.serial_number;

    try {
      const posicion = await obtenerPosicion();

      const resultado = await api.escanear({
        codigo,
        tipo_movimiento: tipoSeleccionado,
        // Un UUID NUEVO: este envió es un escaneo distinto del que fallo, y
        // el anterior ya se consumio con un `client_scan_id` que no registro
        // nada. Reusarlo haria que el backend lo tomara por un reintento.
        client_scan_id: nuevoScanId(),
        latitude: posicion.latitude,
        longitude: posicion.longitude,
      });

      scanIdPendiente = null;
      inputCodigo.value = '';
      pintarResultado(resultado, codigo);
    } catch (error) {
      avisar(error.message, 'error');
      throw error;
    }
  }

  // ============================================================
  // CICLO DE VIDA
  // ============================================================

  // Cargar tipos al montar
  cargarTiposMovimiento();

  // Auto-iniciar cámara al montar (si hay permiso y está en segmento escanear)
  // Usamos setTimeout para que el paint termine primero
  setTimeout(() => {
    if (segmentoActivo === 'escanear' && !razon && !camaraIniciadaAuto) {
      alternarCamara(true).catch(() => { /* silencioso */ });
    }
  }, 300);

  // Limpieza al destruir la vista
  const destruir = () => {
    document.removeEventListener('keydown', atajoCamara);
    document.removeEventListener('visibilitychange', alOcultar);
    // El timer vive fuera del DOM: sin esto sigue corriendo y avisa "no se
    // detecto nada" en una vista que ya no está en pantalla.
    if (timerSinLectura) clearTimeout(timerSinLectura);
    if (lector.activo) apagarCamara();
  };

  // Contrato con `mostrarVista`: `{ elemento, destruir? }`. Devolver `raíz` en vez
  // de `elemento` deja a `vistaActual.elemento` en `undefined`, el fallback
  // `?? vistaActual` mete el objeto entero en el DOM y la pantalla muestra
  // literalmente "[object Object]" en lugar de la vista.
  return { elemento: raiz, destruir };
}

// ============================================================
// HELPERS GLOBALES
// ============================================================

function nuevoScanId() {
  if (crypto?.randomUUID) return crypto.randomUUID();

  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function obtenerPosicion() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve({});
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (posicion) =>
        resolve({
          latitude: posicion.coords.latitude,
          longitude: posicion.coords.longitude,
        }),
      () => resolve({}),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 },
    );
  });
}

function vaciar(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}