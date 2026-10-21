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
import { campoTexto, campoSelect, campoArea, resumenDeErrores } from '../formulario.js';
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

export function vistaIncidencias({ permisos = [], filtros } = {}) {
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

  // --- La bandeja: el trabajo sin dueno -------------------------------------
  //
  // QUE ES LA BANDEJA
  // ------------------
  // Los casos reportados que NADIE TOMO todavia. No es un filtro mas: es el
  // trabajo que se pierde. Por eso es la vista con la que arranca la
  // pantalla: el tecnico tiene que ver primero lo que nobody esta atendiendo.
  //
  // LAS TRES VISTAS
  // ---------------
  // - Bandeja: lo que nadie tomo. Se vacia con "Tomar el caso".
  // - Mis casos: lo que ya tengo asignado y sigue abierto.
  // - Todas: todo, para el supervisor.
  const VISTAS_CASOS = [
    { clave: 'bandeja', texto: 'Bandeja' },
    { clave: 'mias', texto: 'Mis casos' },
    { clave: 'todas', texto: 'Todas' },
  ];
  // La vista con la que se llega desde el tablero decide el segmento.
  //
  // - Si se pidio la BANDEJA, se cae en la bandeja.
  // - Si se pidio "mis casos", se cae en mis casos.
  // - Con CUALQUIER otro filtro (las vencidas, las criticas, las abiertas),
  //   se cae en "todas": la lista tiene que ser EXACTAMENTE la que se pidio.
  //   Filtrar la bandeja con "vencidas" daria una lista vacia, y el operador
  //   pensaria que no hay casos.
  let vistaCasos = 'bandeja';
  if (filtros?.mias) vistaCasos = 'mias';
  else if (filtros?.sin_atender) vistaCasos = 'bandeja';
  else if (filtros) vistaCasos = 'todas';

  // EL FILTRO DE LLEGADA SOLO APLICA A LA PRIMERA CARGA
  //
  // Si el filtro del tablero (por ejemplo "sin atender") se quedara pegado
  // para siempre, cambiar de vista a "Mis casos" daria la interseccion de los
  // dos filtros: casi siempre, una lista vacia. El filtro con el que se llega
  // es para la primera pantalla; despues, manda el segmento.
  let primerCarga = true;

  const segmentado = crear('div', { clase: 'segmented-control' });
  const botonesSegmento = [];
  for (const v of VISTAS_CASOS) {
    const boton = crear('button', {
      clase: `segmento${v.clave === vistaCasos ? ' segmento--activo' : ''}`,
      texto: v.texto,
      atributos: { type: 'button' },
    });
    boton.addEventListener('click', () => {
      vistaCasos = v.clave;
      for (const b of botonesSegmento) {
        b.classList.toggle('segmento--activo', b.dataset.vista === vistaCasos);
      }
      cargar();
    });
    boton.dataset.vista = v.clave;
    botonesSegmento.push(boton);
    segmentado.append(boton);
  }

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

  tarjetaCabecera.append(encabezado, segmentado, zonaKpis, rejillaFiltros);

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
          // Los filtros que solo se aplican al llegar desde el tablero (no
          // tienen un control en la pantalla). SOLO en la primera carga: si
          // se quedaran, cambiar de vista daria una lista vacia.
          solo_abiertas: (primerCarga ? filtros?.solo_abiertas : undefined) || undefined,
          solo_sin_asignar: (primerCarga ? filtros?.solo_sin_asignar : undefined) || undefined,
          fuera_de_sla: (primerCarga ? filtros?.fuera_de_sla : undefined) || undefined,
          sin_atender: (primerCarga ? filtros?.sin_atender : undefined) || undefined,
          // La vista activa decide el filtro de la lista.
          ...filtrosDeVista(),
          limite: 50,
        }),
        api.metricasIncidencias().catch(() => null),
      ]);

      if (numero !== peticionEnCurso) return;

      primerCarga = false;
      pintarKpis(metricas);
      pintarLista(datos.items);
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  /**
   * Los filtros que imponen la vista activa.
   *
   * La bandeja son los casos ABIERTOS que NADIE RESPONDIO todavia. No es lo
   * mismo que "sin asignar": un caso asignado a alguien que lo tiene
   * abandonado sigue sin atendido, y es el que de verdad se pierde.
   */
  function filtrosDeVista() {
    if (vistaCasos === 'bandeja') return { sin_atender: true, solo_abiertas: true };
    if (vistaCasos === 'mias') return { solo_mias: true };
    return {};
  }

  function pintarKpis(metricas) {
    zonaKpis.replaceChildren();
    if (!metricas) return;

    const r = metricas.resumen ?? {};

    // Un KPI que NO lleva a ningun lado es decoracion. Cada uno que se puede
    // filtrar, filtra: y el que trae la bandeja, deja la pantalla en la
    // bandeja.
    const tarjetas = [
      {
        etiqueta: 'Sin atender',
        valor: r.sin_atender ?? 0,
        ayuda: 'Casos abiertos que NADIE respondio todavia. Es el trabajo sin dueno: se van a perder si no se toman.',
        ir: () => irAVista('bandeja'),
      },
      {
        etiqueta: 'Abiertas',
        valor: r.abiertas ?? 0,
        ayuda: 'Casos sin resolver, en cualquiera de sus estados.',
        ir: () => irAVista('todas'),
      },
      {
        etiqueta: 'Vencidas',
        valor: r.vencidas ?? 0,
        ayuda: 'Ya pasaron su plazo de resolucion y siguen abiertas.',
        ir: () => {
          chkVencidas.checked = true;
          irAVista('todas');
        },
      },
      {
        etiqueta: 'Criticas',
        valor: r.criticas_abiertas ?? 0,
        ayuda: 'Severidad S1 abiertas: sin servicio.',
        ir: () => {
          selectSeveridad.value = 'S1';
          irAVista('todas');
        },
      },
      {
        etiqueta: 'Sin asignar',
        valor: r.sin_asignar ?? 0,
        ayuda: 'Nadie las tomo todavia.',
        ir: () => irAVista('bandeja'),
      },
      {
        etiqueta: 'Fuera de SLA',
        valor: r.fuera_de_sla ?? 0,
        ayuda: 'Se resolvieron despues del plazo.',
        ir: () => irAVista('todas'),
      },
      {
        etiqueta: 'MTTR',
        valor: r.mttr_horas !== null && r.mttr_horas !== undefined ? `${r.mttr_horas} h` : '—',
        ayuda: 'Horas promedio entre el reporte y la resolucion. El numero que le importa al responsable.',
      },
    ];

    for (const t of tarjetas) {
      const caja = crear(t.ir ? 'button' : 'div', {
        clase: t.ir ? 'kpi kpi--accion' : 'kpi',
        atributos: t.ir
          ? { type: 'button', 'aria-label': `${t.etiqueta}: ${t.valor}. Ver el detalle.` }
          : { tabindex: '0' },
      });
      caja.append(
        crear('div', { clase: 'kpi__valor', texto: String(t.valor) }),
        crear('div', { clase: 'kpi__etiqueta', texto: t.etiqueta }),
      );
      conTooltip(caja, t.ayuda);
      if (t.ir) caja.addEventListener('click', t.ir);
      zonaKpis.append(caja);
    }
  }

  /**
   * Cambia de vista y recarga la lista.
   *
   * Los botones del segmentado se actualizan tambien: si el KPI te deja en
   * "Bandeja", el segmentado tiene que decir "Bandeja", o el operador no sabe
   * donde esta parado.
   */
  function irAVista(clave) {
    vistaCasos = clave;
    for (const b of botonesSegmento) {
      b.classList.toggle('segmento--activo', b.dataset.vista === clave);
    }
    cargar();
  }

  function pintarLista(incidencias) {
    if (!incidencias.length) {
      // El estado vacio dice QUE no hay. "No hay incidencias con esos filtros"
      // es un mensaje de error de filtros: el operador no sabe si la bandeja
      // esta vacia de verdad o si el filtro esta mal.
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.ok, { tamano: 28 }),
        crear('p', {
          texto:
            vistaCasos === 'bandeja'
              ? 'No hay casos sin atender: todo lo que se reporto tiene a alguien.'
              : vistaCasos === 'mias'
                ? 'No tenes casos asignados.'
                : 'No hay incidencias con esos filtros.',
        }),
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

      // La marca de SLA: lo que hay que ver primero.
      //
      // UN CASO YA RESUELTO NO ESTA "EN PLAZO": el plazo dejo de correr
      // cuando se resolvio. Decirle "En plazo, faltan 2 dias" a un caso
      // cerrado hace pensar al operador que todavia falta trabajo, y lo
      // lleva a buscar por algo que ya esta hecho.
      const resuelto = i.status === 'RESOLVED' || i.status === 'CLOSED';
      const vencida = crear('span', {
        clase: `insignia ${
          resuelto
            ? i.fuera_de_sla
              ? 'insignia--peligro'
              : 'insignia--instalado'
            : i.vencido
              ? 'insignia--peligro'
              : 'insignia--transito'
        }`,
        texto: resuelto
          ? i.fuera_de_sla
            ? 'Fuera de SLA'
            : 'Resuelta'
          : i.vencido
            ? 'Vencida'
            : 'En plazo',
      });
      conTooltip(
        vencida,
        resuelto
          ? i.fuera_de_sla
            ? 'Se resolvio despues de su plazo.'
            : 'Resuelta dentro del plazo.'
          : i.vencido
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

      // TOMAR EL CASO
      //
      // El caso sin dueno se toma con un clic, sin abrir el detalle primero:
      // el tecnico que arranca el turno recorre la bandeja y toma lo que
      // puede, y abrir cada caso para leerlo antes seria un paso de mas.
      if (puedeGestionar && !i.assigned_to && i.status === 'OPEN') {
        const btnTomar = crear('button', {
          clase: 'boton boton--chico',
          texto: 'Tomar',
          atributos: { type: 'button' },
        });
        conTooltip(
          btnTomar,
          'Toma el caso: queda asignado a vos, arranca el reloj de respuesta y se avisa al que lo reporto.',
        );
        btnTomar.addEventListener('click', async () => {
          btnTomar.disabled = true;
          btnTomar.textContent = 'Tomando…';
          try {
            await api.tomarCasoIncidencia(i.id);
            avisar(`Caso ${i.code} tomado.`, 'exito', 2500);
            cargar();
          } catch (error) {
            avisar(error.message, 'error');
            btnTomar.disabled = false;
            btnTomar.textContent = 'Tomar';
          }
        });
        item.append(btnTomar);
      }
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

  // El filtro con el que se llega desde el tablero del Helpdesk.
  if (filtros?.estado) selectEstado.value = filtros.estado;
  if (filtros?.severidad) selectSeveridad.value = filtros.severidad;
  if (filtros?.solo_vencidas) chkVencidas.checked = true;

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

  // LA RESOLUCION
  //
  // Si el caso ya se resolvio, el texto de la resolucion es lo primero que
  // tiene que ver quien lo abre: dice que se hizo. Sin esto, el caso
  // resuelto se ve como un caso mas, y hay que ir a la linea de tiempo a
  // buscarlo.
  if (detalle.resolution) {
    const resolucion = crear('div', { clase: 'incidencia__resolucion' });
    resolucion.append(
      crear('div', { clase: 'incidencia__resolucion-titulo', texto: 'Resolucion' }),
      crear('p', { clase: 'incidencia__descripcion', texto: detalle.resolution }),
    );
    datos.append(resolucion);
  }

  // El SLA, con lo que falta.
  const sla = crear('div', { clase: 'incidencia__sla' });
  const restanteTexto = restante(detalle);
  const resuelto = detalle.status === 'RESOLVED' || detalle.status === 'CLOSED';

  // Un caso cerrado no tiene "plazo que falta": tiene una fecha de
  // resolucion. La insignia dice lo que corresponde a cada situacion.
  sla.append(
    crear('div', {
      clase: `insignia ${
        resuelto
          ? detalle.fuera_de_sla
            ? 'insignia--peligro'
            : 'insignia--instalado'
          : detalle.vencido
            ? 'insignia--peligro'
            : 'insignia--deposito'
      }`,
      texto: resuelto
        ? detalle.fuera_de_sla
          ? 'Resuelta fuera de SLA'
          : 'Resuelta'
        : detalle.vencido
          ? 'Fuera de SLA'
          : 'En plazo',
    }),
    crear('span', {
      clase: 'mono pequeno',
      texto: resuelto
        ? detalle.resolved_at
          ? `Resuelta: ${new Date(detalle.resolved_at).toLocaleString('es-AR')}`
          : ''
        : restanteTexto
          ? `Resolucion: ${restanteTexto}`
          : '',
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

  // EL CIERRE DEL CASO
  // ------------------
  // Resolver un caso de este Service Desk NO es poner un texto y listo: es
  // decir que paso con el EQUIPO. El equipo roto tiene que salir del stock
  // (taller), el reparado vuelve al deposito, y el danado de forma total se
  // desincorpora. Sin esto, el inventario no se entera y el KPI de "en
  // deposito" cuenta equipos que estan rotos.
  if (detalle.device_id && transiciones.some((t) => t.valor === 'RESOLVED')) {
    zona.append(cierreDe(detalle, alCambiar));
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
 * EL DESTINO DEL EQUIPO AL CERRAR EL CASO
 * ---------------------------------------
 * Las tres salidas posibles, en el lenguaje del deposito:
 *
 * - TALLER: el equipo esta roto y va al taller. Deja de contar como
 *   disponible (eso es lo que hace que el "en deposito" sea verdad).
 * - REPARADO: se arreglo y vuelve al deposito.
 * - DANO_TOTAL: no se arregla. El equipo sale del stock con su certificado
 *   de baja: es la unica forma de que un equipo inservible no siga
 *   contando como disponible para siempre.
 *
 * Y una cuarta, "sin cambios": cuando el caso no tiene equipo, o cuando el
 * equipo no se toco (por ejemplo, se resolvio reinstalando el sistema).
 */
const DESTINOS_EQUIPO = [
  {
    valor: 'NINGUNO',
    texto: 'El equipo no cambia de estado',
    ayuda: 'El caso se resuelve, pero el equipo sigue donde estaba.',
  },
  {
    valor: 'TALLER',
    texto: 'Quedo roto: va al taller',
    ayuda: 'El equipo pasa a "en taller" y deja de contar como disponible.',
  },
  {
    valor: 'REPARADO',
    texto: 'Se reparo: vuelve al deposito',
    ayuda: 'El equipo vuelve al deposito y vuelve a estar disponible.',
  },
  {
    valor: 'DANO_TOTAL',
    texto: 'Dano total: sale del stock',
    ayuda: 'El equipo no se arregla: se da de baja y sale del inventario, con su certificado.',
  },
];

/**
 * El formulario de cierre del caso.
 *
 * @param {object} detalle   La incidencia.
 * @param {Function} alCambiar  Se llama despues de cerrar, para refrescar.
 */
function cierreDe(detalle, alCambiar) {
  const zona = crear('div', { clase: 'apilado incidencia__cierre' });

  zona.append(
    crear('h3', { clase: 'tarjeta__titulo', texto: 'Resolver el caso' }),
  );

  const campoResolucion = campoArea({
    etiqueta: 'Como se resolvio *',
    placeholder: 'Ej: Se cambio la placa madre y el equipo quedo funcionando.',
    requerido: true,
    filas: 3,
    descripcion: 'Lo que se hizo. Es lo que lee el que reporto el problema.',
  });

  const selectDestino = crear('select', { clase: 'campo__control' });
  for (const d of DESTINOS_EQUIPO) {
    selectDestino.append(crear('option', { texto: d.texto, atributos: { value: d.valor } }));
  }

  const zonaDestino = crear('label', { clase: 'campo' });
  const textoDestino = crear('span', { clase: 'campo__etiqueta', texto: 'Que paso con el equipo' });
  zonaDestino.append(textoDestino, selectDestino);
  conTooltip(
    textoDestino,
    'El caso no se cierra solo: el equipo tiene que reflejar lo que paso, o el inventario miente.',
  );

  zona.append(campoResolucion.campo, zonaDestino);

  const btnResolver = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Resolver el caso',
    atributos: { type: 'button' },
  });

  btnResolver.addEventListener('click', async () => {
    const resolucion = campoResolucion.control.value.trim();
    campoResolucion.limpiar();

    if (resolucion.length < 3) {
      campoResolucion.marcar('Conta como se resolvio el caso.');
      return;
    }

    btnResolver.disabled = true;
    btnResolver.textContent = 'Resolviendo…';
    try {
      const actualizado = await api.cerrarIncidencia(detalle.id, {
        resolution: resolucion,
        estado: 'RESOLVED',
        destino_equipo: selectDestino.value,
      });
      // El backend devuelve que paso con el EQUIPO ("quedo en el taller con la
      // orden #12"). Se muestra: el tecnico tiene que ver que el equipo se
      // movio de verdad, no solo que el caso se cerro.
      avisar(
        actualizado.mensaje_equipo
          ? `Caso resuelto. ${actualizado.mensaje_equipo} Se aviso al que lo reporto.`
          : 'Caso resuelto. Se aviso al que lo reporto.',
        'exito',
        4500,
      );
      cerrarYReabrir(actualizado, alCambiar);
    } catch (error) {
      avisar(error.message, 'error');
      btnResolver.disabled = false;
      btnResolver.textContent = 'Resolver el caso';
    }
  });

  zona.append(btnResolver);
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
