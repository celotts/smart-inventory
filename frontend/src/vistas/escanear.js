/**
 * Vista de escaneo.
 *
 * ES EL FLUJO PRINCIPAL DE LA APP Y ESTA DISEÑADO PARA UN USUARIO CON LAS
 * MANOS OCUPADAS: en un deposito, con guantes, con una mano en la caja y la otra
 * en el telefono. De ahi salen todas las decisiones.
 *
 *  - Los controles de 44px minimos y la mira grande.
 *  - El tipo de movimiento se elige ANTES de escanear, no despues: en el
 *    momento del escaneo hay que decidir rapido, y decidir sin ver la pantalla
 *    es mucho mas lento que leerla.
 *  - Hay ingreso manual siempre disponible, porque el lector no funciona en
 *    todos los navegadores ni con todas las camaras (vidrio sucio, etiqueta
 *    rayada, permiso denegado, HTTPS no configurado).
 *
 * LA GEOLOCALIZACION ES OPCIONAL
 * -----------------------------
 * El GPS mejora el dato ("este escaneo ocurrio dentro de la sede") pero no es
 * obligatorio: en el interior de un deposito de chapa la senal es mala, y pedir
 * coordenadas como requisito deja al operador sin poder trabajar. Se pide si se
 * puede; si falla, se sigue sin ellas y el backend registra `NULL`, que es "no
 * se pudo verificar" y NO "estabas fuera".
 *
 * IDEMPOTENCIA DEL LADO DEL CLIENTE
 * ----------------------------------
 * El `client_scan_id` se genera ACA, en el dispositivo, antes del `fetch`. Si
 * la respuesta no llega y hay que reintentar, se reutiliza el mismo UUID: es lo
 * que evita que el backend registre dos movimientos por el mismo escaneo.
 */

import { api } from '../api.js';
import { crear, icono, ICONOS, ESTADOS } from '../dom.js';
import { LectorQR, normalizarCodigo } from '../escaner.js';
import { avisar } from '../avisos.js';

/**
 * Tipos que tienen sentido desde el escaner.
 *
 * `DISPOSAL` no esta a proposito: dar de baja es una accion que lleva firma y
 * certificado. Que pase por accidente mientras se camina con una caja es
 * exactamente el error que el trigger `PERMANENTLY_DISPOSED` absorbente evita
 * que se pueda deshacer despues.
 */
const TIPOS_MOVIMIENTO = [
  { valor: 'OUT_TO_CLIENT', texto: 'Sale a cliente' },
  { valor: 'INSTALLATION', texto: 'Instalo en sitio' },
  { valor: 'WAREHOUSE_REENTRY', texto: 'Vuelve a deposito' },
  { valor: 'WITHDRAWAL_TO_WORKSHOP', texto: 'Manda a taller' },
];

export function vistaEscanear({ alAbrirDispositivo }) {
  const raiz = crear('div');

  // --- Tipo de movimiento ---------------------------------------------------
  const tarjetaTipo = crear('div', { clase: 'tarjeta' });
  const listadoTipos = crear('div', { clase: 'fila-botones' });
  let tipoSeleccionado = TIPOS_MOVIMIENTO[0].valor;

  for (const tipo of TIPOS_MOVIMIENTO) {
    const boton = crear('button', {
      clase: 'boton boton--suave',
      texto: tipo.texto,
      atributos: {
        type: 'button',
        'aria-pressed': String(tipo.valor === tipoSeleccionado),
        onclick: () => {
          tipoSeleccionado = tipo.valor;
          for (const otro of listadoTipos.children) {
            otro.setAttribute('aria-pressed', String(otro.dataset.tipo === tipoSeleccionado));
          }
        },
      },
    });
    boton.dataset.tipo = tipo.valor;
    listadoTipos.append(boton);
  }

  tarjetaTipo.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Que estas haciendo' }),
    crear('p', { clase: 'tarjeta__subtitulo', texto: 'Elegilo antes de escanear.' }),
    listadoTipos,
  );

  // --- Camara ---------------------------------------------------------------
  const zonaVideo = crear('div', { clase: 'escaner-marco' });
  const lector = new LectorQR(zonaVideo);
  const video = lector.elementoVideo;

  const zonaVacia = crear('div', { clase: 'escaner-vacio' });
  zonaVacia.append(
    icono(ICONOS.camara, { tamano: 40 }),
    crear('p', { texto: 'La camara esta apagada' }),
  );
  zonaVideo.append(zonaVacia);

  // La mira: dice donde apuntar, sin tapar el codigo.
  const mira = crear('div', { clase: 'escaner-mira' });
  zonaVideo.append(mira);

  const botonCamara = crear('button', {
    clase: 'boton boton--bloque',
    atributos: { type: 'button', onclick: alternarCamara },
    hijos: [icono(ICONOS.camara, { tamano: 18 }), crear('span', { texto: 'Encender camara' })],
  });

  const tarjetaCamara = crear('div', { clase: 'tarjeta' });
  tarjetaCamara.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Escanear etiqueta' }),
    crear('p', {
      clase: 'tarjeta__subtitulo',
      texto: 'Apunta a la etiqueta, o usa el ingreso manual de abajo.',
    }),
    zonaVideo,
    crear('div', { clase: 'fila-botones', hijos: [botonCamara] }),
  );

  const razon = LectorQR.razonNoDisponible();
  if (razon) {
    botonCamara.disabled = true;
    tarjetaCamara.append(crear('p', { clase: 'campo__ayuda', texto: razon }));
  }

  // --- Ingreso manual -------------------------------------------------------
  const inputCodigo = crear('input', {
    clase: 'campo__control mono',
    atributos: {
      type: 'text',
      id: 'codigo-manual',
      autocomplete: 'off',
      autocapitalize: 'characters',
      autocorrect: 'off',
      spellcheck: 'false',
      placeholder: 'INV-a1b2c3d4 o SER-123',
    },
  });

  const botonManual = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Registrar',
    atributos: { type: 'submit' },
  });

  const formularioManual = crear('form', {
    atributos: {
      onsubmit: (evento) => {
        evento.preventDefault();
        procesar(inputCodigo.value);
      },
    },
  });

  const etiquetaManual = crear('label', { clase: 'campo' });
  etiquetaManual.htmlFor = 'codigo-manual';
  etiquetaManual.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Codigo de la etiqueta' }),
    inputCodigo,
  );

  formularioManual.append(etiquetaManual, botonManual);

  const tarjetaManual = crear('div', { clase: 'tarjeta' });
  tarjetaManual.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Ingreso manual' }),
    crear('p', {
      clase: 'tarjeta__subtitulo',
      texto: 'Para cuando la etiqueta esta sucia o el lector no funciona.',
    }),
    formularioManual,
  );

  // --- Resultado ------------------------------------------------------------
  const zonaResultado = crear('div');
  zonaResultado.setAttribute('aria-live', 'polite');

  raiz.append(tarjetaTipo, tarjetaCamara, tarjetaManual, zonaResultado);

  let envioEnCurso = false;
  let scanIdPendiente = null;

  // --- Camara: encendido y apagado ------------------------------------------
  async function alternarCamara() {
    if (lector.activo) {
      apagarCamara();
      return;
    }

    botonCamara.disabled = true;
    try {
      await lector.iniciar((codigo) => {
        inputCodigo.value = codigo;
        procesar(codigo);
      });
      video.hidden = false;
      zonaVacia.hidden = true;
      botonCamara.replaceChildren(
        icono(ICONOS.error, { tamano: 18 }),
        crear('span', { texto: 'Apagar camara' }),
      );
    } catch (error) {
      avisar(error.message, 'error');
    } finally {
      botonCamara.disabled = false;
    }
  }

  function apagarCamara() {
    lector.detener();
    video.hidden = true;
    zonaVacia.hidden = false;
    botonCamara.replaceChildren(
      icono(ICONOS.camara, { tamano: 18 }),
      crear('span', { texto: 'Encender camara' }),
    );
  }

  // Si el usuario se va a otra pestana, la camara se apaga sola. Dejarla
  // encendida en background consume bateria y es una de las cosas que hacen que
  // los usuarios odien las web apps.
  const alOcultar = () => {
    if (document.hidden && lector.activo) apagarCamara();
  };
  document.addEventListener('visibilitychange', alOcultar);

  // --- Procesamiento --------------------------------------------------------
  async function procesar(codigoCrudo) {
    if (envioEnCurso) return;

    const codigo = normalizarCodigo(codigoCrudo);
    if (!codigo) {
      avisar('El codigo viene vacio.', 'alerta');
      return;
    }

    envioEnCurso = true;
    botonManual.disabled = true;
    zonaResultado.replaceChildren(crear('p', { clase: 'tenue pequeno', texto: 'Registrando…' }));

    // El UUID se genera UNA vez por intento de escaneo. Si la peticion falla
    // por red y el operador reintenta con el mismo codigo, se reutiliza este
    // y el backend lo reconoce como el mismo envio.
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

      // Solo se descarta el UUID cuando el backend confirmo. Si respondio con
      // `replay: true`, tambien es exito: el envio ya estaba registrado.
      scanIdPendiente = null;
      inputCodigo.value = '';
      pintarResultado(resultado, codigo);
    } catch (error) {
      // El UUID se CONSERVA para el reintento, salvo que el error sea de
      // validacion: un `400` significa que la peticion esta mal y reenviarla
      // identica va a fallar igual. En ese caso se descarta para que el
      // operador pueda corregir y mandar una peticion nueva.
      if (error.status >= 400 && error.status < 500) scanIdPendiente = null;
      pintarError(error, codigo);
    } finally {
      envioEnCurso = false;
      botonManual.disabled = false;
    }
  }

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
        // `textContent`: el serial viene de la base y el codigo de una etiqueta
        // fisica. Ninguno de los dos puede interpretarse como HTML.
        dd.textContent = valor ?? '—';
      }
      def.append(crear('dt', { texto: clave }), dd);
    };

    fila('Codigo', codigo, { clase: 'mono' });
    fila('Serial', dispositivo.serial_number, { clase: 'mono' });
    fila(
      'Equipo',
      [dispositivo.marca, dispositivo.modelo].filter(Boolean).join(' ') || null,
    );
    fila('Ubicacion', dispositivo.ubicacion_nombre);
    fila('Estado', estadoInfo.texto, { insignia: estadoInfo.clase });

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
            onclick: () => alAbrirDispositivo(dispositivo.id),
          },
        }),
      );
    }

    zonaResultado.replaceChildren(bloque);
  }

  function pintarError(error, codigo) {
    const bloque = crear('div', { clase: 'resultado resultado--error' });

    const titulo = crear('div', { clase: 'resultado__titulo' });
    titulo.append(
      icono(ICONOS.error, { tamano: 18 }),
      crear('span', {
        texto: error.status === 404
          ? 'Ese codigo no esta en el inventario'
          : 'No se pudo registrar',
      }),
    );

    bloque.append(titulo, crear('p', { clase: 'resultado__mensaje', texto: error.message }));

    if (error.status === 404) {
      bloque.append(
        crear('p', {
          clase: 'resultado__mensaje pequeno tenue',
          texto:
            'Si el componente esta dado de baja, hay que emitir un certificado ' +
            'nuevo antes de volver a inventariarlo.',
        }),
      );
    }

    if (error.status === 0) {
      bloque.append(
        crear('p', {
          clase: 'resultado__mensaje pequeno tenue',
          texto: 'Revisa la conexion e intenta de nuevo con el mismo codigo.',
        }),
      );
    }

    bloque.append(crear('p', { clase: 'resultado__mensaje mono pequeno tenue', texto: codigo }));

    zonaResultado.replaceChildren(bloque);
    avisar(error.message, 'error');
  }

  /**
   * Un UUID v4 para la idempotencia.
   *
   * `crypto.randomUUID` requiere contexto seguro, que es la misma condicion que
   * ya pide la camara. El fallback usa `crypto.getRandomValues`, que existe en
   * todos los navegadores seguros.
   *
   * `Math.random` NO se usa: es predecible, y un `client_scan_id` adivinable
   * rompe la idempotencia, que es justamente la garantia que protege el conteo.
   */
  function nuevoScanId() {
    if (crypto?.randomUUID) return crypto.randomUUID();

    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  /** El GPS, con un timeout corto y sin bloquear el escaneo si falla. */
  function obtenerPosicion() {
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
        {
          enableHighAccuracy: true,
          timeout: 8000,
          // Un escaneo con 30 s de antiguedad sigue siendo valido para validar
          // que estabas en la sede: no hace falta la precision del metro.
          maximumAge: 30000,
        },
      );
    });
  }

  return raiz;
}
