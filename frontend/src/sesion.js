/**
 * La sesion: entrar, salir, restaurar, y el login que aparece cuando el
 * token se vence en mitad de una operacion.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * La sesion es un TEMA con sus propias reglas: el token vive en memoria, el
 * refresh en `sessionStorage`, hay un login que se abre SIN cambiar de vista
 * (para no perder el formulario que el operador estaba llenando) y una
 * operacion pendiente que se reanuda sola. Todo eso mezclado con el arranque
 * de la app era un archivo de 900 lineas donde no se sabia que era la sesion y
 * que era el arranque.
 *
 * EL LOGIN QUE NO NAVEGA
 * ---------------------
 * Cuando el token se vence en mitad de una operacion, la app NO tira al
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
  // Sin el `?? login`, se insertaba el objeto entero y el cuerpo de la pagina
  // quedaba con un literal "[object Object]" en vez del formulario. No lo
  // detecta ningun test de unidad: solo se ve corriendo la app.
  const login = vistaLogin({
    alEntrar: async (datos) => {
      setUsuario(datos.usuario ?? { role_name: datos.role_name });
      // Se leen los permisos reales del servidor, no los del rol del JWT: el
      // backend decide, y una copia aca se desincroniza en el primer cambio de
      // permisos.
      await cargarUsuario();
      cabecera.hidden = false;
      // La pantalla de inicio es el tablero: el operador ve el estado del
      // inventario antes de hacer nada. El escaner queda a un toque, en la
      // primera pestana de la izquierda.
      mostrarVista('inicio');
      // La campana de avisos de SLA arranca con la sesion: sin ella, el
      // pedido de conteo seria un 401 en cada arranque de la app.
      iniciarCampana();
      actualizarEstadoIA();
      avisar(`Hola, ${estado.usuario.full_name ?? estado.usuario.email ?? ''}`.trim(), 'exito', 2500);
      // Reanudar operación pendiente (si venía de expiración con refresh token válido)
      await reanudarOperacionPendiente();
    },
  });

  estado.vistaActual = login;
  reemplazar(contenido, login.elemento ?? login);

  // Tambien es una app arrancada: el login ES la pantalla. Sin esta marca, un
  // arranque lento que pasa los 12 segundos del vigilante reemplaza el login
  // por "La app no pudo arrancar".
  document.body.dataset.appLista = '1';
}

/**
 * Carga el perfil del usuario y sus permisos.
 */
export async function cargarUsuario() {
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
    setUsuario({
      ...(estado.usuario ?? {}),
      ...(perfil?.usuario ?? {}),
      permisos: perfil?.permisos ?? [],
    });
    // La lista de roles (nombres) es para selects de administracion, no permisos.
    if (roles?.roles) estado.usuario.roles = roles.roles;

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

/**
 * Intenta restaurar la sesion de una pestana anterior.
 *
 * Sin storage (Safari privado, iframe) o sin refresh token, `restaurarSesion`
 * devuelve null y se muestra el login: es lo correcto, no un error a mostrar.
 */
export async function intentarRestaurar() {
  return restaurarSesion();
}

/**
 * Cierra la sesion, con confirmacion.
 */
export function cerrar() {
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
 * Cierra la sesion en el servidor y vuelve al login.
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
 * Es lo UNICO que hace `main.js`: esperar la base, y decidir si se muestra el
 * login o se restaura la sesion.
 */
export async function arrancar() {
  const restaurada = await intentarRestaurar();

  if (restaurada && haySesion()) {
    setUsuario(restaurada.usuario ?? { role_name: restaurada.role_name });
    await cargarUsuario();
    cabecera.hidden = false;
    const destino = window.location.hash.replace('#', '');
    mostrarVista(destino && VISTAS_POR_NOMBRE[destino] ? destino : 'inicio');
    iniciarCampana();
  } else {
    pintarLogin();
  }
}

// El registro de vistas, para el hash de la URL. Se importa aparte para no
// meter a `navegacion.js` (que importa todas las vistas) en el camino del
// login: sin sesion, el login tiene que cargar rapido.
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
// `alExpirar` (401 en una peticion cualquiera) VUELVE AL LOGIN: la sesion se
// fue y hay que empezar de cero.
//
// `alRequerirLogin` (401 en una peticion CON la operacion pendiente guardada)
// ABRE UN MODAL SIN CAMBIAR DE VISTA: el operador estaba llenando un
// formulario y perderlo por un token vencido es la clase de error que hace
// que deje de confiar en la app.
//
// `alRequerirLogin` se dispara desde `api.js` cuando hay una operacion
// pendiente: es el unico caso en el que el formulario que hay que preservar
// es el que esta en pantalla.
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
