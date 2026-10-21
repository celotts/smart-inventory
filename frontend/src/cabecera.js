/**
 * La cabecera: quien está conectado, el pie, y el modo exterior.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * Son tres cosas que NO tienen que ver con el arranque de la app ni con la
 * sesión: mostrar quien está conectado, pintar el pie con la versión, y el
 * modo de alto contraste para el sol directo. Estaban en `main.js` y lo que
 * tienen en comun es que son "chrome": lo que está siempre en pantalla, en
 * todas las vistas.
 *
 * EL MODO EXTERIOR
 * -----------------
 * Un depósito con sol directo y guantes: la pantalla tiene queverse desde dos
 * metros y con el dedal puesto. El modo sube el contraste y el tamaño de los
 * controles. Es un `data-` en el `<html>` y un bloque de CSS: el estado NO
 * vive en JavaScript, así que sobrevive a un reload sin guardarlo en
 * ningún lado.
 */

import { api, haySesion } from './api.js';
import { etiquetaDeRol, prefijoDeRol } from './i18n.js';
import { crear } from './dom.js';
import { conTooltip } from './tooltip.js';
import { estado } from './estado.js';
import { MODULOS, modulosDe, pestanasDelModulo } from './modulos.js';

/** El sello manual de build, como fallback del hash real. */
const SELLO_BUILD = '2619-1';

const cabecera = document.getElementById('cabecera');

/**
 * Actualiza la info del usuario en la cabecera y las pestañas que dependen
 * de permisos.
 *
 * LAS Pestañas Según EL SERVIDOR
 * ------------------------------
 * No se adivina por rol: una copia de la tabla de permisos en el cliente se
 * desincroniza en el primer cambió y deja botones visibles que dan 403. Lo
 * que decide es el backend, y acá solo se refleja.
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
  const pestanaUsuarios = document.getElementById('pestana-usuarios');
  const pestanaExistencia = document.getElementById('pestana-existencia');

  if (!contenedor || !nombreEl || !rolEl) return;

  const usuario = estado.usuario;

  if (usuario && (usuario.full_name || usuario.email)) {
    // El NOMBRE no se muestra: el bloque dice "Rol: Admin" y el nombre de la
    // persona está en "Mi cuenta". Poner las dos cosas en 24 pixeles de alto
    // hace que se lean como un solo texto largo.
    const nombre = usuario.full_name || usuario.email;
    nombreEl.textContent = '';
    nombreEl.hidden = true;
    // El rol viene del backend como role_name (ADMIN, TECHNICIAN, OPERATOR)
    // La línea del rol: "Rol: Admin". Con el prefijo se sabe que es el rol
    // y no un segundo nombre; y la etiqueta está en el idioma de la app
    // ("Técnico", no "Technician").
    const rol = usuario.role_name || '—';
    rolEl.textContent = `${prefijoDeRol()}${etiquetaDeRol(rol)}`;
    contenedor.hidden = false;
    // En pantallas medianas el nombre se oculta (el avatar solo): el
    // `title` lo deja a un clic de distancia, para que no se pierda.
    contenedor.title = `${nombre} · ${rolEl.textContent}`;

    const perms = usuario.permisos ?? [];
    // Las pestañas que se ven: las del módulo en el que se está trabajando.
    const permitidas = pestanasDelModulo(estado.modulo);
    // "Nuevo" (alta de equipo) y "Productos" (maestro) piden devices:edit.
    if (pestanaNuevo) pestanaNuevo.hidden = !perms.includes('devices:edit') || !permitidas.has('nuevo');
    // Productos se ve con permiso de lectura de inventario (lo tiene el técnico:
    // necesita ver el catalogo aunque no lo edite).
    if (pestanaProductos) pestanaProductos.hidden = !perms.includes('devices:read') || !permitidas.has('productos');
    // Compras pide ver movimientos (ADMIN y OPERATOR).
    if (pestanaCompras) pestanaCompras.hidden = !perms.includes('movements:read') || !permitidas.has('compras');
    // Movimientos pide CREAR movimientos: quien solo puede leer (un técnico) no
    // tiene por que ver una pestaña llena de botones que le van a dar 403.
    if (pestanaMovimientos) pestanaMovimientos.hidden = !perms.includes('movements:create') || !permitidas.has('movimientos');
    // Auditoría es de ADMIN: muestra lo que hizo cada usuario.
    if (pestanaAuditoria) pestanaAuditoria.hidden = !perms.includes('audit:read') || !permitidas.has('auditoria');
    // Incidencias: verlas es de cualquiera (el operador ve los problemas que
    // reporta), gestionarlas es del taller.
    if (pestanaIncidencias) pestanaIncidencias.hidden = !perms.includes('incidents:read') || !permitidas.has('incidencias');
    // Usuarios es de ADMIN: la lista de quien entra es información que un
    // técnico no necesita para trabajar, y el que la tiene puede cambiar el
    // rol de cualquiera.
    if (pestanaUsuarios) pestanaUsuarios.hidden = !perms.includes('users:read') || !permitidas.has('usuarios');
    // Existencia: el stock por rubro. Se ve con permiso de LECTURA de
    // inventario: el depositero necesita saber cuanto hay de cada cosa, y
    // para eso no hace falta permiso de escritura.
    if (pestanaExistencia) pestanaExistencia.hidden = !perms.includes('devices:read') || !permitidas.has('existencia');

    // El selector de módulo se repinta acá: las pestañas y el selector tienen
    // que contar la misma historia, y si cada uno se dibuja por su lado
    // terminan mostrando cosas distintas.
    pintarSelectorModulo();

    // Si el menú no entra en su columna, se avisa con el desvanecido del
    // borde: sin el, un menú que scrollea parece un menú CORTADO. La medicion
    // se hace después de pintar las pestañas, porque antes el ancho es el
    // viejo.
    requestAnimationFrame(() => {
      const nav = document.querySelector('.cabecera__nav');
      if (!nav) return;
      nav.classList.toggle('cabecera__nav--desbordado', nav.scrollWidth > nav.clientWidth + 2);
    });
  } else {
    contenedor.hidden = true;
    if (pestanaNuevo) pestanaNuevo.hidden = true;
    if (pestanaProductos) pestanaProductos.hidden = true;
    if (pestanaCompras) pestanaCompras.hidden = true;
    if (pestanaMovimientos) pestanaMovimientos.hidden = true;
    if (pestanaAuditoria) pestanaAuditoria.hidden = true;
    if (pestanaIncidencias) pestanaIncidencias.hidden = true;
    if (pestanaUsuarios) pestanaUsuarios.hidden = true;
    if (pestanaExistencia) pestanaExistencia.hidden = true;
  }
}

/**
 * El selector de módulo, en la cabecera.
 *
 * Solo PARA QUIEN PUEDE CAMBIAR
 * ----------------------------
 * El módulo es la decisión operativa de la persona ("yo trabajo el depósito"
 * o "yo trabajo el taller"). Un operador con un solo módulo no tiene nada
 * que elegir: el selector no aparece.
 */
/**
 * El selector de módulo, en la cabecera.
 *
 * UN CONTROL CON UNA SOLA Opción ES RUIDO
 * ---------------------------------------
 * El módulo se elige en el lanzador, Después de loguearse: es la decisión
 * operativa del día ("hoy trabajo el depósito" / "hoy trabajo el taller").
 * La cabecera solo lo muestra.
 *
 *   - Con UN solo módulo (operador, técnico), la cabecera muestra una
 *     ETIQUETA con el nombre del módulo. No es un botón porque no hay nada
 *     que pulsar: si lo fuera, el operador lo probaria y no pasaria nada.
 *     Antes se veia un recuadro gris vacío en su lugar, que parece un
 *     error de render.
 *   - Con DOS módulos (admin), la cabecera muestra los dos botones, porque
 *     el admin SI alterna entre el depósito y el taller, y un viaje en
 *     busca y sustitucion por el lanzador cada vez seria absurdo.
 *
 * LA POSICION NO CAMBIA
 * ---------------------
 * La zona del selector está siempre en el mismo lugar de la cabecera, sea
 * una etiqueta o sean dos botones. Lo que se mueve al cambiar de rol es el
 * ancho del contenido, no la posicion de la campana ni del botón de salir.
 *
 * LOS Botones, CUANDO HAY DOS
 * ---------------------------
 * Se pintan TODOS los módulos, en el mismo orden, y los que el usuario no
 * puede usar quedan ocultos con `visibility: hidden`: el hueco ocupa su
 * lugar, así los botones no se mueven de posicion.
 */
export function pintarSelectorModulo() {
  const zona = document.getElementById('cabecera-modulo');
  if (!zona) return;

  zona.replaceChildren();
  zona.hidden = false;

  const disponibles = modulosDe(estado.usuario);

  // UN Solo Módulo: una etiqueta, no un control.
  //
  // Un botón que no lleva a ningún lado es peor que no tener nada: el
  // operador lo pulsa, no pasa nada, y la app parece rota. Con una
  // etiqueta se ve que el módulo YA está elegido y que no hay nada más que
  // decidir.
  if (disponibles.length < 2) {
    const clave = disponibles[0] ?? estado.modulo;
    const modulo = MODULOS[clave];
    if (!modulo) return;

    const etiqueta = crear('span', {
      clase: 'cabecera__módulo cabecera__módulo--fijo',
      texto: modulo.etiqueta,
      atributos: { 'data-modulo': clave },
    });
    conTooltip(etiqueta, modulo.descripcion);
    zona.append(etiqueta);
    return;
  }

  for (const clave of Object.keys(MODULOS)) {
    const modulo = MODULOS[clave];
    const permitido = disponibles.includes(clave);
    const activo = estado.modulo === clave;

    const boton = crear('button', {
      clase:
        `cabecera__modulo${activo ? ' cabecera__módulo--activo' : ''}` +
        (permitido ? '' : ' cabecera__módulo--no-disponible'),
      atributos: {
        type: 'button',
        'aria-pressed': activo ? 'true' : 'false',
        'data-modulo': clave,
      },
      texto: modulo.etiqueta,
    });

    if (permitido) {
      conTooltip(boton, modulo.descripcion);
      boton.addEventListener('click', async () => {
        if (activo) return;
        const { cambiarModulo } = await import('./navegacion.js');
        const { guardarModulo } = await import('./vistas/lanzador.js');
        cambiarModulo(clave, estado.usuario);
        guardarModulo(clave);
      });
    } else {
      // El que no se puede usar no es un botón: es un hueco. Con
      // `visibility: hidden` (y no `hidden`), el hueco OCUPA SU LUGAR: los
      // botones no se mueven de posicion según el rol, y la cabecera se ve
      // igual para todos.
      boton.setAttribute('aria-hidden', 'true');
      boton.tabIndex = -1;
      boton.disabled = true;
    }

    zona.appendChild(boton);
  }
}

/**
 * Deja el selector listo. Se pinta de verdad cuando hay sesión.
 */
export function iniciarSelectorModulo() {
  pintarSelectorModulo();
  // Se vuelve a pintar cuando cambia la sesión (el usuario puede ser otro).
  document.addEventListener('sesion:cambio', pintarSelectorModulo);
}

/**
 * El chip de usuario: abre "Mi cuenta" (cambiar la contraseña).
 *
 * ES UN Botón Y NO UN DIV
 * ----------------------
 * Con un `<div>` al que se le pone un click, el teclado no llega: el
 * operador con teclado no puede cambiar su contraseña, y el lector de
 * pantalla anuncia un texto que no hace nada. Un `<button>` es un botón: foco,
 * Enter y Espacio funcionan, y el lector anuncia "botón".
 */
export function iniciarChipUsuario() {
  const chip = document.getElementById('cabecera-usuario');
  if (!chip) return;

  chip.setAttribute('role', 'button');
  chip.setAttribute('tabindex', '0');
  chip.setAttribute('aria-label', 'Mi cuenta: cambiar la contraseña');

  const abrir = () => {
    import('./mi-cuenta.js').then(({ abrirMiCuenta }) => abrirMiCuenta(estado.usuario));
  };

  chip.addEventListener('click', abrir);
  chip.addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter' || evento.key === ' ') {
      evento.preventDefault();
      abrir();
    }
  });
}

/**
 * El botón de modo exterior (alto contraste para el sol directo).
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
 * Pinta el pie: versión, build, entorno, empresa y el estado de la IA.
 */
export function inicializarFooter() {
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
  // La versión del PRODUCTO, que es la que ve el operador en el pie. No es el
  // hash de build: el hash cambia con cada cambió de contenido (cada deploy),
  // y la versión cambia cuando hay una entrega con novedades.
  //
  // 0.5.0: módulo de incidencias con SLA operable (horario laboral, avisos y
  // escalamiento), vista de Incidencias, campana de avisos y refactor del front.
  const version = getEnv('VITE_APP_VERSION') || '0.5.0';
  const buildHash = getEnv('VITE_BUILD_HASH') || SELLO_BUILD;
  const env = getEnv('MODE') || 'development';
  const company = getEnv('VITE_COMPANY_NAME') || 'FC Quality';
  const supportEmail = getEnv('VITE_SUPPORT_EMAIL') || 'soporte@fcquality.com';

  // Año dinamico
  const yearEl = document.querySelector('.footer__year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // Versión
  const versionEl = document.getElementById('footer-version');
  if (versionEl) versionEl.textContent = `v${version}`;

  // Build hash
  //
  // Se pinta primero el sello manual (que es un fallback: si el servidor no
  // responde, el pie tiene que mostrar algo), y después el hash REAL que
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
 * Vive a nivel de módulo, y no dentro de `inicializarFooter`, para poder
 * refrescarlo en el momento exacto en que hay sesión: el pie se inicializa una
 * sola vez, al arrancar, y para entonces casi siempre está en la pantalla de
 * login.
 */
export async function actualizarEstadoIA() {
  const el = document.getElementById('ai-status');
  if (!el) return;

  // `/api/ai/health` es un endpoint autenticado: sin sesión responde 401
  // siempre. Preguntar igual no informa nada, deja un error rojo en la consola
  // cada 60 segundos mientras el operador está en la pantalla de login, y hace
  // creer que la IA está caida cuando lo único que falta es iniciar sesión.
  if (!haySesion()) {
    el.textContent = 'IA: —';
    el.style.color = '';
    return;
  }

  try {
    // `api.estadoIA()`, no `fetch('/api/ai/health')`: el access token vive en el
    // módulo de la API, en memoria. Un `fetch` pelado sale sin cabecera
    // `Authorization` y el endpoint responde 401 aunque la sesión este
    // abierta — que es exactamente lo que pasaba y se veia como un error rojo
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
