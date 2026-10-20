/**
 * Vista "Incidencias" — el reporte de problemas.
 *
 * QUE ES
 * ------
 * Un caso: "este equipo no prende", "el cliente no tiene internet". Es la
 * unidad de trabajo del dia a dia del soporte, y la vista tiene que ayudar a
 * responder tres preguntas, en este orden:
 *
 *   1. ¿Que se esta rompiendo? (la lista, ordenada por gravedad y por SLA)
 *   2. ¿Que caso es este y que le hicieron? (el detalle, con la linea de tiempo)
 *   3. ¿Que hago con el? (cambiar el estado, comentar, asignar)
 *
 * EL SLA A LA VISTA
 * -----------------
 * El plazo de respuesta y de resolucion se calculan en el servidor (son un
 * COMPROMISO con el cliente, no una regla del codigo) y llegan ya resueltos.
 * Aca solo se muestran: cuanto falta, o cuanto se paso.
 *
 * EL ORDEN DE LA LISTA
 * --------------------
 * Lo decide el backend, y es el que un operador necesita: las VENCIDAS
 * primero, y dentro de cada grupo, las mas graves y las mas antiguas. Reordenar
 * en el front seria duplicar la regla y terminar con dos criterios distintos.
 */

import { api } from '../api.js';
import { crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { campoTexto, campoSelect, resumenDeErrores } from '../formulario.js';
import { conTooltip } from '../tooltip.js';

const SEVERIDADES = [
  { valor: 'S1', texto: 'S1 · Critico (sin servicio)' },
  { valor: 'S2', texto: 'S2 · Alto (impacto serio)' },
  { valor: 'S3', texto: 'S3 · Medio (un equipo)' },
  { valor: 'S4', texto: 'S4 · Bajo (consulta)' },
];

const CATEGORIAS = [
  { valor: 'HARDWARE', texto: 'Hardware' },
  { valor: 'SOFTWARE', texto: 'Software' },
  { valor: 'NETWORK', texto: 'Red' },
  { valor: 'PERIPHERAL', texto: 'Periferico' },
  { valor: 'PHYSICAL', texto: 'Dano fisico' },
  { valor: 'ACCESS', texto: 'Accesos / permisos' },
  { valor: 'OTHER', texto: 'Otro' },
];

const ESTADOS = [
  { valor: 'OPEN', texto: 'Abierta' },
  { valor: 'IN_PROGRESS', texto: 'En curso' },
  { valor: 'WAITING', texto: 'En espera' },
  { valor: 'RESOLVED', texto: 'Resuelta' },
  { valor: 'CLOSED', texto: 'Cerrada' },
  { valor: 'CANCELLED', texto: 'Cancelada' },
];

/** El estado, con su insignia. */
function estadoDe(valor) {
  const encontrado = ESTADOS.find((e) => e.valor === valor);
  return {
    texto: encontrado?.texto ?? valor,
    clase:
      valor === 'OPEN'
        ? 'insignia--peligro'
        : valor === 'IN_PROGRESS'
          ? 'insignia--alerta'
          : valor === 'RESOLVED'
            ? 'insignia--instalado'
            : 'insignia--deposito',
  };
}

/** Un texto corto de "cuanto falta". */
function restante(incidencia) {
  const minutos = incidencia.minutos_para_resolver;
  if (minutos === null || minutos === undefined) return '';
  if (minutos < 0) {
    const horas = Math.floor(Math.abs(minutos) / 60);
    const mins = Math.round(Math.abs(minutos) % 60);
    return horas > 0 ? `vencido hace ${horas}h ${mins}m` : `vencido hace ${mins}m`;
  }
  const horas = Math.floor(minutos / 60);
  const mins = Math.round(minutos % 60);
  if (horas > 24) return `faltan ${Math.floor(horas / 24)} dias`;
  return horas > 0 ? `faltan ${horas}h ${mins}m` : `faltan ${mins}m`;
}

export function vistaIncidencias({ permisos = [] } = {}) {
  const raiz = crear('div');

  const puedeCrear = permisos.includes('incidents:create');
  const puedeGestionar = permisos.includes('incidents:manage');

  // --- Cabecera -------------------------------------------------------------
  const tarjetaCabecera = crear('section', { clase: 'tarjeta' });

  const encabezado = crear('div', { clase: 'flex-entre' });
  encabezado.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Incidencias' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Los problemas reportados y su estado. El plazo de cada caso sale del SLA de su severidad.',
        }),
      ],
    }),
  );

  if (puedeCrear) {
    const btnReportar = crear('button', {
      clase: 'boton',
      texto: 'Reportar incidencia',
      atributos: { type: 'button', onclick: () => abrirReporte() },
    });
    conTooltip(btnReportar, 'Levanta un caso nuevo con su plazo de SLA calculado.');
    encabezado.append(btnReportar);
  }

  // Los KPIs: lo que hay que mirar primero.
  const zonaKpis = crear('div', { clase: 'rejilla-kpi' });

  // --- Filtros --------------------------------------------------------------
  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por codigo, titulo o descripcion',
      autocomplete: 'off',
      spellcheck: 'false',
    },
  });

  const selectEstado = crear('select', { clase: 'campo__control' });
  selectEstado.append(crear('option', { texto: 'Todos los estados', atributos: { value: '' } }));
  for (const e of ESTADOS) {
    selectEstado.append(crear('option', { texto: e.texto, atributos: { value: e.valor } }));
  }

  const selectSeveridad = crear('select', { clase: 'campo__control' });
  selectSeveridad.append(crear('option', { texto: 'Todas las severidades', atributos: { value: '' } }));
  for (const s of SEVERIDADES) {
    selectSeveridad.append(crear('option', { texto: s.texto, atributos: { value: s.valor } }));
  }

  const chkVencidas = crear('input', {
    clase: 'campo__control',
    atributos: { type: 'checkbox' },
  });
  chkVencidas.style.width = 'auto';

  const etiquetaVencidas = crear('label', { clase: 'campo' });
  const textoVencidas = crear('span', { clase: 'campo__etiqueta', texto: 'Solo vencidas' });
  etiquetaVencidas.append(textoVencidas, chkVencidas);
  conTooltip(textoVencidas, 'Casos que ya pasaron su plazo de resolucion y siguen sin resolverse.');

  const etiquetaBusqueda = crear('label', { clase: 'campo' });
  const textoBusqueda = crear('span', { clase: 'campo__etiqueta', texto: 'Buscar' });
  etiquetaBusqueda.append(textoBusqueda, inputBuscar);
  conTooltip(textoBusqueda, 'Busqueda de texto completo: alcanza con un par de palabras del titulo.');

  const etiquetaEstado = crear('label', { clase: 'campo' });
  etiquetaEstado.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Estado' }),
    selectEstado,
  );

  const etiquetaSeveridad = crear('label', { clase: 'campo' });
  etiquetaSeveridad.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Severidad' }),
    selectSeveridad,
  );

  const rejillaFiltros = crear('div', { clase: 'rejilla-filtros' });
  rejillaFiltros.append(etiquetaBusqueda, etiquetaEstado, etiquetaSeveridad, etiquetaVencidas);

  tarjetaCabecera.append(encabezado, zonaKpis, rejillaFiltros);

  // --- Resultados -----------------------------------------------------------
  const zonaLista = crear('div');

  raiz.append(tarjetaCabecera, zonaLista);

  let peticionEnCurso = 0;

  async function cargar() {
    const numero = ++peticionEnCurso;

    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando incidencias…' }));

    try {
      const [datos, metricas] = await Promise.all([
        api.incidencias({
          busqueda: inputBuscar.value.trim() || undefined,
          estado: selectEstado.value || undefined,
          severidad: selectSeveridad.value || undefined,
          solo_vencidas: chkVencidas.checked || undefined,
          limite: 50,
        }),
        api.metricasIncidencias().catch(() => null),
      ]);

      if (numero !== peticionEnCurso) return;

      pintarKpis(metricas);
      pintarLista(datos.items);
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  function pintarKpis(metricas) {
    zonaKpis.replaceChildren();
    if (!metricas) return;

    const r = metricas.resumen ?? {};
    const tarjetas = [
      { etiqueta: 'Abiertas', valor: r.abiertas ?? 0, ayuda: 'Casos sin resolver, en cualquiera de sus estados.' },
      { etiqueta: 'Vencidas', valor: r.vencidas ?? 0, ayuda: 'Ya pasaron su plazo de resolucion y siguen abiertas.' },
      { etiqueta: 'Criticas', valor: r.criticas_abiertas ?? 0, ayuda: 'Severidad S1 abiertas: sin servicio.' },
      { etiqueta: 'Sin asignar', valor: r.sin_asignar ?? 0, ayuda: 'Nadie las tomo todavia.' },
      { etiqueta: 'Fuera de SLA', valor: r.fuera_de_sla ?? 0, ayuda: 'Se resolvieron despues del plazo.' },
      {
        etiqueta: 'MTTR',
        valor: r.mttr_horas !== null && r.mttr_horas !== undefined ? `${r.mttr_horas} h` : '—',
        ayuda: 'Horas promedio entre el reporte y la resolucion. El numero que le importa al responsable.',
      },
    ];

    for (const t of tarjetas) {
      const caja = crear('div', { clase: 'kpi', atributos: { tabindex: '0' } });
      caja.append(
        crear('div', { clase: 'kpi__valor', texto: String(t.valor) }),
        crear('div', { clase: 'kpi__etiqueta', texto: t.etiqueta }),
      );
      conTooltip(caja, t.ayuda);
      zonaKpis.append(caja);
    }
  }

  function pintarLista(incidencias) {
    if (!incidencias.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.ok, { tamano: 28 }),
        crear('p', { texto: 'No hay incidencias con esos filtros.' }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const i of incidencias) {
      const item = crear('li', { clase: 'lista__item' });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });

      const titulo = crear('div', { clase: 'lista__titulo' });
      titulo.append(
        crear('span', { clase: 'mono tenue pequeno', texto: `${i.code} · ` }),
        crear('span', { texto: i.title }),
      );

      const info = crear('div', { clase: 'lista__meta' });
      info.append(
        crear('span', {
          texto: `${i.severity} · ${estadoDe(i.status).texto}${
            i.assigned_to ? ' · Asignada' : ' · Sin asignar'
          }`,
        }),
      );

      const sla = crear('div', { clase: 'lista__meta mono' });
      const restanteTexto = restante(i);
      sla.textContent = restanteTexto || '';

      cuerpo.append(titulo, info, sla);

      // La insignia de estado.
      const estadoInfo = estadoDe(i.status);
      const insignia = crear('span', { clase: `insignia ${estadoInfo.clase}`, texto: estadoInfo.texto });
      conTooltip(insignia, `Estado: ${estadoInfo.texto}`);

      // La marca de SLA vencido: lo que hay que ver primero.
      const vencida = crear('span', {
        clase: `insignia ${i.vencido ? 'insignia--peligro' : 'insignia--transito'}`,
        texto: i.vencido ? 'Vencida' : 'En plazo',
      });
      conTooltip(
        vencida,
        i.vencido
          ? 'Paso su plazo de resolucion y sigue sin resolverse.'
          : `Dentro del plazo: ${restanteTexto || 'sin plazo calculado'}.`,
      );

      const btnVer = crear('button', {
        clase: 'boton boton--suave boton--chico',
        texto: 'Ver',
        atributos: { type: 'button', onclick: () => abrirDetalle(i) },
      });
      conTooltip(btnVer, 'Abre el caso con su linea de tiempo.');

      item.append(cuerpo, insignia, vencida, btnVer);
      lista.append(item);
    }

    zonaLista.replaceChildren(lista);
  }

  // --- Detalle --------------------------------------------------------------
  async function abrirDetalle(incidencia) {
    let detalle;
    try {
      detalle = await api.incidencia(incidencia.id);
    } catch (error) {
      avisar(`No se pudo abrir la incidencia: ${error.message}`, 'error');
      return;
    }
    return abrirDetalleDe(detalle, { puedeGestionar, alCambiar: cargar });
  }

  // --- Reportar una incidencia ----------------------------------------------
  async function abrirReporte() {
    const campoTitulo = campoTexto('Ej: El portatil del cliente Acme no prende', 'Titulo *', {
      requerido: true,
      descripcion: 'Una linea que diga que pasa. Es lo primero que se lee.',
    });

    const campoDescripcion = campoTexto('Ej: Al enchufarlo no enciende ni el led.', 'Descripcion', {
      descripcion: 'El detalle: cuando pasa, que se probo, que dice el cliente.',
    });

    const campoSeveridad = campoSelect({
      etiqueta: 'Severidad *',
      opciones: SEVERIDADES,
      requerido: true,
      vacio: 'Seleccionar…',
      descripcion: 'Define el plazo de respuesta y de resolucion (el SLA).',
    });
    campoSeveridad.control.value = 'S3';

    const campoCategoria = campoSelect({
      etiqueta: 'Categoria *',
      opciones: CATEGORIAS,
      requerido: true,
      vacio: 'Seleccionar…',
      descripcion: 'El tipo de problema. Sirve para ver patrones.',
    });
    campoCategoria.control.value = 'OTHER';

    const zonaResumen = crear('div');

    const form = crear('form', {
      clase: 'apilado',
      atributos: {
        onsubmit: async (e) => {
          e.preventDefault();
          await enviar();
        },
      },
    });

    form.append(
      zonaResumen,
      campoTitulo.campo,
      campoDescripcion.campo,
      campoSeveridad.campo,
      campoCategoria.campo,
    );

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Reportar incidencia',
      atributos: { type: 'submit' },
    });
    form.append(btnGuardar);

    let cerrarModal = () => {};

    async function enviar() {
      const titulo = campoTitulo.control.value.trim();
      const severidad = campoSeveridad.control.value;
      const categoria = campoCategoria.control.value;

      campoTitulo.limpiar();
      campoSeveridad.limpiar();
      campoCategoria.limpiar();
      zonaResumen.replaceChildren();

      if (!titulo) campoTitulo.marcar('El titulo es obligatorio.');
      if (!severidad) campoSeveridad.marcar('Elegi la severidad: define el SLA.');

      const resumen = resumenDeErrores([campoTitulo, campoSeveridad, campoCategoria]);
      if (resumen) {
        zonaResumen.replaceChildren(resumen);
        return;
      }

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Reportando…';
      try {
        const creada = await api.reportarIncidencia({
          title: titulo,
          description: campoDescripcion.control.value.trim() || undefined,
          severity: severidad,
          category: categoria,
        });
        avisar(`Incidencia ${creada.code} reportada.`, 'exito', 3000);
        cerrarModal(true);
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Reportar incidencia';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: 'Reportar incidencia',
      nota: 'El plazo de respuesta y de resolucion se calculan con el SLA de la severidad.',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [],
      avanzarAlCerrar: true,
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
      },
    });
  }

  inputBuscar.addEventListener('input', () => cargar());
  selectEstado.addEventListener('change', () => cargar());
  selectSeveridad.addEventListener('change', () => cargar());
  chkVencidas.addEventListener('change', () => cargar());

  cargar();
  return { elemento: raiz };
}


// ===========================================================================
// EL DETALLE DE UNA INCIDENCIA
// ===========================================================================
//
// Son funciones de MODULO y no de la vista, porque la campana de avisos las
// abre tambien: duplicar el modal seria dos lugares que se desincronizan.

/**
 * Las transiciones posibles desde el estado actual.
 *
 * Son las MISAS que acepta la base (trigger `incident_transicion_valida`).
 * No es una reimplementacion: es el subconjunto que tiene sentido ofrecer, y
 * si la base lo rechaza, el 409 con el motivo se muestra.
 */
export function transicionesDesde(estado) {
  const transiciones = {
    OPEN: [
      { valor: 'IN_PROGRESS', texto: 'En curso' },
      { valor: 'WAITING', texto: 'En espera' },
      { valor: 'CANCELLED', texto: 'Cancelada' },
    ],
    IN_PROGRESS: [
      { valor: 'WAITING', texto: 'En espera' },
      { valor: 'RESOLVED', texto: 'Resuelta' },
      { valor: 'CANCELLED', texto: 'Cancelada' },
    ],
    WAITING: [
      { valor: 'IN_PROGRESS', texto: 'En curso' },
      { valor: 'RESOLVED', texto: 'Resuelta' },
      { valor: 'CANCELLED', texto: 'Cancelada' },
    ],
    RESOLVED: [
      { valor: 'CLOSED', texto: 'Cerrada' },
      { valor: 'IN_PROGRESS', texto: 'Reabrir' },
    ],
    CLOSED: [],
    CANCELLED: [],
  };
  return transiciones[estado] ?? [];
}

/** Que paso, en palabras. */
function descripcionDeEvento(evento) {
  switch (evento.event_type) {
    case 'CREATED':
      return 'Incidencia reportada';
    case 'STATUS_CHANGED':
      return `Estado: ${estadoDe(evento.from_status).texto} → ${estadoDe(evento.to_status).texto}`;
    case 'ASSIGNED':
      return 'Se asigno un responsable';
    case 'SEVERITY_CHANGED':
      return `Cambio de severidad a ${evento.payload?.severidad ?? '—'}`;
    case 'ESCALATED':
      return 'Escalamiento automatico por SLA vencido';
    case 'COMMENT':
      return 'Nota';
    default:
      return evento.event_type;
  }
}

/**
 * El modal del detalle de una incidencia.
 *
 * @param {object} detalle           La incidencia, con su linea de tiempo.
 * @param {object} [opciones]
 * @param {boolean} [opciones.puedeGestionar]  Si el usuario puede cambiar el
 *                                             estado y comentar.
 * @param {Function} [opciones.alCambiar]       Se llama despues de un cambio,
 *                                             para refrescar la lista.
 */
export function abrirDetalleDe(detalle, { puedeGestionar = false, alCambiar } = {}) {
  const cuerpo = crear('div', { clase: 'incidencia' });

  // Los datos.
  const datos = crear('div', { clase: 'incidencia__datos' });
  datos.append(
    crear('div', { clase: 'incidencia__titulo', texto: detalle.title }),
    crear('div', { clase: 'incidencia__meta mono', texto: `${detalle.code} · ${detalle.severity}` }),
  );
  if (detalle.description) {
    datos.append(crear('p', { clase: 'incidencia__descripcion', texto: detalle.description }));
  }

  // El SLA, con lo que falta.
  const sla = crear('div', { clase: 'incidencia__sla' });
  const restanteTexto = restante(detalle);
  sla.append(
    crear('div', {
      clase: `insignia ${detalle.vencido ? 'insignia--peligro' : 'insignia--deposito'}`,
      texto: detalle.vencido ? 'Fuera de SLA' : 'En plazo',
    }),
    crear('span', {
      clase: 'mono pequeno',
      texto: restanteTexto ? `Resolucion: ${restanteTexto}` : '',
    }),
  );
  if (detalle.first_response_at) {
    sla.append(
      crear('span', {
        clase: 'mono pequeno',
        texto: `Respondida: ${new Date(detalle.first_response_at).toLocaleString('es-AR')}`,
      }),
    );
  }
  datos.append(sla);
  cuerpo.append(datos);

  // La linea de tiempo.
  const linea = crear('div', { clase: 'incidencia__linea' });
  linea.append(crear('h3', { clase: 'tarjeta__titulo', texto: 'Historial' }));

  if (!detalle.eventos?.length) {
    linea.append(crear('p', { clase: 'tenue', texto: 'Sin movimientos.' }));
  }

  for (const evento of detalle.eventos ?? []) {
    const fila = crear('div', { clase: 'evento' });
    const cuando = crear('span', {
      clase: 'evento__fecha mono',
      texto: new Date(evento.created_at).toLocaleString('es-AR'),
    });
    const que = crear('span', { clase: 'evento__texto' });
    que.textContent = descripcionDeEvento(evento);
    if (evento.note) {
      que.append(crear('span', { clase: 'evento__nota', texto: evento.note }));
    }
    fila.append(cuando, que);
    linea.append(fila);
  }
  cuerpo.append(linea);

  // Las acciones.
  if (puedeGestionar) {
    cuerpo.append(accionesDe(detalle, alCambiar));
  }

  const btnCerrar = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Cerrar',
    atributos: { type: 'button' },
  });

  let cerrar = () => {};

  cerrar = abrirDialogo({
    titulo: `Incidencia ${detalle.code}`,
    cuerpo: [cuerpo],
    clase: 'modal--formulario',
    acciones: [btnCerrar],
    alAbrir: (_modal, cerrarModal) => {
      cerrar = cerrarModal;
      btnCerrar.addEventListener('click', cerrarModal);
    },
  });

  return cerrar;
}

/**
 * Las acciones del detalle: cambiar el estado y comentar.
 */
function accionesDe(detalle, alCambiar) {
  const zona = crear('div', { clase: 'incidencia__acciones' });

  // Cambiar el estado: solo las transiciones que la base permite desde aca.
  const transiciones = transicionesDesde(detalle.status);
  if (transiciones.length) {
    const selectEstado = crear('select', { clase: 'campo__control' });
    selectEstado.append(
      crear('option', { texto: 'Cambiar estado a…', atributos: { value: '' } }),
    );
    for (const t of transiciones) {
      selectEstado.append(crear('option', { texto: t.texto, atributos: { value: t.valor } }));
    }

    const btnCambiar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cambiar estado',
      atributos: { type: 'button' },
    });

    btnCambiar.addEventListener('click', async () => {
      const nuevo = selectEstado.value;
      if (!nuevo) return avisar('Elegi el estado nuevo.', 'alerta');

      btnCambiar.disabled = true;
      try {
        const actualizado = await api.cambiarEstadoIncidencia(detalle.id, { estado: nuevo });
        avisar('Estado actualizado.', 'exito', 2500);
        // El modal se refresca con el caso nuevo: el que esta en pantalla
        // ya no es el mismo.
        cerrarYReabrir(actualizado, alCambiar);
      } catch (error) {
        avisar(error.message, 'error');
        btnCambiar.disabled = false;
      }
    });

    zona.append(
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Estado' }), selectEstado],
      }),
    );
    zona.append(btnCambiar);
  }

  // Comentar.
  const campoNota = campoTexto('Ej: Se cambio el disco y quedo funcionando.', 'Nota', {
    type: 'text',
  });

  const btnComentar = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Comentar',
    atributos: { type: 'button' },
  });

  btnComentar.addEventListener('click', async () => {
    const nota = campoNota.control.value.trim();
    if (!nota) return avisar('Escribi la nota.', 'alerta');
    btnComentar.disabled = true;
    try {
      const actualizado = await api.comentarIncidencia(detalle.id, nota);
      avisar('Nota agregada.', 'exito', 2500);
      campoNota.control.value = '';
      cerrarYReabrir(actualizado, alCambiar);
    } catch (error) {
      avisar(error.message, 'error');
      btnComentar.disabled = false;
    }
  });

  zona.append(campoNota.campo, btnComentar);
  return zona;
}

/**
 * Cierra el modal del detalle y lo reabre con los datos nuevos.
 *
 * Es mas simple que parchar el DOM: el modal se reconstruye entero con la
 * linea de tiempo al dia.
 */
function cerrarYReabrir(detalle, alCambiar) {
  const modales = document.querySelectorAll('.modal');
  const ultimo = modales[modales.length - 1];
  const botonCerrar = ultimo?.querySelector('.modal__acciones button');
  botonCerrar?.click();

  // El cierre del modal es asincrono (la animacion): se espera un cambio de
  // cuadro antes de abrir el nuevo, o se apilan dos.
  setTimeout(() => {
    abrirDetalleDe(detalle, { puedeGestionar: true, alCambiar });
    alCambiar?.();
  }, 120);
}
