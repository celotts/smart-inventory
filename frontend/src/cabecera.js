/**
 * La cabecera: quien esta conectado, el pie, y el modo exterior.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * Son tres cosas que NO tienen que ver con el arranque de la app ni con la
 * sesion: mostrar quien esta conectado, pintar el pie con la version, y el
 * modo de alto contraste para el sol directo. Estaban en `main.js` y lo que
 * tienen en comun es que son "chrome": lo que esta siempre en pantalla, en
 * todas las vistas.
 *
 * EL MODO EXTERIOR
 * -----------------
 * Un deposito con sol directo y guantes: la pantalla tiene queverse desde dos
 * metros y con el dedal puesto. El modo sube el contraste y el tamano de los
 * controles. Es un `data-` en el `<html>` y un bloque de CSS: el estado NO
 * vive en JavaScript, asi que sobrevive a un reload sin guardarlo en
 * ningun lado.
 */

import { api, haySesion } from './api.js';
import { estado } from './estado.js';

/** El sello manual de build, como fallback del hash real. */
const SELLO_BUILD = '2619-1';

const cabecera = document.getElementById('cabecera');

/**
 * Actualiza la info del usuario en la cabecera y las pestanas que dependen
 * de permisos.
 *
 * LAS PESTANAS SEGUN EL SERVIDOR
 * ------------------------------
 * No se adivina por rol: una copia de la tabla de permisos en el cliente se
 * desincroniza en el primer cambio y deja botones visibles que dan 403. Lo
 * que decide es el backend, y aca solo se refleja.
 */
export function actualizarCabeceraUsuario() {
  const contenedor = document.getElementById('cabecera-usuario');
  const nombreEl = document.getElementById('cabecera-usuario-nombre');
  const rolEl = document.getElementById('cabecera-usuario-rol');
  const pestanaNuevo = document.getElementById('pestana-nuevo');
  const pestanaProductos = document.getElementById('pestana-productos');
  const pestanaCompras = document.getElementById('pestana-compras');
  const pestanaMovimientos = document.getElementById('pestana-movimientos');
  const pestanaAuditoria = document.getElementById('pestana-auditoria');
  const pestanaIncidencias = document.getElementById('pestana-incidencias');

  if (!contenedor || !nombreEl || !rolEl) return;

  const usuario = estado.usuario;

  if (usuario && (usuario.full_name || usuario.email)) {
    nombreEl.textContent = usuario.full_name || usuario.email;
    // El rol viene del backend como role_name (ADMIN, TECHNICIAN, OPERATOR)
    const rol = usuario.role_name || '—';
    rolEl.textContent = rol.charAt(0) + rol.slice(1).toLowerCase();
    contenedor.hidden = false;

    const perms = usuario.permisos ?? [];
    // "Nuevo" (alta de equipo) y "Productos" (maestro) piden devices:edit.
    if (pestanaNuevo) pestanaNuevo.hidden = !perms.includes('devices:edit');
    // Productos se ve con permiso de lectura de inventario (lo tiene el tecnico:
    // necesita ver el catalogo aunque no lo edite).
    if (pestanaProductos) pestanaProductos.hidden = !perms.includes('devices:read');
    // Compras pide ver movimientos (ADMIN y OPERATOR).
    if (pestanaCompras) pestanaCompras.hidden = !perms.includes('movements:read');
    // Movimientos pide CREAR movimientos: quien solo puede leer (un tecnico) no
    // tiene por que ver una pestana llena de botones que le van a dar 403.
    if (pestanaMovimientos) pestanaMovimientos.hidden = !perms.includes('movements:create');
    // Auditoría es de ADMIN: muestra lo que hizo cada usuario.
    if (pestanaAuditoria) pestanaAuditoria.hidden = !perms.includes('audit:read');
    // Incidencias: verlas es de cualquiera (el operador ve los problemas que
    // reporta), gestionarlas es del taller.
    if (pestanaIncidencias) pestanaIncidencias.hidden = !perms.includes('incidents:read');
  } else {
    contenedor.hidden = true;
    if (pestanaNuevo) pestanaNuevo.hidden = true;
    if (pestanaProductos) pestanaProductos.hidden = true;
    if (pestanaCompras) pestanaCompras.hidden = true;
    if (pestanaMovimientos) pestanaMovimientos.hidden = true;
    if (pestanaAuditoria) pestanaAuditoria.hidden = true;
    if (pestanaIncidencias) pestanaIncidencias.hidden = true;
  }
}

/**
 * El boton de modo exterior (alto contraste para el sol directo).
 */
export function iniciarModoExterior() {
  const btnExterior = document.getElementById('btn-exterior');
  if (!btnExterior) return;

  btnExterior.addEventListener('click', () => {
    const activo = document.documentElement.getAttribute('data-outdoor') !== '1';
    if (activo) {
      document.documentElement.setAttribute('data-outdoor', '1');
    } else {
      document.documentElement.removeAttribute('data-outdoor');
    }
    btnExterior.setAttribute('aria-pressed', activo);
    btnExterior.title = activo
      ? 'Desactivar modo exterior (alto contraste)'
      : 'Activar modo exterior / alto contraste (para sol directo)';
    avisarExterno(activo ? 'Modo exterior activado' : 'Modo exterior desactivado');
  });
}

function avisarExterno(mensaje) {
  // Import diferido: `avisos.js` no depende de la cabecera, pero mantener el
  // grafo de imports de este archivo en una sola direccion evita surprises.
  import('./avisos.js').then(({ avisar }) => avisar(mensaje, 'exito', 2000));
}

/**
 * Pinta el pie: version, build, entorno, empresa y el estado de la IA.
 */
export function inicializarFooter() {
  // Version (inyectada por Vite/build o fallback)
  // En módulos ES nativos sin bundler, import.meta.env no existe.
  // Usamos una función auxiliar para acceder de forma segura.
  const getEnv = (key) => {
    try {
      return import.meta.env?.[key];
    } catch {
      return undefined;
    }
  };
  // La version del PRODUCTO, que es la que ve el operador en el pie. No es el
  // hash de build: el hash cambia con cada cambio de contenido (cada deploy),
  // y la version cambia cuando hay una entrega con novedades.
  //
  // 0.5.0: modulo de incidencias con SLA operable (horario laboral, avisos y
  // escalamiento), vista de Incidencias, campana de avisos y refactor del front.
  const version = getEnv('VITE_APP_VERSION') || '0.5.0';
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
  //
  // Se pinta primero el sello manual (que es un fallback: si el servidor no
  // responde, el pie tiene que mostrar algo), y despues el hash REAL que
  // calcula el Dockerfile y sirve en `/build.txt`. El hash es lo que
  // identifica un deploy: con el sello manual a mano, dos personas pueden
  // estar mirando despliegues distintos y no saberlo.
  const buildEl = document.getElementById('build-hash');
  if (buildEl) buildEl.textContent = buildHash.slice(0, 7);

  if (!getEnv('VITE_BUILD_HASH')) {
    import('./build.js').then(({ obtenerSelloDelServidor }) => {
      obtenerSelloDelServidor().then((sello) => {
        if (sello && buildEl) buildEl.textContent = sello.slice(0, 7);
      });
    });
  }

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
export async function actualizarEstadoIA() {
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
      : 'IA: indisponible';
    el.style.color = d.disponible ? 'var(--success)' : 'var(--error)';
  } catch {
    el.textContent = 'IA: error';
    el.style.color = 'var(--error)';
  }
}
