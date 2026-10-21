/**
 * Punto de entrada de la app.
 *
 * QUE HACE ESTE ARCHIVO
 * ----------------------
 * Tres cosas, y ninguna mas:
 *
 *   1. Registrar el service worker.
 *   2. Conectar los eventos globales (las pestanas, el logout, el modo
 *      exterior, el salto a la ficha de un equipo).
 *   3. Arrancar.
 *
 * TODO LO DEMAS VIVE EN SU MODULO
 * --------------------------------
 *   - `navegacion.js`: el registro de vistas y el ciclo de vida de cada una.
 *   - `sesion.js`: entrar, salir, restaurar, y el login que no navega.
 *   - `cabecera.js`: quien esta conectado, el pie, el modo exterior.
 *   - `estado.js`: el estado compartido (usuario, vista actual).
 *   - `build.js`: el sello de build y la deteccion de una version nueva.
 *   - `campana.js`: los avisos de SLA.
 *
 * POR QUE
 * -------
 * Con el modulo de incidencias, `main.js` paso de 700 lineas a un archivo
 * donde no se sabia que era el arranque y que era la sesion. El sintoma de
 * esa mezcla no es un bug: es que cambiar una cosa obliga a leer todo lo
 * demas, y nadie lo hace.
 */

import { verificarVersionDelServidor } from './build.js';
import { iniciarChipUsuario, iniciarModoExterior, iniciarSelectorModulo, inicializarFooter } from './cabecera.js';
import { iniciarCampana } from './campana.js';
import { aplicarIdiomaGuardado, iniciarSelectorIdioma } from './idioma.js';
import { mostrarVista } from './navegacion.js';
import { arrancar, cerrar } from './sesion.js';

// ---------------------------------------------------------------------------
// Service worker
// ---------------------------------------------------------------------------

/**
 * Registra el service worker.
 *
 * SOLO en produccion y SOLO sobre HTTPS o localhost. En desarrollo registra
 * lo que no se necesita: el SW cachea el bundle viejo y uno edita el CSS, ve
 * la version anterior y no entiende por que. El sintoma clasico de un SW en
 * desarrollo.
 */
function registrarServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  if (!window.isSecureContext) return;

  // En DESARROLLO no se registra, y se decide por el hostname y no por
  // `import.meta.env`: no hay bundler (los modulos ES se cargan nativos), asi
  // que `import.meta.env` no existe y accederlo lanzaria.
  //
  // Sin esto, el service worker cachea el bundle viejo y uno edita el CSS, ve
  // la version anterior y no entiende por que.
  const esDesarrollo = /^(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$/.test(
    window.location.hostname,
  );

  // El chequeo de version SI corre en desarrollo: son dos problemas distintos.
  // El service worker es para el modo offline; el sello es para que un
  // navegador con la cache vieja se actualice solo. En desarrollo el archivo
  // no existe y la peticion falla sin consecuencia.
  verificarVersionDelServidor();

  if (esDesarrollo) return;

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Sin service worker la app funciona igual: pierde el modo offline, nada
      // mas. No se avisa porque seria un ruido constante en desarrollo.
    });

    // Actualizar el service worker apenas se abre la pagina.
    //
    // `registration.update()` pide la version nueva del SW. Sin esto, el
    // navegador no vuelve a mirar el `/sw.js` hasta que se cierren TODAS las
    // pestanas del navegador: un deploy puede tardar horas en llegar.
    navigator.serviceWorker.ready
      .then((registration) => registration.update())
      .catch(() => {
        // Si la actualizacion falla, se sigue con la version que hay.
      });
  });
}

// ---------------------------------------------------------------------------
// Eventos globales
// ---------------------------------------------------------------------------

const cabecera = document.getElementById('cabecera');

for (const boton of cabecera.querySelectorAll('[data-vista]')) {
  boton.addEventListener('click', () => mostrarVista(boton.dataset.vista));
}

document.getElementById('btn-salir')?.addEventListener('click', cerrar);

// La auditoría puede saltar a la ficha de un equipo. La vista dispara el evento
// y en vez de llamar a `mostrarVista` directamente: `mostrarVista` vive en este
// archivo y las vistas lo importan de arriba hacia abajo — importarlo desde
// `auditoria.js` cerraría un ciclo de módulos. Un evento de documento desacopla
// las dos capas sin que la vista conozca la navegación.
document.addEventListener('auditoria:abrir-equipo', (evento) => {
  mostrarVista('dispositivo', { deviceId: evento.detail });
});

// EL IDIOMA
// ---------
// Se aplica ANTES de arrancar: si se hiciera despues, la primera pantalla
// (el login) apareceria en espanol y saltaria al ingles un segundo despues.
//
// El boton recarga la app: las vistas ya pintadas tendrian textos en el
// idioma viejo, y traducirlos "en vivo" seria recorrer la pantalla entera.
aplicarIdiomaGuardado();
iniciarSelectorIdioma(() => {
  window.location.reload();
});

// Botón modo exterior / alto contraste (para sol directo, guantes, accesibilidad)
iniciarModoExterior();

// El chip de usuario: "Mi cuenta" (cambiar la propia contraseña).
iniciarChipUsuario();

// El selector de modulo, en la cabecera. Solo aparece para quien puede usar
// los dos modulos.
iniciarSelectorModulo();

// Una vista puede pedir un salto a otra ("Ver todos" en el tablero). Se usa un
// evento de documento y no un import de `navegacion.js`: importarla desde una
// vista crearia un ciclo (navegacion importa las vistas, y las vistas
// importan navegacion). Con ESM, un ciclo no rompe, pero deja el binding en
// `undefined` en el primer arranque, sin error que lo diga.
document.addEventListener('app:ir-a', (evento) => {
  const destino = evento.detail?.vista;
  // Los filtros viajan con el salto: un KPI del tablero abre la vista YA
  // filtrada (los equipos "en deposito", las incidencias "abiertas"...).
  if (destino) mostrarVista(destino, { filtros: evento.detail?.filtros ?? null });
});

// ---------------------------------------------------------------------------
// Arranque
// ---------------------------------------------------------------------------

// Inicializar footer al arrancar
inicializarFooter();

arrancar();
