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

import { abrirDialogo } from './dialogo.js';
import {
  api,
  cerrarSesion,
  haySesion,
  configurar,
  consumirOperacionPendiente,
  restaurarSesion,
} from './api.js';
import { crear, reemplazar } from './dom.js';
import { avisar, marcarPendientes } from './avisos.js';
/**
 * Sello de build.
 *
 * POR QUE EXISTE
 * --------------
 * No hay bundler: el navegador carga los modulos ES nativos desde nginx. Por eso
 * no hay un hash de contenido que cambie solo, y con un `Cache-Control` mal
 * puesto el navegador sigue ejecutando un archivo viejo mientras el servidor ya
 * tiene el nuevo. Cuando eso pasa, los errores que aparecen no corresponden a
 * ningun archivo del repo y no hay forma de saber que version se esta mirando.
 *
 * Este sello se incrementa con cada cambio y se muestra en el pie. Si el pie dice
 * algo distinto de lo esperado, el problema no es el codigo: es la cache del
 * navegador, y se arregla con una recarga forzada (Cmd+Shift+R).
 */
const SELLO_BUILD = '2619-1';

import { vistaLogin } from './vistas/login.js';
import { vistaEscanear } from './vistas/escanear.js';
import { vistaInventario, vistaDispositivo } from './vistas/inventario.js';
import { vistaConsultas } from './vistas/consultas.js';
import { vistaNuevo } from './vistas/nuevo.js';
import { vistaProductos } from './vistas/productos.js';
import { vistaCompras } from './vistas/compras.js';
import { vistaMovimientos } from './vistas/movimientos.js';
import { vistaAuditoria } from './vistas/auditoria.js';

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
  movimientos: { etiqueta: 'Movimientos', construir: vistaMovimientos },
  consultas: { etiqueta: 'Consultas', construir: vistaConsultas },
  productos: { etiqueta: 'Productos', construir: vistaProductos },
  compras: { etiqueta: 'Compras', construir: vistaCompras },
  auditoria: { etiqueta: 'Auditoría', construir: vistaAuditoria },
  nuevo: { etiqueta: 'Nuevo', construir: vistaNuevo },
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
      // Editar y dar de baja son acciones de `devices:edit`. La ficha decide si
      // muestra los botones con este dato; el backend los vuelve a exigir igual.
      permisos: permisosDe(usuario),
      alEliminar: () => mostrarVista('inventario'),
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
      actualizarEstadoIA();
      avisar(`Hola, ${usuario.full_name ?? usuario.email ?? ''}`.trim(), 'exito', 2500);
      // Reanudar operación pendiente (si venía de expiración con refresh token válido)
      await reanudarOperacionPendiente();
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
    // `miPerfil()` devuelve `{ usuario, permisos, sesiones_activas }`. ANTES el
    // codigo hacia `usuario = { ...usuario, ...perfil }`, que metia un campo
    // `usuario` adentro de `usuario` y dejaba `role_name` sin actualizar; y
    // encima pisaba `permisos` con `api.roles()`, que devuelve `{roles: [...]}`
    // (los nombres de rol), NO la lista de permisos. Resultado: `usuario.permisos`
    // era un objeto, `permisos.includes(...)` tiraba, y NINGUNA pestana que
    // dependa de permisos se mostraba — el sintoma era "la pestana Nuevo nunca
    // aparece" con el rol correcto.
    const [perfil, roles] = await Promise.all([
      api.miPerfil(),
      api.roles().catch(() => null),
    ]);

    // El perfil trae el usuario y sus permisos reales, decididos por el servidor.
    usuario = { ...(usuario ?? {}), ...(perfil?.usuario ?? {}), permisos: perfil?.permisos ?? [] };
    // La lista de roles (nombres) es para selects de administracion, no permisos.
    if (roles?.roles) usuario.roles = roles.roles;

    // Sin esta llamada, las pestanas que dependen de permisos NO se refrescaban
    // tras un login exitoso: el nombre de usuario se pintaba (eso lo hace el
    // llamador), pero "Productos"/"Compras"/"Nuevo" seguian con el `hidden` del
    // HTML. `actualizarCabeceraUsuario` solo se llamaba en el `catch`.
    actualizarCabeceraUsuario();
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

    actualizarCabeceraUsuario();
  }
}

// ---------------------------------------------------------------------------
// Sesion
// ---------------------------------------------------------------------------

async function intentarRestaurar() {
  // Sin storage (Safari privado, iframe) o sin refresh token, `restaurarSesion`
  // devuelve null y se muestra el login: es lo correcto, no un error a mostrar.
  return restaurarSesion();
}


/**
 * Actualiza la info del usuario en la cabecera.
 * Se llama tras login y tras restaurar sesión.
 */
function actualizarCabeceraUsuario() {
  const contenedor = document.getElementById('cabecera-usuario');
  const nombreEl = document.getElementById('cabecera-usuario-nombre');
  const rolEl = document.getElementById('cabecera-usuario-rol');
  const pestanaNuevo = document.getElementById('pestana-nuevo');
  const pestanaProductos = document.getElementById('pestana-productos');
  const pestanaCompras = document.getElementById('pestana-compras');
  const pestanaMovimientos = document.getElementById('pestana-movimientos');
  const pestanaAuditoria = document.getElementById('pestana-auditoria');

  if (!contenedor || !nombreEl || !rolEl) return;

  if (usuario && (usuario.full_name || usuario.email)) {
    nombreEl.textContent = usuario.full_name || usuario.email;
    // El rol viene del backend como role_name (ADMIN, TECHNICIAN, OPERATOR)
    const rol = usuario.role_name || '—';
    rolEl.textContent = rol.charAt(0) + rol.slice(1).toLowerCase();
    contenedor.hidden = false;

    // Las pestañas que dependen de permisos se muestran segun lo que el SERVIDOR
    // devolvio. No se adivina por rol: una copia de la tabla de permisos en el
    // cliente se desincroniza y deja botones visibles que dan 403.
    const perms = usuario.permisos ?? [];
    // "Nuevo" (alta de equipo) y "Productos" (maestro) piden devices:edit.
    if (pestanaNuevo) pestanaNuevo.hidden = !perms.includes('devices:edit');
    // Productos se ve con permiso de lectura de inventario (lo tiene el tecnico:
    // necesita ver el catalogo aunque no lo edite).
    if (pestanaProductos) pestanaProductos.hidden = !perms.includes('devices:read');
    // Compras pide ver movimientos (ADMIN y OPERATOR).
    if (pestanaCompras) pestanaCompras.hidden = !perms.includes('movements:read');
    // Movimientos pide CREAR movimientos: quien solo puede leer (un técnico) no
    // tiene por qué ver una pestaña llena de botones que le van a dar 403.
    if (pestanaMovimientos) pestanaMovimientos.hidden = !perms.includes('movements:create');
    // Auditoría es de ADMIN: muestra lo que hizo cada usuario.
    if (pestanaAuditoria) pestanaAuditoria.hidden = !perms.includes('audit:read');
  } else {
    contenedor.hidden = true;
    if (pestanaNuevo) pestanaNuevo.hidden = true;
    if (pestanaProductos) pestanaProductos.hidden = true;
    if (pestanaCompras) pestanaCompras.hidden = true;
    if (pestanaMovimientos) pestanaMovimientos.hidden = true;
    if (pestanaAuditoria) pestanaAuditoria.hidden = true;
  }
}

function cerrar() {
  const btnCancelar = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Cancelar',
    atributos: { type: 'button' },
  });
  const btnConfirmar = crear('button', {
    clase: 'boton boton--peligro',
    texto: 'Cerrar sesión',
    atributos: { type: 'button' },
  });

  abrirDialogo({
    titulo: '¿Cerrar sesión?',
    mensaje: 'Se cerrará tu sesión actual. ¿Continuar?',
    clase: 'modal--confirmacion',
    acciones: [btnCancelar, btnConfirmar],
    alAbrir: (_modal, cerrarModal) => {
      btnCancelar.addEventListener('click', cerrarModal);
      btnConfirmar.addEventListener('click', () => {
        cerrarModal();
        hacerLogout();
      });
    },
  });
}

/**
 * Muestra modal de login (para expiración de token durante operación).
 * NO navega a login page - mantiene la vista actual en background.
 * @param {Function} alExito - Callback cuando login exitoso
 */
function mostrarLoginModal(alExito) {
  let cerrarModal = () => {};

  const form = crear('form', {
    clase: 'apilado',
    estilo: 'margin-top:1rem;gap:0.75rem',
    atributos: {
      onsubmit: async (e) => {
        e.preventDefault();
        const email = emailInput.value.trim();
        const password = passwordInput.value;
        if (!email || !password) return;

        btnSubmit.disabled = true;
        btnSubmit.textContent = 'Entrando…';

        try {
          const datos = await api.login(email, password);
          cerrarModal();
          await alExito(datos);
        } catch (error) {
          avisar(error.message, 'error');
        } finally {
          btnSubmit.disabled = false;
          btnSubmit.textContent = 'Entrar';
        }
      },
    },
  });

  const emailInput = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'email',
      placeholder: 'Email',
      autocomplete: 'email',
      required: true,
      autofocus: true,
    },
  });

  const passwordInput = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'password',
      placeholder: 'Contraseña',
      autocomplete: 'current-password',
      required: true,
    },
  });

  const btnSubmit = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Entrar',
    atributos: { type: 'submit' },
  });

  form.append(
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Email' }), emailInput] }),
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Contraseña' }), passwordInput] }),
    btnSubmit
  );

  // `cuerpo` a mano porque este modal lleva un `<form>` adentro, no un texto:
  // `abrirDialogo` acepta nodos ya construidos.
  cerrarModal = abrirDialogo({
    titulo: 'Sesión expirada',
    cuerpo: [
      crear('p', { clase: 'tenue pequeno', texto: 'Tu sesión ha expirado. Inicia sesión para continuar.' }),
      form,
    ],
    clase: 'modal--login',
  });
}

/**
 * Reanuda operación pendiente tras login exitoso.
 *
 * `consumirOperacionPendiente` es un export con nombre de `api.js`, no un metodo
 * del objeto `api`: llamar `api.consumirOperacionPendiente()` es `undefined`
 * y tira un TypeError que el manejador de promesas no controladas de mas abajo
 * convierte en un error rojo en la consola, sin relación visible con lo que el
 * operador estaba haciendo.
 */
async function reanudarOperacionPendiente() {
  const op = consumirOperacionPendiente();
  if (!op) return;

  const { ruta, ...opciones } = op;
  try {
    avisar('Reanudando operación…', 'info', 1500);
    await api.pedir(ruta, opciones);
    avisar('Operación completada', 'exito', 2000);
  } catch (error) {
    if (error.status !== 401) { // 401 ya manejado por api.js
      avisar(`No se pudo reanudar: ${error.message}`, 'error');
    }
  }
}

function hacerLogout() {
  api.logout();
  cerrarSesion();
  usuario = null;
  vistaActual = null;
  vistaActualNombre = null;
  history.replaceState(null, '', window.location.pathname);
  actualizarCabeceraUsuario();
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

// La auditoría puede saltar a la ficha de un equipo. La vista dispara el evento
// en vez de llamar a `mostrarVista` directamente: `mostrarVista` vive en este
// archivo y las vistas lo importan de arriba hacia abajo — importarlo desde
// `auditoria.js` cerraría un ciclo de módulos. Un evento de documento desacopla
// las dos capas sin que la vista conozca la navegación.
document.addEventListener('auditoria:abrir-equipo', (evento) => {
  mostrarVista('dispositivo', { deviceId: evento.detail });
});

// Botón modo exterior / alto contraste (para sol directo, guantes, accesibilidad)
const btnExterior = document.getElementById('btn-exterior');
if (btnExterior) {
  btnExterior.addEventListener('click', () => {
    const activo = document.documentElement.toggleAttribute('data-outdoor');
    btnExterior.setAttribute('aria-pressed', activo);
    btnExterior.title = activo
      ? 'Desactivar modo exterior (alto contraste)'
      : 'Activar modo exterior / alto contraste (para sol directo)';
    avisar(activo ? 'Modo exterior activado' : 'Modo exterior desactivado', 'exito', 2000);
  });
}

configurar({
  alExpirar: () => {
    usuario = null;
    pintarLogin();
  },
  alRequerirLogin: () => {
    // Mostrar modal login en lugar de navegar a login page
    mostrarLoginModal(async (datos) => {
      usuario = datos.usuario ?? { role_name: datos.role_name };
      await cargarUsuario();
      cabecera.hidden = false;
      avisar(`Hola, ${usuario.full_name ?? usuario.email ?? ''}`.trim(), 'exito', 2500);
      // Reanudar operación pendiente
      await reanudarOperacionPendiente();
    });
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

/* ---------------------------------------------------------------------------
   Footer - inicialización dinámica
   --------------------------------------------------------------------------- */
function inicializarFooter() {
  // Versión (inyectada por Vite/build o fallback)
  // En módulos ES nativos sin bundler, import.meta.env no existe.
  // Usamos una función auxiliar para acceder de forma segura.
  const getEnv = (key) => {
    try {
      return import.meta.env?.[key];
    } catch {
      return undefined;
    }
  };
  const version = getEnv('VITE_APP_VERSION') || '0.3.0';
  const buildHash = getEnv('VITE_BUILD_HASH') || SELLO_BUILD;
  const env = getEnv('MODE') || 'development';
  const company = getEnv('VITE_COMPANY_NAME') || 'FC Quality';
  const supportEmail = getEnv('VITE_SUPPORT_EMAIL') || 'soporte@fcquality.com';

  // Año dinámico
  const yearEl = document.querySelector('.footer__year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // Versión
  const versionEl = document.getElementById('footer-version');
  if (versionEl) versionEl.textContent = `v${version}`;

  // Build hash
  const buildEl = document.getElementById('build-hash');
  if (buildEl) buildEl.textContent = buildHash.slice(0, 7);

  // Entorno
  const envEl = document.getElementById('footer-env');
  if (envEl) {
    envEl.textContent = env.toUpperCase();
    envEl.dataset.env = env;
  }

  // Empresa
  const brandEl = document.querySelector('.footer__brand strong');
  if (brandEl) brandEl.textContent = company;

  // Email soporte
  const supportLink = document.querySelector('.footer__legal a[href^="mailto:"]');
  if (supportLink) supportLink.href = `mailto:${supportEmail}`;

  // Estado IA
  actualizarEstadoIA();
  setInterval(actualizarEstadoIA, 60000);
}

/**
 * Estado de la IA en el footer.
 *
 * Vive a nivel de modulo, y no dentro de `inicializarFooter`, para poder
 * refrescarlo en el momento exacto en que hay sesion: el pie se inicializa una
 * sola vez, al arrancar, y para entonces casi siempre esta en la pantalla de
 * login.
 */
async function actualizarEstadoIA() {
  const el = document.getElementById('ai-status');
  if (!el) return;

  // `/api/ai/health` es un endpoint autenticado: sin sesion responde 401
  // siempre. Preguntar igual no informa nada, deja un error rojo en la consola
  // cada 60 segundos mientras el operador esta en la pantalla de login, y hace
  // creer que la IA esta caida cuando lo unico que falta es iniciar sesion.
  if (!haySesion()) {
    el.textContent = 'IA: —';
    el.style.color = '';
    return;
  }

  try {
    // `api.estadoIA()`, no `fetch('/api/ai/health')`: el access token vive en el
    // modulo de la API, en memoria. Un `fetch` pelado sale sin cabecera
    // `Authorization` y el endpoint responde 401 aunque la sesion este
    // abierta — que es exactamente lo que pasaba y se veía como un error rojo
    // en la consola del navegador con el usuario ya logueado.
    const d = await api.estadoIA();
    el.textContent = d.disponible
      ? `IA: ${d.modelo_configurado || 'ollama'} ✓`
      : `IA: indisponible`;
    el.style.color = d.disponible ? 'var(--success)' : 'var(--error)';
  } catch {
    el.textContent = 'IA: error';
    el.style.color = 'var(--error)';
  }
}

// Inicializar footer al arrancar
inicializarFooter();

arrancar();
