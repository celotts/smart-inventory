/**
 * La sesión: entrar, salir, restaurar, y el login que aparece cuando el
 * token se vence en mitad de una operación.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * La sesión es un TEMA con sus propias reglas: el token vive en memoria, el
 * refresh en `sessionStorage`, hay un login que se abre SIN cambiar de vista
 * (para no perder el formulario que el operador estaba llenando) y una
 * operación pendiente que se reanuda sola. Todo eso mezclado con el arranque
 * de la app era un archivo de 900 líneas donde no se sabia que era la sesión y
 * que era el arranque.
 *
 * EL LOGIN QUE NO NAVEGA
 * ---------------------
 * Cuando el token se vence en mitad de una operación, la app NO tira al
 * login: le pide la clave en un modal y sigue. Perder un formulario de diez
 * camposfilled por un token vencido es la clase de error que hace que el
 * operador deje trusting la app.
 */

import { api, configurar, consumirOperacionPendiente, haySesion, restaurarSesion } from './api.js';
import { actualizarCabeceraUsuario, actualizarEstadoIA } from './cabecera.js';
import { iniciarCampana } from './campana.js';
import { crear, reemplazar } from './dom.js';
import { estado, setUsuario } from './estado.js';
import { mostrarVista } from './navegacion.js';
import { moduloGuardado, pantallaDeEntrada, guardarModulo } from './vistas/lanzador.js';
import { moduloPorDefecto } from './modulos.js';
import { setModulo } from './estado.js';
import { abrirDialogo } from './dialogo.js';
import { avisar } from './avisos.js';
import { vistaLogin } from './vistas/login.js';

const contenido = document.getElementById('contenido');
const cabecera = document.getElementById('cabecera');

/**
 * Pinta la pantalla de login.
 */
export function pintarLogin() {
  cabecera.hidden = true;
  estado.vistaActual?.destruir?.();
  estado.vistaActual = null;

  // Se desenvuelve `elemento` igual que en `mostrarVista`: el login usa el
  // mismo contrato `{ elemento, destruir? }` que las demas vistas.
  //
  // Sin el `?? login`, se insertaba el objeto entero y el cuerpo de la página
  // quedaba con un literal "[object Object]" en vez del formulario. No lo
  // detecta ningún test de unidad: solo se ve corriendo la app.
  const login = vistaLogin({
    alEntrar: async (datos) => {
      try {
        setUsuario(datos.usuario ?? { role_name: datos.role_name });
        // Se leen los permisos reales del servidor, no los del rol del JWT: el
        // backend decide, y una copia acá se desincroniza en el primer cambió
        // de permisos.
        await cargarUsuario();
        cabecera.hidden = false;
      // LA PANTALLA DE ENTRADA: se elige con que módulo se trabaja.
      //
      // Con un solo módulo (operador o técnico) no hay nada que elegir y se
      // entra directo. Con dos (admin) se elige, y la eleccion se recuerda.
      const guardado = moduloGuardado(estado.usuario);
      if (guardado) {
        setModulo(guardado);
        mostrarVista('inicio');
      } else {
        mostrarPantallaDeEntrada();
      }
      // La campana de avisos de SLA arranca con la sesión: sin ella, el
      // pedido de conteo seria un 401 en cada arranque de la app.
      iniciarCampana();
      actualizarEstadoIA();
      avisar(`Hola, ${estado.usuario.full_name ?? estado.usuario.email ?? ''}`.trim(), 'exito', 2500);
        // Reanudar operación pendiente (si venia de expiracion con refresh token válido)
        await reanudarOperacionPendiente();
      } catch (error) {
        // Si algo falla Después de autenticarse, el operador se queda en la
        // pantalla de login sin saber por que: la sesión está bien, pero la
        // app no entra. Sin este mensaje, el único sintoma es "no me deja
        // entrar" y el operador piensa que la clave está mal.
        console.error('No se pudo completar el ingreso:', error);
        mostrarErrorFalloIngreso(error);
      }
    },
  });

  estado.vistaActual = login;
  reemplazar(contenido, login.elemento ?? login);

  // También es una app arrancada: el login ES la pantalla. Sin está marca, un
  // arranque lento que pasa los 12 segundos del vigilante reemplaza el login
  // por "La app no pudo arrancar".
  document.body.dataset.appLista = '1';
}

/**
 * El error de un ingreso que se autentico pero no pudo completar.
 *
 * Se muestra en la PANTALLA DE LOGIN, que es donde está el operador: si el
 * error aparece en un toast que se va solo a los 4 segundos, no sirve de nada.
 */
function mostrarErrorFalloIngreso(error) {
  const caja = crear('p', {
    clase: 'campo__error',
    texto: `Se hizo el ingreso, pero la app no pudo abrir: ${error.message ?? error}`,
  });
  caja.setAttribute('role', 'alert');
  const zona = document.getElementById('contenido');
  if (zona) zona.prepend(caja);
}

/**
 * La pantalla de entrada: elegir módulo.
 *
 * Se muestra Después de autenticarse y ANTES de entrar a la app. Es la
 * separacion entre las dos soluciones: cada persona entra a la suya, y la
 * app se le abre coherente con su rol.
 */
function mostrarPantallaDeEntrada() {
  const pantalla = pantallaDeEntrada({
    usuario: estado.usuario,
    alEntrar: (modulo) => {
      setModulo(modulo);
      guardarModulo(modulo);
      // Las pestañas dependen del módulo: se repintan antes de mostrar la
      // vista, o la app queda con las del módulo anterior.
      actualizarCabeceraUsuario();
      mostrarVista('inicio');
    },
  });

  reemplazar(contenido, pantalla);
  // La navegacion y el selector de módulo se ocultan durante el lanzador: la
  // app todavía no empezo, y ver las pestañas de un módulo que todavía no se
  // eligio confunde.
  cabecera.classList.add('cabecera--lanzador');
  document.body.dataset.appLista = '1';
}

/**
 * Carga el perfil del usuario y sus permisos.
 */
export async function cargarUsuario() {
  try {
    // `api.miPerfil()` y `api.roles()`: el PERFIL y los Permisos los decide el
    // servidor. El frontend no los calcula: una copia de la tabla de permisos
    // acá se desincroniza en el primer cambió y produce botones visibles que
    // devuelven 403.
    //
    // `roles()` puede fallar sin romper: un usuario que no tenga `roles:read`
    // (un Operador) sigue usando la app, solo sin la lista de roles.
    // `miPerfil()` devuelve `{ usuario, permisos, sesiones_activas }`. ANTES el
    // código hacia `usuario = { ...usuario, ...perfil }`, que metia un campo
    // `usuario` adentro de `usuario` y dejaba `role_name` sin actualizar; y
    // encima pisaba `permisos` con `api.roles()`, que devuelve `{roles: [...]}`
    // (los nombres de rol), NO la lista de permisos. Resultado: `usuario.permisos`
    // era un objeto, `permisos.includes(...)` tiraba, y Ninguna pestaña que
    // dependa de permisos se mostraba — el sintoma era "la pestaña Nuevo nunca
    // aparece" con el rol correcto.
    const [perfil, roles] = await Promise.all([
      api.miPerfil(),
      api.roles().catch(() => null),
    ]);

    // El perfil trae el usuario y sus permisos reales, decididos por el servidor.
    setUsuario({
      ...(estado.usuario ?? {}),
      ...(perfil?.usuario ?? {}),
      permisos: perfil?.permisos ?? [],
    });
    // La lista de roles (nombres) es para selects de administracion, no permisos.
    if (roles?.roles) estado.usuario.roles = roles.roles;

    // Sin está llamada, las pestañas que dependen de permisos NO se refrescaban
    // tras un login exitoso: el nombre de usuario se pintaba (eso lo hace el
    // llamador), pero "Productos"/"Compras"/"Nuevo" seguian con el `hidden` del
    // HTML. `actualizarCabeceraUsuario` solo se llamaba en el `catch`.
    actualizarCabeceraUsuario();
  } catch (error) {
    // ANTES este `catch` era `catch {}` y se comia hasta los TypeError, que es
    // como paso lo de `api.pedir`: el módulo exportaba la función pero no el
    // método, y la app Follow andando con el perfil sin cargar y sin que nadie
    // se enterara.
    //
    // Un error de autenticación ya lo maneja `api.js` (cierra la sesión). Lo
    // que se registra acá es todo lo demas.
    if (error?.status !== 401 && error?.status !== 403) {
      console.error('No se pudo cargar el perfil del usuario:', error);
    }

    actualizarCabeceraUsuario();
  }
}

/**
 * Intenta restaurar la sesión de una pestaña anterior.
 *
 * Sin storage (Safari privado, iframe) o sin refresh token, `restaurarSesion`
 * devuelve null y se muestra el login: es lo correcto, no un error a mostrar.
 */
export async function intentarRestaurar() {
  return restaurarSesion();
}

/**
 * Cierra la sesión, con confirmacion.
 */
export function cerrar() {
  const btnCancelar = crear('button', {
    clase: 'botón botón--suave',
    texto: 'Cancelar',
    atributos: { type: 'button' },
  });
  const btnConfirmar = crear('button', {
    clase: 'botón botón--peligro',
    texto: 'Cerrar sesión',
    atributos: { type: 'button' },
  });

  abrirDialogo({
    titulo: '¿Cerrar sesión?',
    mensaje: 'Se cerrara tu sesión actual. ¿Continuar?',
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
 * Muestra modal de login (para expiracion de token durante operación).
 * NO navega a login page - mantiene la vista actual en background.
 *
 * @param {Function} alExito - Callback cuando login exitoso
 */
export function mostrarLoginModal(alExito) {
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
    clase: 'botón botón--bloque',
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
 * `consumirOperacionPendiente` es un export con nombre de `api.js`, no un método
 * del objeto `api`: llamar `api.consumirOperacionPendiente()` es `undefined`
 * y tira un TypeError que el manejador de promesas no controladas de más abajo
 * convierte en un error rojo en la consola, sin relacion visible con lo que el
 * operador estaba haciendo.
 */
export async function reanudarOperacionPendiente() {
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

/**
 * Cierra la sesión en el servidor y vuelve al login.
 */
export function hacerLogout() {
  api.logout().finally(() => {
    setUsuario(null);
    pintarLogin();
  });
}

/**
 * El arranque de la app.
 *
 * Es lo Único que hace `main.js`: esperar la base, y decidir si se muestra el
 * login o se restaura la sesión.
 */
export async function arrancar() {
  const restaurada = await intentarRestaurar();

  if (restaurada && haySesion()) {
    setUsuario(restaurada.usuario ?? { role_name: restaurada.role_name });
    await cargarUsuario();
    cabecera.hidden = false;

    // El módulo: el guardado si el usuario puede usarlo, y si no, el que le
    // corresponde por rol.
    const guardado = moduloGuardado(estado.usuario);
    setModulo(guardado ?? moduloPorDefecto(estado.usuario));

    const destino = window.location.hash.replace('#', '');
    mostrarVista(destino && VISTAS_POR_NOMBRE[destino] ? destino : 'inicio');
    iniciarCampana();
    document.dispatchEvent(new CustomEvent('sesion:cambio'));
  } else {
    pintarLogin();
  }
}

// El registro de vistas, para el hash de la URL. Se importa aparte para no
// meter a `navegacion.js` (que importa todas las vistas) en el camino del
// login: sin sesión, el login tiene que cargar rápido.
const VISTAS_POR_NOMBRE = {
  inicio: true,
  escanear: true,
  inventario: true,
  movimientos: true,
  incidencias: true,
  consultas: true,
  productos: true,
  compras: true,
  auditoria: true,
  nuevo: true,
  usuarios: true,
};

// ---------------------------------------------------------------------------
// EL AVISO DE LA API
// ---------------------------------------------------------------------------
//
// `api.js` no puede navegar: no conoce la UI. Cuando el token se vence, avisa
// por estos callbacks.
//
// LA DIFERENCIA ENTRE LOS DOS
// ---------------------------
// `alExpirar` (401 en una petición cualquiera) VUELVE AL LOGIN: la sesión se
// fue y hay que empezar de cero.
//
// `alRequerirLogin` (401 en una petición CON la operación pendiente guardada)
// ABRE UN MODAL SIN CAMBIAR DE VISTA: el operador estaba llenando un
// formulario y perderlo por un token vencido es la clase de error que hace
// que deje de confiar en la app.
//
// `alRequerirLogin` se dispara desde `api.js` cuando hay una operación
// pendiente: es el único caso en el que el formulario que hay que preservar
// es el que está en pantalla.
configurar({
  alExpirar: () => {
    setUsuario(null);
    pintarLogin();
  },
  alRequerirLogin: () => {
    mostrarLoginModal(async (datos) => {
      setUsuario(datos.usuario ?? { role_name: datos.role_name });
      await cargarUsuario();
      cabecera.hidden = false;
      avisar(`Hola, ${estado.usuario.full_name ?? estado.usuario.email ?? ''}`.trim(), 'exito', 2500);
      // Reanudar operación pendiente
      await reanudarOperacionPendiente();
    });
  },
});
