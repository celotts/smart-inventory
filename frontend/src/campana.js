/**
 * La campana de avisos de SLA.
 *
 * QUE ES
 * ------
 * Un SLA que nadie recibe es un SLA de papel. El backend calcula quien lo
 * incumple y escala los casos vencidos, pero si eso no LLEGA a la pantalla
 * del operador, el trabajo del barrido no sirve de nada: el operador se
 * entera del problema cuando el cliente llama.
 *
 * POR QUE UN POLLING Y NO UN WEBSOCKET
 * ------------------------------------
 * Un websocket mantiene una conexión abierta por operador, y en un depósito
 * con mala cobertura eso es una conexión que se cae y se re-establece. El
 * polling cada 30 segundos es una petición chica que se reintenta sola, y
 * con la app en segundo plano no cuesta nada.
 *
 * Y el badge se actualiza AL VOLVER A LA APP: el `visibilitychange` es el
 * evento que dice "el operador volvio", y es el momento de preguntar de nuevo.
 */

import { api } from './api.js';
import { crear, icono, ICONOS } from './dom.js';
import { abrirDialogo } from './dialogo.js';
import { conTooltip } from './tooltip.js';

const INTERVALO_MS = 30000;

/** El estado del módulo: el botón, el contador y el timer. */
let boton = null;
let contador = null;
let timer = null;
let iniciado = false;

/**
 * Pinta la campana y la conecta. Se llama una vez, al arrancar la app.
 */
export function iniciarCampana() {
  if (boton) return;

  boton = document.getElementById('btn-campana');
  contador = document.getElementById('campana-contador');
  if (!boton) return;

  boton.addEventListener('click', () => abrirAvisos());

  conTooltip(boton, 'Avisos de SLA: casos por vencer, vencidos y escalados.');

  // El badge se pregunta al volver a la app: si el operador estuvo en otra
  // pestaña veinte minutos, el número que ve tiene que ser el de ahora.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') actualizarContador();
  });

  iniciado = true;
  actualizarContador();
  timer = setInterval(actualizarContador, INTERVALO_MS);
}

/**
 * Pide el número de avisos sin leer y lo pinta.
 *
 * Un error de red NO puede romper la app: la campana es un extra, y si
 * falla se oculta y listo. El próximo polling lo reintenta.
 */
async function actualizarContador() {
  if (!boton) return;

  try {
    const datos = await api.pedir('/incidents/notifications/count');
    const noLeidos = datos?.no_leidos ?? 0;

    if (contador) {
      contador.textContent = noLeidos > 99 ? '99+' : String(noLeidos);
      contador.hidden = noLeidos === 0;
    }
    boton.hidden = false;
    boton.setAttribute(
      'aria-label',
      noLeidos === 0 ? 'Avisos de SLA (ninguno)' : `Avisos de SLA (${noLeidos} sin leer)`,
    );
  } catch {
    // Sin conexión con la API: la campana se oculta y el próximo intento
    // la vuelve a mostrar. Un badge con un número viejo es peor que no
    // tener badge.
    boton.hidden = true;
  }
}

/**
 * Abre la lista de avisos.
 */
async function abrirAvisos() {
  let avisos = [];
  try {
    avisos = await api.pedir('/incidents/notifications');
  } catch (error) {
    // El error ya se avisa con `avisar()`.
    return;
  }

  const cuerpo = crear('div', { clase: 'avisos-lista' });

  if (!avisos.length) {
    cuerpo.append(
      crear('p', { clase: 'tenue', texto: 'No hay avisos. Todos los casos están en plazo.' }),
    );
  }

  for (const aviso of avisos) {
    const fila = crear('button', {
      clase: `aviso-fila${aviso.read_at ? '' : ' aviso-fila--nuevo'}`,
      atributos: { type: 'button' },
    });

    fila.append(
      crear('span', {
        clase: `aviso-fila__icono ${aviso.type === 'SLA_ESCALATED' ? 'aviso-fila__icono--alerta' : ''}`,
        hijos: [icono(aviso.type === 'SLA_ESCALATED' ? ICONOS.error : ICONOS.relojito, { tamano: 16 })],
      }),
    );

    const cuerpoFila = crear('span', { clase: 'aviso-fila__cuerpo' });
    cuerpoFila.append(
      crear('span', { clase: 'aviso-fila__titulo', texto: aviso.title }),
      crear('span', { clase: 'aviso-fila__mensaje', texto: aviso.message || '' }),
      crear('span', {
        clase: 'aviso-fila__fecha mono',
        texto: new Date(aviso.created_at).toLocaleString('es-AR'),
      }),
    );
    fila.append(cuerpoFila);

    // Al tocar el aviso, se marca como leído y se abre el caso.
    fila.addEventListener('click', async () => {
      try {
        await api.pedir(`/incidents/notifications/${aviso.id}/leer`, { metodo: 'POST' });
      } catch {
        // Si no se puede marcar como leído, el aviso se abre igual: el
        // operador tiene que ver el caso, el "leído" es cosmetico.
      }
      actualizarContador();
      cerrar();
      mostrarIncidencia(aviso.incident_id);
    });

    cuerpo.appendChild(fila);
  }

  const btnCerrar = crear('button', {
    clase: 'botón botón--suave',
    texto: 'Cerrar',
    atributos: { type: 'button' },
  });

  let cerrar = () => {};

  cerrar = abrirDialogo({
    titulo: 'Avisos de SLA',
    nota: 'Casos por vencer, vencidos y escalados. El plazo sale del SLA de cada severidad.',
    cuerpo: [cuerpo],
    clase: 'modal--formulario',
    acciones: [btnCerrar],
    alAbrir: (_modal, cerrarModal) => {
      cerrar = cerrarModal;
      btnCerrar.addEventListener('click', cerrarModal);
    },
  });
}

/**
 * Abre el detalle de una incidencia (la misma vista de la lista de
 * incidencias, en un modal).
 *
 * Se importa en el momento y no arriba del archivo: la campana y la vista
 * de incidencias se usan siempre juntos, y un import estatico los ataria
 * (y con el, el módulo de incidencias se cargaria en toda app, incluso sin
 * el permiso para verlo).
 */
async function mostrarIncidencia(incidenciaId) {
  const detalle = await api.incidencia(incidenciaId).catch(() => null);
  if (!detalle) return;

  const { abrirDetalleDe } = await import('./vistas/incidencias.js');
  abrirDetalleDe(detalle);
}

/** Para las pruebas y para el cierre limpio. */
export function detenerCampana() {
  if (timer) clearInterval(timer);
  timer = null;
  iniciado = false;
}
