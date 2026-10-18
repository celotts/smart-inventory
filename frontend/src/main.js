/**
 * Punto de entrada de la aplicacion.
 *
 * QUE HACE
 * --------
 * Decide que vista se muestra, mantiene la sesion y registra el service worker.
 *
 * POR QUE EL ACCESS TOKEN NO SOBREVIVE A UNA RECARGA
 * -------------------------------------------------
 * Vive en memoria (ver `api.js`). Eso significa que recargar la pagina pide la
 * contrasena de nuevo.
 *
 * Es un precio conscious, y es el correcto para esta app: en un deposito el
 * equipo es compartido y muchas veces queda abierto en una tablet. Con el token
 * en `localStorage`, cerrar el navegador no cerraba la sesion, y el turno
 * siguiente hereda la sesion del anterior. Aca se pierde el refresh (que esta en
 * `sessionStorage` y si sobrevive la recarga), pero el acceso hay que rehacerlo.
 *
 * LA SESION SE RESTAURA EN SILENCIO CUANDO SE PUEDE
 * --------------------------------------------------
 * Al abrir la app se intenta un `refresh` con el token de `sessionStorage`. Si
 * funciona, entra sin pedir nada: el caso normal es volver a la pestana. Si no,
 * muestra el login. El refresh se hace DESPUES de pintar el esqueleto, para que
 * en un celular lento no haya una pantalla en blanco esperando la red.
 */

import { api, cerrarSesion, haySesion, configurar } from './api.js';
import { crear, reemplazar } from './dom.js';
import { avisar, marcarPendientes } from './avisos.js';
import { vistaLogin } from './vistas/login.js';
import { vistaEscanear } from './vistas/escanear.js';
import { vistaInventario, vistaDispositivo } from './vistas/inventario.js';
import { vistaConsultas } from './vistas/consultas.js';

const contenido = document.getElementById('contenido');
const cabecera = document.getElementById('cabecera');

/** Estado de la UI. */
let usuario = null;
let vistaActual = null;
let vistaActualNombre = null;

/**
 * Registro del service worker.
 *
 * Solo en produccion y solo sobre HTTPS o localhost. En desarrollo registra lo
 * que no se necesita: el SW cachea el bundle viejo y uno edita CSS y ve la
 * version anterior sin entender por que.
 */
function registrarServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  if (!window.isSecureContext) return;

  // En DESARROLLO no se registra, y se decide por el hostname y no por
  // `import.meta.env`: no hay bundler (los modulos ES se cargan nativos), asi
  // que `import.meta.env` no existe y accederlo lanzaria.
  //
  // Sin esto, el service worker cachea el bundle viejo y uno edita el CSS, ve
  // la version anterior y no entiende por que. El sintoma clasico de un SW en
  // desarrollo.
  const esDesarrollo = /^(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$/.test(
    window.location.hostname,
  );
  if (esDesarrollo) return;

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Sin service worker la app funciona igual: pierde el modo offline, nada
      // mas. No se avisa porque seria un ruido constante en desarrollo.
    });
  });
}

// ---------------------------------------------------------------------------
// Navegacion
// ---------------------------------------------------------------------------

const VISTAS = {
  escanear: { etiqueta: 'Escanear', construir: vistaEscanear },
  inventario: { etiqueta: 'Inventario', construir: vistaInventario },
  consultas: { etiqueta: 'Consultas', construir: vistaConsultas },
};

/** Que permisos tiene este usuario. Viene del login y del endpoint /auth/yo. */
function permisosDe(usuarioActual) {
  const lista = usuarioActual?.permisos;
  if (Array.isArray(lista)) return lista;

  // Fallback por rol, para cuando la sesion se restaura por refresh y el
  // `/auth/yo` todavia no respondio. Es una COPIA de `core/permissions.py`, y
  // por eso es una lista de permisos exactos que el backend define: sirve
  // unicamente para pintar la UI antes de que llegue la respuesta real.
  const porRol = {
    ADMIN: [
      'users:read', 'users:create', 'users:edit', 'users:delete', 'users:reset_password',
      'roles:read', 'roles:edit',
      'devices:read', 'devices:edit', 'movements:read', 'movements:create',
      'repairs:read', 'repairs:edit', 'repairs:assign',
      'ai:query', 'ai:analyze', 'ai:report', 'audit:read',
    ],
    TECHNICIAN: [
      'devices:read', 'repairs:read', 'repairs:edit',
      'ai:query', 'ai:analyze', 'ai:report',
    ],
    OPERATOR: [
      'devices:read', 'movements:read', 'movements:create',
      'ai:query', 'ai:report',
    ],
  };

  return porRol[usuarioActual?.role_name] ?? [];
}

function mostrarVista(nombre, opciones = {}) {
  // Si se cambia de vista, la anterior se destruye. Una vista puede tener
  // recursos que no se limpian solos: la camara del escaner, por ejemplo, que
  // sigue encendida si no se apaga explicitamente.
  if (vistaActual?.destruir) {
    try {
      vistaActual.destruir();
    } catch (error) {
      // Que un fallo al limpiar no impida mostrar la vista siguiente: el
      // operador necesita trabajar aunque la camara no se haya podido apagar.
      console.error('No se pudo limpiar la vista anterior:', error);
    }
  }

  vistaActualNombre = nombre;

  if (nombre === 'dispositivo') {
    vistaActual = vistaDispositivo(opciones.deviceId, {
      alVolver: () => mostrarVista('inventario'),
    });
  } else {
    const definicion = VISTAS[nombre];
    if (!definicion) return;
    vistaActual = definicion.construir({
      alAbrirDispositivo: (id) => mostrarVista('dispositivo', { deviceId: id }),
      permisos: permisosDe(usuario),
    });
  }

  // Toda vista devuelve `{ elemento, destruir? }`. Aceptar tambien un elemento
  // suelto evita que una vista nueva rompa por no conocer el contrato, pero el
  // contrato nuevo es el que se escribe.
  const elemento = vistaActual?.elemento ?? vistaActual;

  reemplazar(contenido, elemento);

  // `aria-current` en la pestana activa, y el estado del historial del
  // navegador para que el boton "atras" del celular funcione.
  for (const boton of cabecera.querySelectorAll('[data-vista]')) {
    if (boton.dataset.vista === nombre) boton.setAttribute('aria-current', 'page');
    else boton.removeAttribute('aria-current');
  }

  if (window.location.hash !== `#${nombre}`) {
    history.pushState({ vista: nombre }, '', `#${nombre}`);
  }
}

function pintarLogin() {
  cabecera.hidden = true;
  vistaActual?.destruir?.();
  vistaActual = null;

  // Se desenvuelve `elemento` igual que en `mostrarVista`: el login usa el
  // mismo contrato `{ elemento, destruir? }` que las demas vistas.
  //
  // Sin el `?? login`, se insertaba el objeto entero y el cuerpo de la pagina
  // quedaba con un literal "[object Object]" en vez del formulario. No lo
  // detecta ningun test de unidad: solo se ve corriendo la app.
  const login = vistaLogin({
    alEntrar: async (datos) => {
      usuario = datos.usuario ?? { role_name: datos.role_name };
      // Se leen los permisos reales del servidor, no los del rol del JWT: el
      // backend decide, y una copia aca se desincroniza en el primer cambio de
      // permisos.
      await cargarUsuario();
      cabecera.hidden = false;
      mostrarVista('escanear');
      avisar(`Hola, ${usuario.full_name ?? usuario.email ?? ''}`.trim(), 'exito', 2500);
    },
  });

  vistaActual = login;
  reemplazar(contenido, login.elemento ?? login);
}

async function cargarUsuario() {
  try {
    // `api.miPerfil()` y `api.roles()`: el PERFIL y los PERMISOS los decide el
    // servidor. El frontend no los calcula: una copia de la tabla de permisos
    // acá se desincroniza en el primer cambio y produce botones visibles que
    // devuelven 403.
    //
    // `roles()` puede fallar sin romper: un usuario que no tenga `roles:read`
    // (un Operador) sigue usando la app, solo sin la lista de roles.
    const [perfil, permisos] = await Promise.all([
      api.miPerfil(),
      api.roles().catch(() => null),
    ]);

    usuario = { ...(usuario ?? {}), ...perfil };
    if (permisos) usuario.permisos = permisos;
  } catch (error) {
    // ANTES este `catch` era `catch {}` y se comia hasta los TypeError, que es
    // como paso lo de `api.pedir`: el modulo exportaba la funcion pero no el
    // metodo, y la app Follow andando con el perfil sin cargar y sin que nadie
    // se enterara.
    //
    // Un error de autenticacion ya lo maneja `api.js` (cierra la sesion). Lo
    // que se registra aca es todo lo demas.
    if (error?.status !== 401 && error?.status !== 403) {
      console.error('No se pudo cargar el perfil del usuario:', error);
    }
  }
}

// ---------------------------------------------------------------------------
// Sesion
// ---------------------------------------------------------------------------

async function intentarRestaurar() {
  let refresh = null;
  try {
    refresh = sessionStorage.getItem('si.refresh');
  } catch {
    // Sin storage (Safari privado, iframe): no hay nada que restaurar y se
    // muestra el login. Es lo correcto, no un error a mostrar.
    return null;
  }

  if (!refresh) return null;

  try {
    return await api.pedir('/auth/refresh', {
      metodo: 'POST',
      cuerpo: { refresh_token: refresh },
      reintentar: false,
    });
  } catch {
    return null;
  }
}

function cerrar() {
  api.logout();
  cerrarSesion();
  usuario = null;
  vistaActual = null;
  vistaActualNombre = null;
  history.replaceState(null, '', window.location.pathname);
  pintarLogin();
}

// ---------------------------------------------------------------------------
// Arranque
// ---------------------------------------------------------------------------

async function arrancar() {
  // Los errores no controlados se avisan, pero no se tragan en silencio: un
  // `catch (e) {}` en un manejador deja la app en un estado que nadie puede
  // explicar.
  window.addEventListener('unhandledrejection', (evento) => {
    console.error('Promesa rechazada sin manejar:', evento.reason);
  });

  registrarServiceWorker();

  // Se pinta el esqueleto antes de tocar la red: en un celular con 4G de
  // segundo plano, esperar la red antes de pintar es esperar en blanco.
  reemplazar(contenido, crear('div', { clase: 'cargando', texto: 'Iniciando…' }));

  const restaurada = await intentarRestaurar();

  if (restaurada && haySesion()) {
    usuario = restaurada.usuario ?? { role_name: restaurada.role_name };
    await cargarUsuario();
    cabecera.hidden = false;
    const destino = window.location.hash.replace('#', '');
    mostrarVista(VISTAS[destino] ? destino : 'escanear');
  } else {
    pintarLogin();
  }
}

// --- Eventos globales -------------------------------------------------------

for (const boton of cabecera.querySelectorAll('[data-vista]')) {
  boton.addEventListener('click', () => mostrarVista(boton.dataset.vista));
}

document.getElementById('btn-salir')?.addEventListener('click', cerrar);

configurar({
  alExpirar: () => {
    usuario = null;
    pintarLogin();
  },
});

// Boton atras del celular: vuelve a la vista anterior sin recargar.
window.addEventListener('popstate', (evento) => {
  const destino = evento.state?.vista ?? window.location.hash.replace('#', '');
  if (destino && usuario) mostrarVista(destino);
});

// La app vuelve a la vida con un token que puede haberse vencido mientras
// estaba dormida en background.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && haySesion()) {
    // Se refresca en silencio: si falla, `api.js` cierra la sesion solo.
    api.pedir('/auth/yo').catch(() => {});
  }
});

// Estado de la conexion, para el aviso de pendientes.
window.addEventListener('online', () => {
  marcarPendientes(0);
  avisar('Volvio la conexion.', 'exito', 2500);
});

window.addEventListener('offline', () => {
  avisar('Sin conexion. Los escaneos se guardan y se envian solos.', 'alerta', 0);
});

arrancar();
