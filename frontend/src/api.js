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
 * eso es la diferencia entre "cerrar la app" y "la proxima persona entra con mi
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
 */

/** Base de la API. */
const BASE = new URL('/api', window.location.origin).toString();

/** Access token. Solo en memoria: si se recarga la pagina, hay que loguearse. */
let accessToken = null;

/** Se evita el bucle infinito de refresh si el /refresh tambien falla. */
let renovando = false;

/**
 * Se llama cuando la sesion no se puede recuperar, para volver al login.
 * Lo inyecta `main.js` para que este modulo no sepa de vistas.
 */
let alPerderSesion = () => {};

/** Configuracion desde el modulo principal. */
export function configurar({ alExpirar } = {}) {
  if (typeof alExpirar === 'function') alPerderSesion = alExpirar;
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

async function leerRefreshToken() {
  try {
    return sessionStorage.getItem('si.refresh');
  } catch {
    return null;
  }
}

async function renovar() {
  if (renovando) return false;

  const refresh = await leerRefreshToken();
  if (!refresh) return false;

  renovando = true;
  try {
    const r = await fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    });

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
 */
export async function pedir(ruta, opciones = {}) {
  const {
    metodo = 'GET',
    cuerpo,
    params,
    reintentar = true,
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

  let respuesta;
  try {
    respuesta = await fetch(url, {
      method: metodo,
      headers: cabeceras,
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
      // `same-origin` y no `include`: no hace falta mandar cookies (la sesion
      // va en el Authorization) y `include` abriria la puerta a que una
      // peticion cruzara a otro origen con credenciales.
      credentials: 'same-origin',
    });
  } catch {
    throw new ErrorDeApi('No hay conexion con el servidor.', 0, null);
  }

  // 401 con un token vencido: se renueva UNA vez y se reintenta.
  if (respuesta.status === 401 && reintentar && (await renovar())) {
    return pedir(ruta, { ...opciones, reintentar: false });
  }

  if (respuesta.status === 401) {
    cerrarSesion();
    alPerderSesion();
    throw new ErrorDeApi('Tu sesion termino. Volve a iniciar sesion.', 401, null);
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

  return datos;
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
 * comia en silencio: la app Follow funcionando pero sin perfil ni permisos.
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
    }).then(async (r) => {
      const datos = await r.json().catch(() => null);
      if (!r.ok) throw new ErrorDeApi(mensajeDeError(datos, r.status), r.status, datos);
      guardarSesion(datos);
      return datos;
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

  listarDispositivos: (params) => pedir('/devices', { params }),

  obtenerDispositivo: (id, params) => pedir(`/devices/${id}`, { params }),

  historial: (id, params) => pedir(`/movements/dispositivo/${id}`, { params }),

  preguntar: (pregunta) => pedir('/ai/preguntar', { metodo: 'POST', cuerpo: { pregunta } }),

  reporte: () => pedir('/ai/reporte', { metodo: 'POST', cuerpo: {} }),

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
};
