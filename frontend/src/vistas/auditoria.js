/**
 * Vista "Auditoría" — la traza de lo que paso en el sistema.
 *
 * QUE MUESTRA, Y POR QUE EN UNA SOLA LISTA
 * -----------------------------------------
 * Una línea de tiempo cronologica que mezcla dos cosas que en el sistema viven
 * en tablas distintas pero que quien audita lee juntas:
 *
 *   - los MOVIMIENTOS de inventario: quien saco que equipo, de que sede a que
 *     sede, cuando (de `movement_history`);
 *   - los EVENTOS del sistema: logins, altas y cambios de usuarios (de
 *     `audit_logs`).
 *
 * Separarlas en dos pantallas obligaria al auditor a saber en cual buscar, y la
 * pregunta real ("que paso ayer entre las 3 y las 5") cruza las dos.
 *
 * LA INSIGNIA DE Categoría NO ES DECORATIVA
 * -----------------------------------------
 * El borde izquierdo de cada fila dice si es inventario o sistema. Es lo que
 * permite escanear la lista en diagonal y ver de un golpe dónde están los
 * movimientos de equipos sin leer cada línea.
 *
 * No hay innerHTML: los nombres de usuario y de sede los digita gente.
 */

import { api } from '../api.js';
import { crearPaginacion } from '../paginacion.js';
import { crear, icono, ICONOS } from '../dom.js';
import { formatearFecha } from './inventario.js';

/** El color y el texto de cada categoría de evento. */
const CATEGORIAS = {
  inventario: { texto: 'Inventario', clase: 'insignia--deposito' },
  sistema: { texto: 'Sistema', clase: 'insignia--transito' },
};

export function vistaAuditoria({ permisos = [] } = {}) {
  const raiz = crear('div');

  // --- Filtros --------------------------------------------------------------
  const tarjetaFiltros = crear('div', { clase: 'tarjeta' });

  const cabecera = crear('div', { clase: 'flex-entre' });
  cabecera.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Auditoria' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Quien hizo que y cuando. Movimientos de equipos y eventos del sistema.',
        }),
      ],
    }),
  );

  const btnActualizar = crear('button', {
    clase: 'botón botón--suave',
    texto: 'Actualizar',
    atributos: { type: 'button', onclick: () => recargar() },
  });
  cabecera.append(btnActualizar);

  // Las cifras del encabezado. Se llenan después de cargar: si van vacías y
  // luego aparecen, el layout no salta porque el contenedor ya tiene su altura.
  const zonaResumen = crear('div', { clase: 'rejilla-kpi', estilo: 'margin-top:1rem' });

  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por serial, usuario, sede u observacion',
      autocomplete: 'off',
    },
  });

  const selectCategoria = crear('select', { clase: 'campo__control' });
  selectCategoria.append(
    crear('option', { texto: 'Todo', atributos: { value: '' } }),
    crear('option', { texto: 'Movimientos de inventario', atributos: { value: 'inventario' } }),
    crear('option', { texto: 'Eventos del sistema', atributos: { value: 'sistema' } }),
  );

  const campoDesde = crear('input', {
    clase: 'campo__control',
    atributos: { type: 'date' },
  });
  const campoHasta = crear('input', {
    clase: 'campo__control',
    atributos: { type: 'date' },
  });

  const filaFiltros = crear('div', { clase: 'rejilla-filtros', estilo: 'margin-top:1rem' });
  filaFiltros.append(
    crear('label', {
      clase: 'campo',
      hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Buscar' }), inputBuscar],
    }),
    crear('label', {
      clase: 'campo',
      hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Tipo' }), selectCategoria],
    }),
    crear('label', {
      clase: 'campo',
      hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Desde' }), campoDesde],
    }),
    crear('label', {
      clase: 'campo',
      hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Hasta' }), campoHasta],
    }),
  );

  tarjetaFiltros.append(cabecera, zonaResumen, filaFiltros);

  // --- Resultados -----------------------------------------------------------
  const zonaLista = crear('div');

  // LA Paginación (el componente compartido)
  let pagina = 0;
  let POR_PAGINA = 50;
  let total = 0;
  let peticionEnCurso = 0;

  const paginacion = crearPaginacion({
    total: 0,
    limite: POR_PAGINA,
    pagina,
    etiqueta: 'eventos',
    alCambiarPagina: (nueva) => {
      pagina = Math.max(0, nueva);
      cargar();
    },
    alCambiarLimite: (nuevo) => {
      POR_PAGINA = nuevo;
      pagina = 0;
      cargar();
    },
  });

  raiz.append(tarjetaFiltros, zonaLista, paginacion.elemento);

  async function cargar() {
    const numero = ++peticionEnCurso;
    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando auditoría…' }));

    const params = {
      limite: POR_PAGINA,
      offset: pagina * POR_PAGINA,
    };
    const busqueda = inputBuscar.value.trim();
    if (busqueda) params.busqueda = busqueda;
    if (selectCategoria.value) params.categoria = selectCategoria.value;
    // Las fechas se mandan como fin/inicio del día en ISO. `new Date('2026-01-01')`
    // es medianoche UTC, que en una zona negativa cae el día anterior: se
    // construye con la hora explicita para no correr un día el rango.
    if (campoDesde.value) params.desde = `${campoDesde.value}T00:00:00`;
    if (campoHasta.value) params.hasta = `${campoHasta.value}T23:59:59`;

    try {
      const datos = await api.auditoria(params);
      if (numero !== peticionEnCurso) return;
      total = datos.total;
      pintarLista(datos.items);
      pintarPaginacion();
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  async function cargarResumen() {
    try {
      const r = await api.auditoriaResumen();
      zonaResumen.replaceChildren(
        tarjetaKpi(r.movimientos_hoy, 'Movimientos hoy'),
        tarjetaKpi(r.movimientos_semana, 'Está semana'),
        tarjetaKpi(r.logins_semana, 'Ingresos (7 días)'),
        tarjetaKpi(r.movimientos_totales, 'Movimientos totales'),
      );
    } catch {
      // El resumen es contexto, no el dato principal. Si falla, la lista sigue
      // siendo útil: no se avisa ni se bloquea nada.
      zonaResumen.replaceChildren();
    }
  }

  function recargar() {
    pagina = 0;
    cargar();
    cargarResumen();
  }

  function tarjetaKpi(valor, etiqueta) {
    const tarjeta = crear('div', { clase: 'kpi' });
    tarjeta.append(
      crear('div', { clase: 'kpi__valor tabular', texto: String(valor ?? 0) }),
      crear('div', { clase: 'kpi__etiqueta', texto: etiqueta }),
    );
    return tarjeta;
  }

  function pintarLista(items) {
    if (!items.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.buscar, { tamano: 28 }),
        crear('p', { texto: 'No hay eventos que coincidan con los filtros.' }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const evento of items) {
      lista.append(filaEvento(evento));
    }

    zonaLista.replaceChildren(lista);
  }

  function filaEvento(evento) {
    const categoria = CATEGORIAS[evento.categoria] ?? { texto: evento.categoria, clase: '' };

    const item = crear('li', { clase: 'lista__item' });

    const cuerpo = crear('div', { clase: 'lista__cuerpo' });

    // Título: la accion. Debajo, que objeto y quien.
    const titulo = crear('div', { clase: 'flex-entre' });
    titulo.append(
      crear('div', { clase: 'lista__titulo', texto: evento.accion }),
      crear('span', {
        clase: `insignia ${categoria.clase}`,
        texto: categoria.texto,
      }),
    );
    cuerpo.append(titulo);

    // El objeto: el serial con su equipo para movimientos, la tabla para
    // eventos de sistema.
    if (evento.categoria === 'inventario') {
      const equipo = [evento.marca, evento.modelo].filter(Boolean).join(' ');
      cuerpo.append(
        crear('div', {
          clase: 'lista__meta mono',
          texto: evento.serial_number ?? '—',
        }),
      );
      if (equipo) cuerpo.append(crear('div', { clase: 'lista__meta', texto: equipo }));

      // El recorrido: de dónde a dónde. Es el corazon de la trazabilidad.
      const recorrido = [evento.origen, evento.destino].filter(Boolean).join(' → ');
      if (recorrido) {
        cuerpo.append(crear('div', { clase: 'lista__meta', texto: recorrido }));
      }

      // Un escaneo fuera del radio de la sede se marca. No es un error: es el
      // dato que justifica una revisión después.
      if (evento.geo_valida === false) {
        cuerpo.append(
          crear('span', { clase: 'insignia insignia--peligro', texto: 'Fuera de sede' }),
        );
      }
    } else {
      cuerpo.append(
        crear('div', { clase: 'lista__meta', texto: `Sobre ${evento.subtipo ?? '—'}` }),
      );
    }

    if (evento.observaciones) {
      cuerpo.append(
        crear('div', { clase: 'lista__meta tenue pequeno', texto: evento.observaciones }),
      );
    }

    // El actor y la fecha. Van juntos porque la pregunta "quien y cuando" se
    // responde de una sola lectura.
    const pie = crear('div', { clase: 'lista__meta' });
    pie.append(
      crear('span', { clase: 'tabular', texto: formatearFecha(evento.fecha) }),
      crear('span', { texto: ' · ' }),
      crear('span', { texto: evento.actor ?? 'Sistema' }),
    );
    cuerpo.append(pie);

    item.append(cuerpo);

    // Un movimiento de inventario lleva a la ficha del equipo: la auditoría se
    // usa para INVESTIGAR, y el paso siguiente casi siempre es abrir el equipo.
    if (evento.categoria === 'inventario' && evento.device_id) {
      item.append(
        crear('button', {
          clase: 'botón botón--suave',
          texto: 'Ver equipo',
          atributos: {
            type: 'button',
            onclick: () => {
              document.dispatchEvent(
                new CustomEvent('auditoria:abrir-equipo', { detail: evento.device_id }),
              );
            },
          },
        }),
      );
    }

    return item;
  }

  /**
   * La barra de paginación: el COMPONENTE compartido, para que todas las
   * tablas de la app se comporten igual (los botones, el "página 3 de 15" y
   * la cantidad por página).
   */
  function pintarPaginacion() {
    paginacion.actualizar({ total, limite: POR_PAGINA, pagina });
  }

  // La búsqueda y los selects reinician la página: si no, pedir la página 4 con
  // un filtro nuevo devuelve vacío y parece que no hay resultados.
  inputBuscar.addEventListener('input', () => {
    pagina = 0;
    cargar();
  });
  selectCategoria.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });
  campoDesde.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });
  campoHasta.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });

  cargar();
  cargarResumen();
  return { elemento: raiz };
}
