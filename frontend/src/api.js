/**
 * Cliente de la API.
 *
 * DONDE VIVE EL TOKEN
 * -------------------
 * El access token vive EN MEMORIA, en una variable de este módulo, y nunca se
 * escribe en el DOM.
 *
 * La versión anterior lo ponia en el `value` de un `<input type="text">` de la
 * página. Eso era un robo de sesión trivial: cualquier XSS —y la página tenia
 * varios, via el contenido de un QR y via la respuesta de la IA— hacia
 * `document.querySelector('#token').value` y se lo mandaba. Además el input era
 * visible, aparecia en el historial del navegador al teclear con F12, y el token
 * viajaba en el `Referer` de cualquier petición a un tercero.
 *
 * Refresh token: `sessionStorage`, no `localStorage`. La diferencia es que
 * `sessionStorage` muere cuando se cierra la pestaña, y `localStorage` no
 * sobrevive ni a cerrar el navegador. En una tablet compartida en un depósito,
 * eso es la diferencia entre "cerrar la app" y "el turno siguiente entra con mi
 * sesión".
 *
 * `localStorage` tampoco sirve: es accesible desde cualquier script de la página
 * y sobrevive indefinidamente.
 *
 * RENOVACION
 * ----------
 * Un access token dura 15 minutos. Cuando la API responde 401, se intenta un
 * `refresh` una sola vez y se reintenta la petición original. Si el refresh
 * falla, se cierra la sesión: seguir reintentando con un token muerto produce
 * un bucle de 401 que deja la app inutil.
 *
 * TIMEOUT Y REINTENTOS
 * --------------------
 * - Timeout por defecto: 5 minutos (300s) para peticiones largas (IA, reportes)
 * - Timeout corto: 30s para peticiones normales
 * - Reintentos con backoff exponencial en errores 5xx y timeout
 */



import { traducir } from './i18n.js';

/** Base de la API. */
const BASE = new URL('/api', window.location.origin).toString();

/** Access token. Solo en memoria: si se recarga la página, hay que loguearse. */
let accessToken = null;

/** Se evita el bucle infinito de refresh si el /refresh también falla. */
let renovando = false;

/**
 * La renovacion EN CURSO, compartida.
 *
 * Todas las peticiones que reciben un 401 al mismo tiempo esperan a Está
 * promesa: se renueva una sola vez, con un solo refresh token. (El backend
 * rota el refresh token en cada canje, y un token reutilizado se toma como
 * un token clonado: cierra todas las sesiones del usuario.)
 *
 * @type {Promise<boolean>|null}
 */
let promesaRenovacion = null;

/**
 * NO HAY OPERACIÓN PENDIENTE
 * --------------------------
 * Antes, cuando el token se vencía en mitad de una petición, esa petición
 * se guardaba (`sessionStorage`) y se volvía a ejecutar sola después del
 * login, sin que el operador lo pidiera.
 *
 * Eso es una petición CON EFECTO que el operador no autorizó: un "Guardar
 * cambios" que se reejecuta solo puede volver a escribir, y un "Registrar
 * escaneo" que se reejecuta solo puede duplicar el movimiento. Que la app
 * "se acuerde" de lo que el operador estaba por hacer y lo haga por las
 * espaldas es peor que perder el trabajo.
 *
 * La regla ahora es una sola: una petición que no se pudo hacer, no se hace
 * después. Se avisa, se pide la clave de nuevo, y el operador repite lo que
 * quería hacer.
 */

/**
 * Se llama cuando la sesión no se puede recuperar, para volver al login.
 * Lo inyecta `main.js` para que este módulo no sepa de vistas.
 */
let alPerderSesion = () => {};

/**
 * Callback para mostrar modal de login (inyectado por main.js).
 * @type {Function|null}
 */
let alRequerirLogin = null;

/** Configuración desde el módulo principal. */
export function configurar({ alExpirar, alRequerirLogin: loginCallback } = {}) {
  if (typeof alExpirar === 'function') alPerderSesion = alExpirar;
  if (typeof loginCallback === 'function') alRequerirLogin = loginCallback;
}

export function haySesion() {
  return Boolean(accessToken);
}

/**
 * Marca que el operador CERRÓ la sesión a propósito.
 *
 * POR QUÉ EXISTE
 * --------------
 * Un 401 significa "tu sesión ya no vale". Pero después de un logout
 * explícito, un 401 no significa eso: significa que la petición se hizo
 * sin token, porque la sesión ya estaba cerrada. Son cosas distintas:
 *
 *   - 401 con sesión viva  → el token venció → hay que avisar y re-loguear.
 *   - 401 sin sesión      → el operador ya salió → NO hay que avisar nada.
 *
 * Sin esta marca, cerrar sesión y esperar medio minuto mostraba el cartel
 * de "Sesión expirada" encima de la pantalla de login: el operador leía
 * "tu sesión expiró" cuando lo que había pasado es que él la había cerrado.
 *
 * La marca se limpia sola en `guardarSesion()`: si el operador vuelve a
 * entrar, la sesión nueva vuelve a ser una sesión viva.
 */
let sesionCerradaPorElOperador = false;

export function marcarSesionCerrada() {
  sesionCerradaPorElOperador = true;
}

/**
 * ¿La sesión la cerró el operador (y no venció)?
 * @returns {boolean}
 */
export function sesionCerradaVoluntariamente() {
  return sesionCerradaPorElOperador;
}

/** Guarda la sesión que devuelve /auth/login. */
export function guardarSesion({ access_token, refresh_token }) {
  accessToken = access_token;
  // Una sesión nueva es una sesión VIVA: la marca de "cerrada a propósito"
  // se borra acá, o el próximo 401 de verdad no avisaría nada.
  sesionCerradaPorElOperador = false;
  try {
    if (refresh_token) sessionStorage.setItem('si.refresh', refresh_token);
  } catch {
    // Sin storage (modo privado en Safari, iframe sin permisos): se sigue con
    // el access token en memoria y se pierde la renovacion. Es degradar, no
    // romper, y no vale la pena avisarle al usuario.
  }
}

export function cerrarSesion() {
  accessToken = null;
  try {
    sessionStorage.removeItem('si.refresh');
    // La operación pendiente ya no existe (ver "NO HAY OPERACIÓN PENDIENTE"),
    // pero un `sessionStorage` de una versión anterior puede tenerla
    // guardada. Se borra igual: es una petición con efecto que no debe
    // quedar dando vueltas.
    sessionStorage.removeItem('si.pendingOp');
  } catch { /* sin storage: no hay nada que borrar */ }
}

/** Error de API con el status y el detalle del servidor. */
export class ErrorDeApi extends Error {
  constructor(mensaje, status, detalle) {
    super(mensaje);
    this.name = 'ErrorDeApi';
    this.status = status;
    this.detalle = detalle;
  }
}

/**
 * Fetch con timeout y reintentos automaticos.
 */
async function fetchConTimeout(url, opciones = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...opciones,
      signal: controller.signal,
    });
    return response;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new ErrorDeApi('La petición excedio el tiempo límite.', 408, null);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Petición con reintentos exponenciales para errores 5xx y timeout.
 */
async function fetchConReintentos(url, opciones = {}, reintentos = 3, timeoutMs = 30000) {
  let ultimoError;

  for (let intento = 0; intento <= reintentos; intento++) {
    try {
      return await fetchConTimeout(url, opciones, timeoutMs);
    } catch (error) {
      ultimoError = error;

      // No reintentar errores 4xx (error del cliente)
      if (error instanceof ErrorDeApi && error.status >= 400 && error.status < 500) {
        throw error;
      }

      // Último intento: lanzar error
      if (intento === 3) break;

      // Backoff exponencial: 1s, 2s, 4s...
      const espera = 1000 * 2 ** intento;
      await new Promise(r => setTimeout(r, espera));
    }

    throw ultimoError;
  }
}

async function leerRefreshToken() {
  try {
    return sessionStorage.getItem('si.refresh');
  } catch {
    return null;
  }
}

/**
 * Recupera la sesión al abrir la app (F5, recarga, volver a la pestaña).
 *
 * POR QUE NO PASA POR `pedir`
 * --------------------------
 * `pedir` manda el `Authorization` que hay en memoria, que al recargar está
 * vacío, y además su manejo de 401 dispara el modal de login y guarda la
 * operación pendiente: para restaurar eso no serve, es lo contrario.
 *
 * Y POR QUE HACE FALTA UNA Función PROPIA
 * ---------------------------------------
 * Antes esto se hacia con `api.pedir('/auth/refresh')` desde `main.js`. El
 * endpoint devolvia 200 con un token nuevo y, aún así, la app caia al login:
 * `guardarSesion()` solo se llama dentro de `renovar()`, que es el camino del
 * token vencido, no este. El access token nuevo se descartaba, `haySesion()`
 * seguia dando false y recargar la página te echaba siempre —con la sesión
 * activa y el refresh token intacto en `sessionStorage`.
 *
 * @returns {Promise<object|null>} Los datos del token, o null si no hay sesión
 *                                  que recuperar.
 */
export async function restaurarSesion() {
  const refresh = await leerRefreshToken();
  if (!refresh) return null;

  try {
    const respuesta = await fetchConTimeout(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    }, 10000);

    if (!respuesta.ok) return null;

    const datos = await respuesta.json();

    // Sin esto, el token nuevo existe pero nadie lo guarda.
    guardarSesion({
      access_token: datos.access_token,
      refresh_token: datos.refresh_token ?? refresh,
    });

    return datos;
  } catch {
    // Sin red o sin storage: es degradar, no romper. La app abre al login.
    return null;
  }
}

/**
 * Renueva el access token.
 *
 * EL PROBLEMA QUE ESTO ARREGLA
 * ----------------------------
 * Cuando el access token vence (15 minutos), la app tiene varias peticiones
 * en vuelo: el tablero, la campana, la vista que se está abriendo. Todas
 * reciben un 401 AL MISMO TIEMPO, y todas llaman a `renovar()`.
 *
 * Con el guardia viejo ("si ya hay una renovacion en curso, devuelve false"),
 * la SEGUNDA petición tomaba el `false` como "la sesión semurio" y llamaba a
 * `cerrarSesion()`: el operador quedaba afuera de la app a los 15 minutos de
 * usarla, sin haber hecho nada malo.
 *
 * Peor: el backend ROTA el refresh token en cada canje, y un refresh ya usado
 * se interpreta como un token clonado, lo que cierra TODAS las sesiones del
 * usuario. Dos renovaciones en paralelo con el mismo token eran, para el
 * backend, un robo.
 *
 * LA SOLUCION
 * -----------
 * Que todas las peticiones que necesitan renovar ESPEREN a la MISMA
 * renovacion (una promesa compartida), en vez de que unas esperen y otras
 * desistan. Se renueva una vez, con un solo token, y todas reintentan.
 *
 * @returns {Promise<boolean>} `true` si hay un access token nuevo.
 */
async function renovar() {
  // Ya hay una renovacion en curso: se ESPERA a esa, no se abandona.
  if (promesaRenovacion) return promesaRenovacion;

  // LA PROMESA SE ARMA ANTES DE CUALQUIER `await`
  //
  // Si el `await` del token estuviera antes de armar la promesa, dos
  // peticiones que llegan en el mismo tick pasarian las dos por el chequeo y
  // cada una frenaria su propia renovacion. Y con dos canjes del MISMO
  // refresh token, el backend (que lo rota y toma un token repetido como
  // un token clonado) cierra TODAS las sesiones del usuario.
  //
  // Armarla de entrada, sin `await` en el medio, hace que la segunda
  // petición encuentre la promesa y la espere.
  promesaRenovacion = (async () => {
    const refresh = await leerRefreshToken();
    if (!refresh) return false;

    try {
      const r = await fetchConTimeout(`${BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      }, 10000); // 10s timeout for refresh

      if (!r.ok) return false;

      const datos = await r.json();
      guardarSesion({
        access_token: datos.access_token,
        refresh_token: datos.refresh_token ?? refresh,
      });
      return true;
    } catch {
      // Sin red. No se pierde la sesión por un problema de conexión: el token
      // viejo puede seguir siendo válido.
      return false;
    } finally {
      renovando = false;
    }
  })();

  try {
    return await promesaRenovacion;
  } finally {
    // La promesa compartida se libera recien cuando Terminó: mientras tanto,
    // las peticiones que llegan siguen esperandola.
    promesaRenovacion = null;
  }
}

/**
 * Petición a la API.
 *
 * @param {string} ruta    Ej: '/devices/resolve'
 * @param {object} opciones
 * @param {string} [opciones.método]
 * @param {object} [opciones.cuerpo]
 * @param {object} [opciones.params]  Query string.
 * @param {boolean} [opciones.reintentar=true]  Un 401 no se reintenta solo.
 * @param {number} [opciones.timeoutMs] Timeout en ms (default 30s, 300s para IA/reportes)
 * @param {boolean} [opciones.streaming] Si es true, usa fetch sin timeout para streaming
 */
export async function pedir(ruta, opciones = {}) {
  const {
    metodo = 'GET',
    cuerpo,
    params,
    reintentar = true,
    streaming = false,
    comoTexto = false,
  } = opciones;

  const url = new URL(BASE + ruta, window.location.origin);
  if (params) {
    for (const [clave, valor] of Object.entries(params)) {
      if (valor !== undefined && valor !== null && valor !== '') {
        url.searchParams.set(clave, valor);
      }
    }
  }

  const cabeceras = {};
  if (cuerpo !== undefined) cabeceras['Content-Type'] = 'application/json';
  if (accessToken) cabeceras.Authorization = `Bearer ${accessToken}`;

  // Timeout largo para streaming (IA, reportes), normal para resto
  const timeoutMs = opciones.timeoutMs
    ?? (ruta.startsWith('/ai/') || ruta.startsWith('/api/ai/') || ruta.includes('/reporte') ? 300000 : 30000);

  let respuesta;
  try {
    const opcionesFetch = {
      method: metodo,
      headers: cabeceras,
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
      credentials: 'same-origin',
    };

    if (!streaming) {
      respuesta = await fetchConTimeout(url, opcionesFetch, timeoutMs);
    } else {
      // Para streaming (SSE), no aplicar timeout - dejar que fluya
      respuesta = await fetch(url, opcionesFetch);
    }
  } catch {
    // Sin red, o el servidor no está. En un proyecto que vive 100% en
    // Docker, el caso más comun es que el contenedor de la API no este
    // arriba: el mensaje dice eso, porque "no hay conexión" no le dice al
    // operador que hacer.
    throw new ErrorDeApi(
      'No hay conexión con el servidor. Si el proyecto corre en Docker, ' +
        'chequea que los contenedores esten arriba (`make estado`).',
      0,
      null,
    );
  }

  // 401 con un token vencido: se renueva UNA vez y se reintenta.
  if (respuesta.status === 401 && reintentar && (await renovar())) {
    return pedir(ruta, { ...opciones, reintentar: false });
  }

  if (respuesta.status === 401) {
    // El operador CERRÓ la sesión: este 401 es de una petición que se hizo
    // sin token (el polling de la campana, una vista que estaba abriendo).
    // No hay nada que avisar: la sesión ya está cerrada y el operador lo
    // sabe porque él la cerró. Avisarle "tu sesión expiró" sería
    // contradecirlo.
    if (sesionCerradaPorElOperador) {
      throw new ErrorDeApi('La sesión está cerrada.', 401, null);
    }

    // NO se guarda la petición para reejecutarla después del login: ver la
    // nota de "NO HAY OPERACIÓN PENDIENTE" más arriba.
    cerrarSesion();
    if (alRequerirLogin) {
      alRequerirLogin(); // Mostrar modal login
    } else {
      alPerderSesion(); // Fallback: redirigir a login page
    }
    throw new ErrorDeApi('Tu sesión terminó. Inicia sesión para continuar.', 401, null);
  }

  const texto = await respuesta.text();
  let datos = null;
  if (texto) {
    try {
      datos = JSON.parse(texto);
    } catch {
      datos = null;
    }
  }
  if (!respuesta.ok) {
    throw new ErrorDeApi(
      mensajeDeError(datos, respuesta.status),
      respuesta.status,
      datos,
    );
  }

  // `comoTexto`: para las respuestas que NO son JSON —el SVG del QR—. Se
  // devuelve el cuerpo crudo en vez de parsearlo. El chequeo de error de
  // arriba va antes: un error tiene que seguir siendo un error con su
  // mensaje, vine de donde vine la respuesta.
  return comoTexto ? texto : datos;
}

/**
 * Saca un mensaje útil del error de la API.
 *
 * El backend devuelve el detalle en varias formas —string, objeto con
 * `mensaje`, lista de validación de pydantic— y el frontend tiene que mostrar lo
 * que el operador puede hacer, no un JSON crudo.
 */
function mensajeDeError(datos, status) {
  const detalle = datos?.detail ?? datos?.error;

  if (typeof detalle === 'string' && detalle) return detalle;

  if (detalle && typeof detalle === 'object') {
    if (typeof detalle.mensaje === 'string') {
      // Los errores de permiso traen `faltantes` y `tu_rol`: el usuario tiene
      // que saber que le falta, no solo que no puede.
      const extra = Array.isArray(detalle.faltantes) && detalle.faltantes.length
        ? ` (te falta: ${detalle.faltantes.join(', ')})`
        : '';
      return `${traducir(detalle.mensaje)}${extra}`;
    }

    // Validación de pydantic: [{loc: [...], msg: "..."}]
    if (Array.isArray(datos?.detail)) {
      const partes = datos.detail.map((e) => {
        const campo = Array.isArray(e.loc) ? e.loc.filter((x) => x !== 'body').join('.') : '';
        return campo ? `${campo}: ${e.msg}` : e.msg;
      });
      return partes.join(' · ');
    }
  }

  if (status === 429) return traducir('Demasiadas solicitudes. Espera un momento.');
  if (status >= 500) return traducir('El servidor tiene un problema. Intenta de nuevo.');
  return `Error ${status}`;
}

/**
 * Atajos tipados de los endpoints que usa la app.
 *
 * `pedir` queda expuesto como `api.pedir` además de exportarlo suelto.
 *
 * POR QUE: `pedir` se exporta como función de módulo porque la usan `api.login`
 * y los reintentos internos, pero las vistas la necesitan para endpoints que
 * no tienen atajo. Cuando la exportacion como método faltaba, `api.pedir(...)`
 * era `undefined` y rompia con un TypeError que el `try/catch` del llamador se
 * comia en silencio: la app Follow andando con el perfil sin cargar y sin que nadie
 * se enterara.
 *
 * Un error de este tipo es invisible por construccion (un TypeError no se ve en
 * un log de red porque no hubo request), así que la regla es: si una vista
 * llama `api.algo`, `algo` tiene que existir acá.
 */
export const api = {
  pedir,
  login: (email, password) =>
    fetch(`${BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ email, password }),
    })
      .then(async (r) => {
        const datos = await r.json().catch(() => null);
        if (!r.ok) throw new ErrorDeApi(mensajeDeError(datos, r.status), r.status, datos);
        guardarSesion(datos);
        return datos;
      })
      .catch((error) => {
        // Un `fetch` que no llega al servidor tira un TypeError crudo
        // ("Failed to fetch"), que en pantalla es un mensaje que no le dice
        // nada al operador. En un proyecto que vive en Docker, casi siempre
        // significa que el contenedor de la API no está arriba.
        if (error instanceof ErrorDeApi) throw error;
        throw new ErrorDeApi(
          'No hay conexión con el servidor. Si el proyecto corre en Docker, ' +
            'chequea que los contenedores esten arriba (`make estado`).',
          0,
          null,
        );
      }),

  logout: () =>
    fetch(`${BASE}/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${accessToken}` },
      credentials: 'same-origin',
    }).catch(() => { /* si falla, la sesión local se borra igual */ }),

  resolverCodigo: (codigo) => pedir(`/devices/resolve`, { params: { codigo } }),

  escanear: (datos) => pedir('/movements/escanear', { metodo: 'POST', cuerpo: datos }),

  escanearLote: (datos) => pedir('/movements/escanear-lote', { metodo: 'POST', cuerpo: datos }),

  obtenerTiposMovimiento: () => pedir('/movements/movement-types'),

  listarDispositivos: (params) => pedir('/devices', { params }),

  obtenerDispositivo: (id, params) => pedir(`/devices/${id}`, { params }),

  // PATCH y no PUT: se manda Solo lo que el operador toco. El backend ignora
  // las claves ausentes, así que editar el serial no pisa el modelo ni la
  // ubicación con los valores de la pantalla que quedaron viejos.
  actualizarDispositivo: (id, datos) =>
    pedir(`/devices/${id}`, { metodo: 'PATCH', cuerpo: datos }),

  // DELETE en la API, baja Lógica en la base: el historial se conserva.
  darDeBajaDispositivo: (id, datos) =>
    pedir(`/devices/${id}`, { metodo: 'DELETE', cuerpo: datos }),

  historial: (id, params) => pedir(`/movements/dispositivo/${id}`, { params }),

  // El historial va en el pedido: sin el, una pregunta de seguimiento ("cual
  // es ese equipo que se encontro?") se responde de algo que no tiene nada
  // que ver.
  preguntar: (pregunta, historial = []) =>
    pedir('/ai/preguntar', {
      metodo: 'POST',
      cuerpo: { pregunta, historial },
      timeoutMs: 300000,
    }),

  // La consulta al ORQUESTADOR: elije el agente segun la pregunta y el
  // modulo en el que se esta trabajando, y devuelve quien respondio.
  //
  // El `modulo` va en el pedido y no se deduce del front: es la informacion
  // de "donde esta mirando el operador", que es lo que desempata cuando la
  // pregunta no tiene palabras clave ("y ahora que?").
  consultarAgente: (pregunta, modulo = null, historial = []) =>
    pedir('/ai/agente', {
      metodo: 'POST',
      cuerpo: { pregunta, modulo, historial },
      timeoutMs: 300000,
    }),

  agentes: () => pedir('/ai/agentes'),

  reporte: () => pedir('/ai/reporte', { metodo: 'POST', cuerpo: {}, timeoutMs: 300000 }),

  estadoIA: () => pedir('/ai/health'),

  listaAlcances: () => pedir('/ai/alcances'),

  // --- Sesión ---------------------------------------------------------------
  // `miPerfil` y `roles` devuelven lo que el SERVIDOR decide. El frontend no
  // calcula permisos: una copia de `core/permissions.py` en el cliente se
  // desincroniza en el primer cambió de permisos y produce un botón visible
  // que devuelve 403.
  miPerfil: () => pedir('/auth/yo'),
  roles: () => pedir('/users/roles'),
  logoutTodas: () => pedir('/auth/logout-all', { metodo: 'POST' }),

  // --- Catalogo (maestro de productos) -------------------------------------
  categorias: () => pedir('/catalog/categories'),
  // El maestro de productos: `{items, total}` desde la paginación del
  // backend. Los SELECTORES piden la lista entera (`límite` sin definir).
  productos: (params) => pedir('/catalog/products', { params }),
  crearProducto: (datos) => pedir('/catalog/products', { metodo: 'POST', cuerpo: datos }),
  actualizarProducto: (id, datos) => pedir(`/catalog/products/${id}`, { metodo: 'PUT', cuerpo: datos }),
  eliminarProducto: (id) => pedir(`/catalog/products/${id}`, { metodo: 'DELETE' }),
  clientes: () => pedir('/catalog/clients'),
  crearCliente: (datos) => pedir('/catalog/clients', { metodo: 'POST', cuerpo: datos }),
  actualizarCliente: (id, datos) => pedir(`/catalog/clients/${id}`, { metodo: 'PUT', cuerpo: datos }),
  eliminarCliente: (id) => pedir(`/catalog/clients/${id}`, { metodo: 'DELETE' }),
  proveedores: () => pedir('/catalog/vendors'),
  crearProveedor: (datos) => pedir('/catalog/vendors', { metodo: 'POST', cuerpo: datos }),

  // --- Stock agregado -------------------------------------------------------
  // La existencia por producto y ubicación: `{items, total}`.
  resumenStock: (params) => pedir('/stock/summary', { params }),
  stockBajo: () => pedir('/stock/low'),

  // --- Compras (ordenes y recepcion) ---------------------------------------
  ordenesCompra: (params) => pedir('/purchase-orders', { params }),
  obtenerOrden: (id) => pedir(`/purchase-orders/${id}`),
  crearOrden: (datos) => pedir('/purchase-orders', { metodo: 'POST', cuerpo: datos }),
  recibirOrden: (id, datos) => pedir(`/purchase-orders/${id}/receive`, { metodo: 'POST', cuerpo: datos }),
  sugerirReorden: () => pedir('/purchase-orders/suggest', { metodo: 'POST', cuerpo: {} }),

  // --- Ubicaciones ----------------------------------------------------------
  ubicaciones: () => pedir('/devices/locations'),

  // --- Movimientos por producto ---------------------------------------------
  // Un movimiento por cantidad: el operador elige producto y cuantas unidades,
  // y el backend elige los equipos (FIFO) y los mueve. Es la operación de
  // depósito real: nadie tiene los seriales a mano cuando llega una caja.
  moverPorProducto: (datos) =>
    pedir('/movements/por-producto', { metodo: 'POST', cuerpo: datos }),

  // --- Usuarios -------------------------------------------------------------
  // El admin da de alta, cambia la clave y desactiva. La API existe desde
  // hace rato; lo que faltaba era la pantalla.
  listarUsuarios: (params) => pedir('/users', { params }),
  crearUsuario: (datos) => pedir('/users', { metodo: 'POST', cuerpo: datos }),
  actualizarUsuario: (id, datos) => pedir(`/users/${id}`, { metodo: 'PATCH', cuerpo: datos }),
  resetearPassword: (id, password_nueva) =>
    pedir(`/users/${id}/reset-password`, { metodo: 'POST', cuerpo: { password_nueva } }),
  desactivarUsuario: (id) => pedir(`/users/${id}/desactivar`, { metodo: 'POST' }),
  restaurarUsuario: (id) => pedir(`/users/${id}/restaurar`, { metodo: 'POST' }),
  roles: () => pedir('/users/roles'),
  // Mi propia contraseña: pide la ACTUAL (si no, cualquiera con un token
  // robado se cambia la clave y echa al dueño de su cuenta).
  cambiarMiPassword: (datos) => pedir('/auth/cambiar-password', { metodo: 'POST', cuerpo: datos }),

  // --- Incidencias ----------------------------------------------------------
  // El reporte de un problema: lo ve, lo reporta y lo resuelve el taller.
  incidencias: (params) => pedir('/incidents', { params }),
  incidencia: (id) => pedir(`/incidents/${id}`),
  metricasIncidencias: () => pedir('/incidents/metrics'),
  reportarIncidencia: (datos) => pedir('/incidents', { metodo: 'POST', cuerpo: datos }),
  editarIncidencia: (id, datos) => pedir(`/incidents/${id}`, { metodo: 'PATCH', cuerpo: datos }),
  cambiarEstadoIncidencia: (id, datos) =>
    pedir(`/incidents/${id}/transitions`, { metodo: 'POST', cuerpo: datos }),
  // Tomar EL CASO: el técnico lo saca de la bandeja y queda asignado a el.
  tomarCasoIncidencia: (id) => pedir(`/incidents/${id}/claim`, { metodo: 'POST' }),
  // EL CIERRE: que se resolvio y que paso con el equipo (taller, reparado o
  // daño total). El backend se encarga de mover el equipo en el inventario.
  cerrarIncidencia: (id, datos) =>
    pedir(`/incidents/${id}/cierre`, { metodo: 'POST', cuerpo: datos }),
  comentarIncidencia: (id, nota) =>
    pedir(`/incidents/${id}/comments`, { metodo: 'POST', cuerpo: { nota } }),

  // --- Auditoría ------------------------------------------------------------
  // `auditoría` une movimientos de inventario y eventos de cuentas en una sola
  // línea de tiempo (ver el service del backend).
  auditoria: (params) => pedir('/audit/timeline', { params }),
  auditoriaResumen: () => pedir('/audit/resumen'),

  // Auditoría completa - trazabilidad CRUD con INSERT/UPDATE/DELETE
  auditoriaTrazabilidad: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return pedir('/audit/trazabilidad' + (qs ? '?' + qs : ''));
  },
};
