/**
 * Cliente de la API.
 *
 * DONDE VIVE EL TOKEN
 * -------------------
 * El access token vive EN MEMORIA, en una variable de este modulo, y nunca se
 * escribe en el DOM.
 *
 * La version anterior lo ponia en el `value` de un `<input type="text">` de la
 * pagina. Eso era un robo de sesion trivial: cualquier XSS —y la pagina tenia
 * varios, via el contenido de un QR y via la respuesta de la IA— hacia
 * `document.querySelector('#token').value` y se lo mandaba. Ademas el input era
 * visible, aparecia en el historial del navegador al teclear con F12, y el token
 * viajaba en el `Referer` de cualquier peticion a un tercero.
 *
 * Refresh token: `sessionStorage`, no `localStorage`. La diferencia es que
 * `sessionStorage` muere cuando se cierra la pestana, y `localStorage` no
 * sobrevive ni a cerrar el navegador. En una tablet compartida en un deposito,
 * eso es la diferencia entre "cerrar la app" y "el turno siguiente entra con mi
 * sesion".
 *
 * `localStorage` tampoco sirve: es accesible desde cualquier script de la pagina
 * y sobrevive indefinidamente.
 *
 * RENOVACION
 * ----------
 * Un access token dura 15 minutos. Cuando la API responde 401, se intenta un
 * `refresh` una sola vez y se reintenta la peticion original. Si el refresh
 * falla, se cierra la sesion: seguir reintentando con un token muerto produce
 * un bucle de 401 que deja la app inutil.
 *
 * TIMEOUT Y REINTENTOS
 * --------------------
 * - Timeout por defecto: 5 minutos (300s) para peticiones largas (IA, reportes)
 * - Timeout corto: 30s para peticiones normales
 * - Reintentos con backoff exponencial en errores 5xx y timeout
 */



/** Base de la API. */
const BASE = new URL('/api', window.location.origin).toString();

/** Access token. Solo en memoria: si se recarga la pagina, hay que loguearse. */
let accessToken = null;

/** Se evita el bucle infinito de refresh si el /refresh tambien falla. */
let renovando = false;

/**
 * Operación pendiente guardada cuando expira el token durante una request.
 * Permite reanudar tras login sin perder datos.
 * @type {Object|null}
 */
let operacionPendiente = null;

/**
 * Se llama cuando la sesion no se puede recuperar, para volver al login.
 * Lo inyecta `main.js` para que este modulo no sepa de vistas.
 */
let alPerderSesion = () => {};

/**
 * Callback para mostrar modal de login (inyectado por main.js).
 * @type {Function|null}
 */
let alRequerirLogin = null;

/** Configuracion desde el modulo principal. */
export function configurar({ alExpirar, alRequerirLogin: loginCallback } = {}) {
  if (typeof alExpirar === 'function') alPerderSesion = alExpirar;
  if (typeof loginCallback === 'function') alRequerirLogin = loginCallback;
}

/**
 * Guarda la operación actual para reanudar tras login.
 *
 * Se guarda EXACTAMENTE lo que `pedir` recibe: `{ ruta, ...opciones }`, con la
 * ruta relativa. Guardar la ruta ya concatenada con `BASE` obligaba a quien
 * reanuda a saber si tenía que quitarle el prefijo o no, y esa duda se
 * resolvia siempre mal: `pedir` lo vuelve a agregar y la peticion terminaba en
 * `/api/api/...`, un 404 que no dice nada de la causa real.
 *
 * @param {string} ruta       Relativa, como la recibe `pedir`. Ej: '/movements/escanear'
 * @param {object} opciones   Las mismas opciones de `pedir`.
 */
function guardarOperacionPendiente(ruta, opciones) {
  const op = { ruta, ...opciones };
  operacionPendiente = op;
  try {
    sessionStorage.setItem('si.pendingOp', JSON.stringify(op));
  } catch { /* ignore */ }
}

/**
 * Recupera y limpia la operación pendiente.
 * @returns {Object|null}
 */
export function consumirOperacionPendiente() {
  if (operacionPendiente) {
    const op = operacionPendiente;
    operacionPendiente = null;
    try { sessionStorage.removeItem('si.pendingOp'); } catch {}
    return op;
  }
  try {
    const raw = sessionStorage.getItem('si.pendingOp');
    if (raw) {
      sessionStorage.removeItem('si.pendingOp');
      return JSON.parse(raw);
    }
  } catch {}
  return null;
}

/**
 * Indica si hay una operación guardada esperando login.
 */
export function hayOperacionPendiente() {
  return !!operacionPendiente || !!sessionStorage.getItem('si.pendingOp');
}

export function haySesion() {
  return Boolean(accessToken);
}

/** Guarda la sesion que devuelve /auth/login. */
export function guardarSesion({ access_token, refresh_token }) {
  accessToken = access_token;
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
      throw new ErrorDeApi('La petición excedió el tiempo límite.', 408, null);
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
 * Y POR QUE HACE FALTA UNA FUNCIÓN PROPIA
 * ---------------------------------------
 * Antes esto se hacía con `api.pedir('/auth/refresh')` desde `main.js`. El
 * endpoint devolvía 200 con un token nuevo y, aun así, la app caía al login:
 * `guardarSesion()` solo se llama dentro de `renovar()`, que es el camino del
 * token vencido, no este. El access token nuevo se descartaba, `haySesion()`
 * seguía dando false y recargar la página te echaba siempre —con la sesión
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

async function renovar() {
  if (renovando) return false;

  const refresh = await leerRefreshToken();
  if (!refresh) return false;

  renovando = true;
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
    // Sin red. No se pierde la sesion por un problema de conexion: el token
    // viejo puede seguir siendo valido.
    return false;
  } finally {
    renovando = false;
  }
}

/**
 * Peticion a la API.
 *
 * @param {string} ruta    Ej: '/devices/resolve'
 * @param {object} opciones
 * @param {string} [opciones.metodo]
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
    // Sin red, o el servidor no esta. En un proyecto que vive 100% en
    // Docker, el caso mas comun es que el contenedor de la API no este
    // arriba: el mensaje dice eso, porque "no hay conexion" no le dice al
    // operador que hacer.
    throw new ErrorDeApi(
      'No hay conexion con el servidor. Si el proyecto corre en Docker, ' +
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
    // Guardar operación para reanudar tras login (modal, no full page)
    if (reintentar) {
      guardarOperacionPendiente(ruta, { ...opciones, reintentar: false });
    }
    cerrarSesion();
    if (alRequerirLogin) {
      alRequerirLogin(); // Mostrar modal login
    } else {
      alPerderSesion(); // Fallback: redirigir a login page
    }
    throw new ErrorDeApi('Tu sesion termino. Inicia sesion para continuar.', 401, null);
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
 * Saca un mensaje util del error de la API.
 *
 * El backend devuelve el detalle en varias formas —string, objeto con
 * `mensaje`, lista de validacion de pydantic— y el frontend tiene que mostrar lo
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
      return `${detalle.mensaje}${extra}`;
    }

    // Validacion de pydantic: [{loc: [...], msg: "..."}]
    if (Array.isArray(datos?.detail)) {
      const partes = datos.detail.map((e) => {
        const campo = Array.isArray(e.loc) ? e.loc.filter((x) => x !== 'body').join('.') : '';
        return campo ? `${campo}: ${e.msg}` : e.msg;
      });
      return partes.join(' · ');
    }
  }

  if (status === 429) return 'Demasiadas solicitudes. Espera un momento.';
  if (status >= 500) return 'El servidor tiene un problema. Intenta de nuevo.';
  return `Error ${status}`;
}

/**
 * Atajos tipados de los endpoints que usa la app.
 *
 * `pedir` queda expuesto como `api.pedir` ademas de exportarlo suelto.
 *
 * POR QUE: `pedir` se exporta como funcion de modulo porque la usan `api.login`
 * y los reintentos internos, pero las vistas la necesitan para endpoints que
 * no tienen atajo. Cuando la exportacion como metodo faltaba, `api.pedir(...)`
 * era `undefined` y rompia con un TypeError que el `try/catch` del llamador se
 * comia en silencio: la app Follow andando con el perfil sin cargar y sin que nadie
 * se enterara.
 *
 * Un error de este tipo es invisible por construccion (un TypeError no se ve en
 * un log de red porque no hubo request), asi que la regla es: si una vista
 * llama `api.algo`, `algo` tiene que existir aca.
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
        // significa que el contenedor de la API no esta arriba.
        if (error instanceof ErrorDeApi) throw error;
        throw new ErrorDeApi(
          'No hay conexion con el servidor. Si el proyecto corre en Docker, ' +
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
    }).catch(() => { /* si falla, la sesion local se borra igual */ }),

  resolverCodigo: (codigo) => pedir(`/devices/resolve`, { params: { codigo } }),

  escanear: (datos) => pedir('/movements/escanear', { metodo: 'POST', cuerpo: datos }),

  escanearLote: (datos) => pedir('/movements/escanear-lote', { metodo: 'POST', cuerpo: datos }),

  obtenerTiposMovimiento: () => pedir('/movements/movement-types'),

  listarDispositivos: (params) => pedir('/devices', { params }),

  obtenerDispositivo: (id, params) => pedir(`/devices/${id}`, { params }),

  // PATCH y no PUT: se manda SOLO lo que el operador tocó. El backend ignora
  // las claves ausentes, asi que editar el serial no pisa el modelo ni la
  // ubicacion con los valores de la pantalla que quedaron viejos.
  actualizarDispositivo: (id, datos) =>
    pedir(`/devices/${id}`, { metodo: 'PATCH', cuerpo: datos }),

  // DELETE en la API, baja LOGICA en la base: el historial se conserva.
  darDeBajaDispositivo: (id, datos) =>
    pedir(`/devices/${id}`, { metodo: 'DELETE', cuerpo: datos }),

  historial: (id, params) => pedir(`/movements/dispositivo/${id}`, { params }),

  preguntar: (pregunta) => pedir('/ai/preguntar', { metodo: 'POST', cuerpo: { pregunta }, timeoutMs: 300000 }),

  reporte: () => pedir('/ai/reporte', { metodo: 'POST', cuerpo: {}, timeoutMs: 300000 }),

  estadoIA: () => pedir('/ai/health'),

  listaAlcances: () => pedir('/ai/alcances'),

  // --- Sesion ---------------------------------------------------------------
  // `miPerfil` y `roles` devuelven lo que el SERVIDOR decide. El frontend no
  // calcula permisos: una copia de `core/permissions.py` en el cliente se
  // desincroniza en el primer cambio de permisos y produce un boton visible
  // que devuelve 403.
  miPerfil: () => pedir('/auth/yo'),
  roles: () => pedir('/users/roles'),
  logoutTodas: () => pedir('/auth/logout-all', { metodo: 'POST' }),

  // --- Catalogo (maestro de productos) -------------------------------------
  categorias: () => pedir('/catalog/categories'),
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
  // y el backend elige los equipos (FIFO) y los mueve. Es la operacion de
  // deposito real: nadie tiene los seriales a mano cuando llega una caja.
  moverPorProducto: (datos) =>
    pedir('/movements/por-producto', { metodo: 'POST', cuerpo: datos }),

  // --- Incidencias ----------------------------------------------------------
  // El reporte de un problema: lo ve, lo reporta y lo resuelve el taller.
  incidencias: (params) => pedir('/incidents', { params }),
  incidencia: (id) => pedir(`/incidents/${id}`),
  metricasIncidencias: () => pedir('/incidents/metrics'),
  reportarIncidencia: (datos) => pedir('/incidents', { metodo: 'POST', cuerpo: datos }),
  editarIncidencia: (id, datos) => pedir(`/incidents/${id}`, { metodo: 'PATCH', cuerpo: datos }),
  cambiarEstadoIncidencia: (id, datos) =>
    pedir(`/incidents/${id}/transitions`, { metodo: 'POST', cuerpo: datos }),
  comentarIncidencia: (id, nota) =>
    pedir(`/incidents/${id}/comments`, { metodo: 'POST', cuerpo: { nota } }),

  // --- Auditoria ------------------------------------------------------------
  // `auditoria` une movimientos de inventario y eventos de cuentas en una sola
  // linea de tiempo (ver el service del backend).
  auditoria: (params) => pedir('/audit/timeline', { params }),
  auditoriaResumen: () => pedir('/audit/resumen'),

  // Auditoría completa - trazabilidad CRUD con INSERT/UPDATE/DELETE
  auditoriaTrazabilidad: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return pedir('/audit/trazabilidad' + (qs ? '?' + qs : ''));
  },
};
