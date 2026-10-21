/**
 * Vista "Inicio" — el tablero.
 *
 * DOS SOLUCIONES, DOS TABLEROS
 * -----------------------------
 * La app crecio y ahora resuelve DOS problemas distintos:
 *
 *   - INVENTARIO: donde está cada equipo, cuanto vale, y que hay que reponer.
 *   - HELPDESK:   los problemas reportados, el SLA y el taller.
 *
 * Meter las dos cosas en el mismo tablero no era un problema de espacio: era
 * un problema de RESPUESTA. Un responsable de depósito abre el tablero para
 * ver cuanto stock le falta, y encuentra el MTTR del taller en el mismo
 * lugar: las dos cosas se leen como una sola, y la que no le importa tapa la
 * que si.
 *
 * LAS REGLAS DEL TABLERO
 * ----------------------
 * 1. UN TABLERO, UNA PREGUNTA. Se elige la solucion y se ve esa, entera.
 * 2. Máximo TRES Números GRANDES. El ojo no escanea ocho números: elige.
 *    Los demas van en una fila secundaria, más chicos.
 * 3. CADA ALERTA LLEVA A SU LISTA, con el filtro puesto. Un "3 productos
 *    bajo mínimo" sin destino es ruido; con destino, es una tarea.
 * 4. UN Número CON CONTEXTO. Un cero es buena noticia solo si se sabe que
 *    mide y contra que se compara (el tooltip lo dice).
 * 5. SIN SCROLL INFINITO. Tres bloques, cada uno con "ver todos". El tablero
 *    se lee en tres segundos o no se lee.
 * 6. CERO DATOS DUPLICADOS ENTRE SOLUCIONES. El Helpdesk no muestra
 *    "equipos por estado": no le sirve a nadie.
 * 7. EL INTERRUPTOR SE RECUERDA: es una preferencia de la persona, no un
 *    dato de la app.
 * 8. EL QUE NO TIENE PERMISO NO VE LA SOLUCION: un operador sin
 *    `incidents:read` no puede ver un Helpdesk vacío.
 */

import { api } from '../api.js';
import { ESTADOS, crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo } from '../dialogo.js';
import { conTooltip } from '../tooltip.js';
import { crearPaginacion } from '../paginacion.js';

const SOLUCIONES = {
  inventario: {
    etiqueta: 'Inventario',
    titulo: 'Inventario',
    subtitulo: 'Donde está cada equipo, cuanto vale y que hay que reponer.',
  },
  helpdesk: {
    etiqueta: 'Service Desk',
    titulo: 'Service Desk',
    subtitulo: 'Los problemas reportados, el SLA y el taller.',
  },
};

export function vistaInicio({ permisos = [], modulo = 'inventario' } = {}) {
  const raiz = crear('div');

  const puedeVerIncidencias = permisos.includes('incidents:read');

  // --- Cabecera y selector --------------------------------------------------
  const tarjetaCabecera = crear('section', { clase: 'tarjeta' });

  const encabezado = crear('div', { clase: 'flex-entre' });
  const textosCabecera = crear('div');
  const titulo = crear('h2', { clase: 'tarjeta__titulo' });
  const subtitulo = crear('p', { clase: 'tarjeta__subtitulo', estilo: 'margin:0' });
  textosCabecera.append(titulo, subtitulo);
  encabezado.append(textosCabecera);

  // La zona de números: tres destacados y una fila de secundarios.
  const zonaDestacados = crear('div', { clase: 'rejilla-kpi' });
  const zonaSecundarios = crear('div', { clase: 'rejilla-kpi' });
  const zonaContenido = crear('div', { clase: 'apilado' });

  tarjetaCabecera.append(encabezado, zonaDestacados, zonaSecundarios);

  raiz.append(tarjetaCabecera, zonaContenido);

  // --- Estado ---------------------------------------------------------------
  //
  // EL Módulo, NO UN SELECTOR PROPIO
  // --------------------------------
  // El tablero NO decide que muestra: lo decide el módulo en el que se está
  // trabajando, que se eligio en la pantalla de entrada. Por eso acá no hay un
  // selector: habria DOS lugares para cambiar de módulo (este y el de la
  // cabecera) y se desincronizarian.
  let solucionActual = modulo;
  let datosInventario = null;
  let datosHelpdesk = null;
  let casosAbiertos = [];
  let casosSinAtender = [];

  function definicionActual() {
    return SOLUCIONES[solucionActual] ?? Object.values(SOLUCIONES)[0];
  }

  async function cargar() {
    try {
      // Las tres cosas se piden juntas: el operador cambia de solucion seguido,
      // y con esperas separadas el tablero tarda medio minuto.
      const [inventario, helpdesk, casos, sinAtender] = await Promise.all([
        api.pedir('/reports/dashboard'),
        puedeVerIncidencias ? api.metricasIncidencias().catch(() => null) : Promise.resolve(null),
        puedeVerIncidencias
          ? api.incidencias({ solo_abiertas: true, limite: 6 }).catch(() => null)
          : Promise.resolve(null),
        // EL BACKLOG: los casos que NADIE respondió. Van aparte de los que
        // vencen: un caso puede no haber vencido y aún así estar parado
        // desde hace tres días.
        puedeVerIncidencias
          ? api.incidencias({ sin_atender: true, limite: 6 }).catch(() => null)
          : Promise.resolve(null),
      ]);
      datosInventario = inventario;
      datosHelpdesk = helpdesk;
      casosAbiertos = casos?.items ?? [];
      casosSinAtender = sinAtender?.items ?? [];
    } catch (error) {
      avisar(`No se pudo cargar el tablero: ${error.message}`, 'error');
    }
    pintar();
  }

  // --- Piezas reutilizables -------------------------------------------------
  /**
   * Un número del tablero.
   *
   * `destacado` lo hace grande: es el que se mira en los tres segundos. Los
   * secundarios van chicos, en una fila aparte.
   */
  function numero({ etiqueta, valor, ayuda, destacado = false, destino }) {
    const caja = crear(destino ? 'button' : 'div', {
      clase: destacado ? 'kpi kpi--destacado' : 'kpi',
      atributos: destino
        ? { type: 'button', 'aria-label': `${etiqueta}: ${valor}. Ver el detalle.` }
        : { tabindex: '0' },
    });
    caja.append(
      crear('div', { clase: 'kpi__valor', texto: String(valor) }),
      crear('div', { clase: 'kpi__etiqueta', texto: etiqueta }),
    );
    conTooltip(caja, ayuda);

    // UN Número QUE NO LLEVA A Ningún LADO ES DECORACION
    // -------------------------------------------------
    // Si el operador hace clic en "En depósito", espera ver los equipos que
    // están en depósito. Con el filtro YA PUESTO: no tiene que reconstruir
    // lo que el tablero le acaba de decir.
    if (destino) {
      caja.classList.add('kpi--accion');
      caja.addEventListener('click', () => irA(destino.vista, destino.filtros));
    }

    return caja;
  }

  /**
   * Una tarjeta con una lista y un "ver todos".
   *
   * EL "VER TODOS" ES OBLIGATORIO
   * -----------------------------
   * Un bloque que muestra 5 de 20 elementos, sin forma de ver los otros 15, es
   * un callejon sin salida. El botón lleva a la lista CON EL FILTER PUESTO:
   * el operador no tiene que reconstruir lo que ya vio en el tablero.
   */
  function tarjetaLista({ titulo, subtitulo, elementos, verTodos, alVerTodos }) {
    const tarjeta = crear('section', { clase: 'tarjeta' });

    const cabecera = crear('div', { clase: 'flex-entre' });
    const textos = crear('div');
    textos.append(crear('h3', { clase: 'tarjeta__titulo', texto: titulo }));
    if (subtitulo) {
      textos.append(crear('p', { clase: 'tarjeta__subtitulo', estilo: 'margin:0', texto: subtitulo }));
    }
    cabecera.append(textos);

    if (verTodos && elementos.length) {
      const btn = crear('button', {
        clase: 'botón botón--suave botón--chico',
        texto: verTodos,
        atributos: { type: 'button' },
      });
      btn.addEventListener('click', alVerTodos);
      cabecera.append(btn);
    }

    tarjeta.append(cabecera);

    if (!elementos.length) {
      tarjeta.append(
        crear('p', { clase: 'tenue', texto: 'Nada para mostrar acá. Todo en orden.' }),
      );
      return tarjeta;
    }

    const lista = crear('ul', { clase: 'lista' });
    for (const elemento of elementos) {
      lista.appendChild(elemento);
    }
    tarjeta.appendChild(lista);
    return tarjeta;
  }

  /** Una fila de la lista: cuerpo a la izquierda, insignia a la derecha. */
  /**
   * Una fila del tablero.
   *
   * QUE AL HACER CLIC SE MUESTRE EL DETALLE
   * ---------------------------------------
   * Un tablero que muestra "En transito: 3" y no deja ver WHICHES tres son
   * obliga al operador a ir a otra pantalla y filtrar a mano. La fila es un
   * BOTON: se pulsa y se abre el detalle de lo que muestra.
   *
   * `alClick` es la accion: si no viene, la fila es texto (no todo se
   * puede abrir: un dato que no tiene destino no es un enlace).
   */
  function fila({ titulo, meta, descripcion, insignia, claseInsignia, ayudaInsignia, alClick, ayudaClic }) {
    const item = crear('li', { clase: 'lista__item' });
    const cuerpo = crear('div', { clase: 'lista__cuerpo' });
    cuerpo.append(crear('div', { clase: 'lista__titulo', texto: titulo }));
    if (meta) cuerpo.append(crear('div', { clase: 'lista__meta mono', texto: meta }));
    if (descripcion) cuerpo.append(crear('div', { clase: 'lista__meta', texto: descripcion }));

    if (insignia) {
      const etiqueta = crear('span', { clase: `insignia ${claseInsignia ?? ''}`, texto: insignia });
      if (ayudaInsignia) conTooltip(etiqueta, ayudaInsignia);
      item.appendChild(etiqueta);
    }

    if (alClick) {
      // La fila entera es el boton: se pulsa en cualquier parte de la fila.
      const boton = crear('button', {
        clase: 'lista__item boton-lista',
        atributos: {
          type: 'button',
          'aria-label': ayudaClic ? `${titulo}. ${ayudaClic}` : String(titulo),
        },
      });
      boton.append(cuerpo);
      const insigniaEnElBoton = boton.querySelector('.insignia');
      if (insigniaEnElBoton) boton.appendChild(insigniaEnElBoton);
      boton.addEventListener('click', alClick);
      if (ayudaClic) conTooltip(boton, ayudaClic);
      item.replaceChildren(boton);
      return item;
    }

    item.appendChild(cuerpo);
    return item;
  }

  // ------------------------------------------------------------- INVENTARIO
  function pintarInventario() {
    zonaDestacados.replaceChildren();
    zonaSecundarios.replaceChildren();
    zonaContenido.replaceChildren();

    const d = datosInventario;
    if (!d) {
      zonaContenido.append(crear('div', { clase: 'cargando', texto: 'Cargando…' }));
      return;
    }

    const kpis = d.kpis ?? {};
    const criticos = d.productos_criticos ?? [];
    const valor = (v) => (v === null || v === undefined ? '—' : String(v));

    // LOS TRES DESTACADOS: los que se miran en los tres segundos.
    zonaDestacados.append(
      numero({
        destacado: true,
        etiqueta: 'Equipos',
        valor: valor(kpis.total_devices),
        ayuda: 'Componentes activos en el inventario, sin los dados de baja.',
        destino: { vista: 'inventario' },
      }),
      numero({
        destacado: true,
        etiqueta: 'En depósito',
        valor: valor(kpis.en_deposito),
        ayuda: 'Disponibles para salir. Es el stock que se puede entregar hoy.',
        destino: { vista: 'inventario', filtros: { estado: 'IN_WAREHOUSE' } },
      }),
      numero({
        destacado: true,
        etiqueta: 'Por reponer',
        valor: valor(criticos.length),
        ayuda: 'Productos con el stock igual o por debajo del mínimo. Click para verlos.',
        destino: { vista: 'stock' },
      }),
    );

    // LOS SECUNDARIOS: los que se miran si hacen falta.
    zonaSecundarios.append(
      numero({
        etiqueta: 'Instalados',
        valor: valor(kpis.instalados),
        ayuda: 'Working en sitio del cliente.',
        destino: { vista: 'inventario', filtros: { estado: 'INSTALLED' } },
      }),
      numero({
        etiqueta: 'En tránsito',
        // La clave del backend es `en_tránsito` (con 'o'): `en_transit` daba
        // `undefined` y el número salia como un guion.
        valor: valor(kpis.en_transito),
        ayuda: 'Salieron del depósito, todavía no instalados.',
        destino: { vista: 'inventario', filtros: { estado: 'IN_TRANSIT' } },
      }),
      numero({
        etiqueta: 'Movimientos (30 d)',
        valor: valor(kpis.movimientos_30d),
        ayuda: 'Escaneos y traslados registrados en el último mes.',
        destino: { vista: 'movimientos' },
      }),
    );

    // BLOQUE 1: por reponer. Es la ACCION del responsable de depósito.
    const paraReponer = criticos.slice(0, 6).map((p) =>
      fila({
        titulo: `${p.brand ?? ''} ${p.model_name ?? p.model ?? ''}`.trim(),
        meta: p.sku,
        descripcion: `Disponibles ${p.disponibles} · Minimo ${p.min_stock} · Lead time ${p.lead_time_days ?? '?'} d`,
        insignia: `Faltan ${p.faltante}`,
        claseInsignia: 'insignia--peligro',
        ayudaInsignia: `El minimo es ${p.min_stock} y hay ${p.disponibles} disponibles.`,
        // EL DETALLE: el producto que se pulso, con su faltante.
        alClick: () => abrirStockBajo(p.sku),
        ayudaClic: 'Ver el detalle de este producto',
      }),
    );

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Por reponer',
        subtitulo: 'Productos con el stock igual o por debajo del mínimo.',
        elementos: paraReponer,
        verTodos: 'Ver el stock',
        alVerTodos: () => irA('stock'),
      }),
    );

    // BLOQUE 2: donde está el equipo.
    const stock = d.stock_por_estado ?? [];
    const porEstado = stock.map((s) => {
      const info = ESTADOS[s.estado] ?? { texto: s.estado, clase: 'insignia--baja' };
      return fila({
        titulo: info.texto,
        descripcion: `${Math.round((Number(s.cantidad) / (stock.reduce((a, x) => a + Number(x.cantidad || 0), 0) || 1)) * 100)}% del total`,
        insignia: String(s.cantidad),
        claseInsignia: info.clase,
        ayudaInsignia: `${info.texto}: ${s.cantidad} equipos`,
        // EL DETALLE: el inventario con el filtro de ese estado.
        alClick: () => irA('inventario', { estado: s.estado }),
        ayudaClic: `Ver los equipos ${info.texto.toLowerCase()}`,
      });
    });

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Equipos por estado',
        elementos: porEstado,
        verTodos: 'Ver el inventario',
        alVerTodos: () => irA('inventario'),
      }),
    );

    // BLOQUE 3: los últimos movimientos.
    const movimientos = (d.movimientos_recientes ?? []).slice(0, 6).map((m) =>
      fila({
        // La clave del backend es `movimiento` (la etiqueta del tipo de
        // movimiento): `movement_type` daba `undefined` y salia 'Movimiento'.
        titulo: m.movimiento ?? 'Movimiento',
        meta: m.serial_number ?? '',
        descripcion: `${m.brand ?? ''} ${m.model ?? ''}`.trim(),
        // EL DETALLE: la ficha del equipo que se movio.
        alClick: m.device_id ? () => irA('dispositivo', { deviceId: m.device_id }) : undefined,
        ayudaClic: m.device_id ? 'Ver la ficha del equipo' : undefined,
      }),
    );

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Últimos movimientos',
        elementos: movimientos,
        verTodos: 'Ver los movimientos',
        alVerTodos: () => irA('movimientos'),
      }),
    );
  }

  // --------------------------------------------------------------- HELPDESK
  function pintarHelpdesk() {
    zonaDestacados.replaceChildren();
    zonaSecundarios.replaceChildren();
    zonaContenido.replaceChildren();

    const m = datosHelpdesk?.resumen;
    if (!m) {
      zonaContenido.append(crear('div', { clase: 'cargando', texto: 'Cargando…' }));
      return;
    }

    const valor = (v) => (v === null || v === undefined ? '—' : String(v));

    zonaDestacados.append(
      numero({
        destacado: true,
        etiqueta: 'Abiertas',
        valor: valor(m.abiertas),
        ayuda: 'Casos sin resolver, en cualquiera de sus estados.',
        destino: { vista: 'incidencias', filtros: { solo_abiertas: true } },
      }),
      numero({
        destacado: true,
        etiqueta: 'Vencidas',
        valor: valor(m.vencidas),
        ayuda: 'Ya pasaron su plazo de resolucion y siguen abiertas. Es lo primero que hay que atacar.',
        destino: { vista: 'incidencias', filtros: { solo_vencidas: true } },
      }),
      numero({
        destacado: true,
        etiqueta: 'MTTR',
        valor: m.mttr_horas !== null && m.mttr_horas !== undefined ? `${m.mttr_horas} h` : '—',
        ayuda: 'Horas promedio entre el reporte y la resolucion. El número que le importa al responsable del taller.',
        // Un objeto vacío de filtros: la vista abre en "Todas" y no en la
        // bandeja. Sin esto, el clic en un número general caeria en la lista
        // de trabajo pendiente, que es otra cosa.
        destino: { vista: 'incidencias', filtros: {} },
      }),
    );

    zonaSecundarios.append(
      numero({
        etiqueta: 'Sin atender',
        valor: valor(m.sin_atender),
        ayuda:
          'Casos abiertos que NADIE respondió todavía: el trabajo sin dueño. Es lo primero que hay que tomar.',
        destino: { vista: 'incidencias', filtros: { sin_atender: true } },
      }),
      numero({
        etiqueta: 'Criticas (S1)',
        valor: valor(m.criticas_abiertas),
        ayuda: 'Sin servicio: son las que no pueden esperar.',
        destino: { vista: 'incidencias', filtros: { severidad: 'S1' } },
      }),
      numero({
        etiqueta: 'Sin asignar',
        valor: valor(m.sin_asignar),
        ayuda: 'Nadie las todavía: son las que se van a perder.',
        destino: { vista: 'incidencias', filtros: { solo_sin_asignar: true } },
      }),
      numero({
        etiqueta: 'Fuera de SLA',
        valor: valor(m.fuera_de_sla),
        ayuda: 'Se resolvieron después del plazo: el SLA incumplido.',
        destino: { vista: 'incidencias', filtros: { fuera_de_sla: true } },
      }),
    );

    // BLOQUE 1: los casos que vencen, con la cuenta regresiva.
    const casos = casosAbiertos.map((c) => {
      const minutos = c.minutos_para_resolver;
      const cuenta = minutos === null || minutos === undefined
        ? 'Sin plazo'
        : minutos < 0
          ? `Vencido hace ${Math.round(Math.abs(minutos) / 60)} h`
          : `Vence en ${Math.max(1, Math.round(minutos / 60))} h`;

      return fila({
        titulo: c.title,
        meta: `${c.code} · ${c.severity}`,
        descripcion: cuenta,
        insignia: c.vencido ? 'Vencido' : 'En plazo',
        claseInsignia: c.vencido ? 'insignia--peligro' : 'insignia--alerta',
        ayudaInsignia: cuenta,
      });
    });

    // BLOQUE 0: EL BACKLOG. Lo que nadie took.
    //
    // Va PRIMERO, antes que los casos que vencen: un caso sin dueño se pierde
    // igual, y no por falta de tiempo, sino porque nadie lo vio. Es el
    // bloque que se mira al empezar el turno.
    const sinAtender = casosSinAtender.map((c) => {
      const horas = Math.max(
        0,
        Math.round((Date.now() - new Date(c.created_at).getTime()) / 3600000),
      );

      return fila({
        titulo: c.title,
        meta: `${c.code} · ${c.severity}`,
        descripcion: `Sin responder desde hace ${horas} h`,
        insignia: `${horas} h`,
        claseInsignia: horas > 24 ? 'insignia--peligro' : 'insignia--alerta',
        ayudaInsignia: 'Nadie todavía respondió este caso.',
      });
    });

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Sin atender',
        subtitulo: 'Reportados y sin responder. El trabajo que se pierde si no se toma.',
        elementos: sinAtender,
        verTodos: 'Ver la bandeja',
        alVerTodos: () => irA('incidencias', { sin_atender: true }),
      }),
    );

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Casos que vencen',
        subtitulo: 'El plazo sale del SLA de la severidad de cada caso.',
        elementos: casos,
        verTodos: 'Ver las incidencias',
        alVerTodos: () => irA('incidencias'),
      }),
    );

    // BLOQUE 2: la distribución por severidad.
    const severidades = (datosHelpdesk?.por_severidad ?? []).map((s) =>
      fila({
        titulo: `Severidad ${s.severity}`,
        descripcion:
          s.severity === 'S1' ? 'Critico: sin servicio' :
          s.severity === 'S2' ? 'Alto: impacto serio' :
          s.severity === 'S3' ? 'Medio: un equipo' : 'Bajo: consulta',
        insignia: String(s.cantidad),
        claseInsignia: 'insignia--deposito',
        ayudaInsignia: `${s.cantidad} casos abiertos de severidad ${s.severity}`,
      }),
    );

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Abiertas por severidad',
        elementos: severidades,
        verTodos: 'Ver por severidad',
        alVerTodos: () => irA('incidencias'),
      }),
    );

    // BLOQUE 3: el trabajo pendiente (mantenimientos y compras).
    const mantenimientos = datosInventario?.mantenimientos_overdue ?? [];
    const pos = datosInventario?.pos_pendientes ?? [];
    const pendientes = [
      ...mantenimientos.map((m) =>
        fila({
          titulo: `${m.serial_number} · ${m.plan ?? 'Mantenimiento'}`,
          descripcion: `${m.model ?? ''} · vencido hace ${m.dias_vencido ?? '?'} dias`.trim(),
          insignia: 'Vencido',
          claseInsignia: 'insignia--peligro',
        }),
      ),
      ...pos.map((p) =>
        fila({
          titulo: `Orden de compra · ${p.proveedor ?? ''}`,
          descripcion: `Vencio hace ${p.dias_retraso ?? '?'} dias`,
          insignia: 'Atrasada',
          claseInsignia: 'insignia--alerta',
        }),
      ),
    ];

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Trabajo pendiente',
        subtitulo: 'Mantenimientos vencidos y ordenes de compra atrasadas.',
        elementos: pendientes,
      }),
    );
  }

  // --- Pintado --------------------------------------------------------------
  function pintar() {
    const definicion = definicionActual();
    titulo.textContent = definicion.titulo;
    subtitulo.textContent = definicion.subtitulo;

    if (solucionActual === 'helpdesk') {
      pintarHelpdesk();
    } else {
      pintarInventario();
    }
  }

  /**
   * Ir a otra vista.
   *
   * La navegacion se dispara con un evento de documento, no con una import
   * de `navegacion.js`: importarla desde acá crearia un ciclo (navegacion
   * importa las vistas, y las vistas importan navegacion).
   */
  function irA(vista, filtros) {
    // "stock" no es una vista: es un MODAL con los productos por reponer. El
    // stock bajo es de PRODUCTOS, y la vista de inventario lista EQUIPOS: no
    // son la misma cosa, así que abrir el inventario con otro filtro
    // mentiria.
    if (vista === 'stock') {
      abrirStockBajo();
      return;
    }
    document.dispatchEvent(new CustomEvent('app:ir-a', { detail: { vista, filtros } }));
  }

  /**
   * El modal de "por reponer": los productos bajo su stock mínimo.
   */
  /**
   * El modal de "Por reponer".
   *
   * `skuFoco` abre el detalle de UN producto: la fila del tablero que se
   * pulso. Sin eso, el modal muestra la lista completa y el operador tiene
   * que buscar a mano el producto que acaba de pulsar.
   */
  async function abrirStockBajo(skuFoco = null) {
    // EL CONTENEDOR NO LLEVA LA CLASE "CARGANDO"
    //
    // La clase `cargando` pone un spinner con un `::before`. Si el
    // contenedor la tiene, el spinner se queda girando PARA SIEMPRE: la
    // lista llega abajo y el operador ve un circulo que no para, al lado de
    // datos que ya están cargados. Es la clase la que hay que quitar, no el
    // texto: por eso el estado de carga es un HIJO que se reemplaza, y el
    // contenedor queda limpio.
    const cuerpo = crear('div');
    cuerpo.append(crear('div', { clase: 'cargando', texto: 'Consultando el stock…' }));

    // LA Paginación DEL MODAL
    //
    // El stock bajo son TODOS los productos por debajo del mínimo: con 200
    // tipos de producto, el modal traia 200 filas de golpe y la pantalla se
    // congela. El modal página igual que las pantallas, con la misma barra.
    let pagina = 0;
    const limite = 25;

    const paginacion = crearPaginacion({
      total: 0,
      limite,
      pagina,
      etiqueta: 'productos',
      alCambiarPagina: (nueva) => {
        pagina = Math.max(0, nueva);
        cargar();
      },
    });

    const btnCerrar = crear('button', {
      clase: 'botón botón--suave',
      texto: 'Cerrar',
      atributos: { type: 'button' },
    });

    let cerrarModal = () => {};

    abrirDialogo({
      titulo: skuFoco ? `Por reponer — ${skuFoco}` : 'Por reponer',
      nota: 'Productos con el stock igual o por debajo del mínimo, con el faltante y el lead time del proveedor.',
      cuerpo: [cuerpo, paginacion.elemento],
      clase: 'modal--formulario',
      acciones: [btnCerrar],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCerrar.addEventListener('click', cerrar);
      },
    });

    async function cargar() {
      cuerpo.replaceChildren(crear('div', { clase: 'cargando', texto: 'Consultando el stock…' }));

      try {
        // La MISMA consulta que usa la tarjeta "Por reponer" del tablero: si
        // son distintas, el detalle de un producto no coincide con la fila
        // que se pulso.
        const datos = await api.pedir('/stock/por-reponer');
        const todos = datos?.items ?? datos ?? [];
        const total = datos?.total ?? todos.length;

        // La página se arma sobre la lista completa: el endpoint de stock
        // bajo no página (es el detalle de una consulta puntual), así que el
        // corte se hace acá y la barra dice cuantos hay en total.
        const soloEste = skuFoco ? todos.filter((i) => i.sku === skuFoco) : todos;
        const items = soloEste.slice(pagina * limite, pagina * limite + limite);

        if (!items.length) {
          const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
          vacio.append(
            icono(ICONOS.ok, { tamano: 28 }),
            crear('p', { texto: 'Todos los productos están por encima de su mínimo.' }),
          );
          cuerpo.replaceChildren(vacio);
          paginacion.actualizar({ total: 0, limite, pagina: 0 });
          return;
        }

        const lista = crear('ul', { clase: 'lista' });
        for (const item of items) {
          const cuerpoFila = crear('div', { clase: 'lista__cuerpo' });
          cuerpoFila.append(
            crear('div', { clase: 'lista__titulo', texto: `${item.brand ?? ''} ${item.model_name ?? item.model ?? ''}`.trim() }),
            crear('div', { clase: 'lista__meta mono', texto: item.sku ?? '' }),
            crear('div', {
              clase: 'lista__meta',
              texto: `Disponible ${item.available_count ?? item.disponibles ?? 0} · Minimo ${item.min_stock ?? 0} · ${item.location_name ?? item.ubicacion ?? ''}`,
            }),
          );
          const insignia = crear('span', {
            clase: 'insignia insignia--peligro',
            texto: `Faltan ${item.deficit ?? item.faltante ?? 0}`,
          });
          const item_ = crear('li', { clase: 'lista__item' });
          item_.append(cuerpoFila, insignia);
          lista.appendChild(item_);
        }

        cuerpo.replaceChildren(lista);
        paginacion.actualizar({ total: soloEste.length, limite, pagina });

        // El boton de "ver todos", cuando se abrio con un producto enfocado.
        if (skuFoco) {
          const btnTodos = crear('button', {
            clase: 'boton boton--suave boton--chico',
            texto: 'Ver todos los productos por reponer',
            atributos: { type: 'button' },
          });
          btnTodos.addEventListener('click', () => {
            cerrarModal(true);
            abrirStockBajo();
          });
          cuerpo.append(btnTodos);
        }
      } catch (error) {
        cuerpo.replaceChildren(
          crear('p', { clase: 'campo__error', texto: `No se pudo consultar el stock: ${error.message}` }),
        );
      }
    }

    cargar();
  }

  // --- Arranque -------------------------------------------------------------
  // Si el módulo es Helpdesk y el usuario no puede ver incidencias (un rol
  // nuevo, un permiso cambiado), se cae al de inventario: es mejor un tablero
  // que no sea el suyo que una pantalla vacía.
  if (solucionActual === 'helpdesk' && !puedeVerIncidencias) {
    solucionActual = 'inventario';
  }

  cargar();
  return { elemento: raiz };
}
