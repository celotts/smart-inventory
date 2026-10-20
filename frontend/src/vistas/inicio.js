/**
 * Vista "Inicio" — el tablero.
 *
 * DOS SOLUCIONES, DOS TABLEROS
 * -----------------------------
 * La app crecio y ahora resuelve DOS problemas distintos:
 *
 *   - INVENTARIO: donde esta cada equipo, cuanto vale, y que hay que reponer.
 *   - HELPDESK:   los problemas reportados, el SLA y el taller.
 *
 * Meter las dos cosas en el mismo tablero no era un problema de espacio: era
 * un problema de RESPUESTA. Un responsable de deposito abre el tablero para
 * ver cuanto stock le falta, y encuentra el MTTR del taller en el mismo
 * lugar: las dos cosas se leen como una sola, y la que no le importa tapa la
 * que si.
 *
 * LAS REGLAS DEL TABLERO
 * ----------------------
 * 1. UN TABLERO, UNA PREGUNTA. Se elige la solucion y se ve esa, entera.
 * 2. MAXIMO TRES NUMEROS GRANDES. El ojo no escanea ocho numeros: elige.
 *    Los demas van en una fila secundaria, mas chicos.
 * 3. CADA ALERTA LLEVA A SU LISTA, con el filtro puesto. Un "3 productos
 *    bajo minimo" sin destino es ruido; con destino, es una tarea.
 * 4. UN NUMERO CON CONTEXTO. Un cero es buena noticia solo si se sabe que
 *    mide y contra que se compara (el tooltip lo dice).
 * 5. SIN SCROLL INFINITO. Tres bloques, cada uno con "ver todos". El tablero
 *    se lee en tres segundos o no se lee.
 * 6. CERO DATOS DUPLICADOS ENTRE SOLUCIONES. El Helpdesk no muestra
 *    "equipos por estado": no le sirve a nadie.
 * 7. EL INTERRUPTOR SE RECUERDA: es una preferencia de la persona, no un
 *    dato de la app.
 * 8. EL QUE NO TIENE PERMISO NO VE LA SOLUCION: un operador sin
 *    `incidents:read` no puede ver un Helpdesk vacio.
 */

import { api } from '../api.js';
import { ESTADOS, crear } from '../dom.js';
import { avisar } from '../avisos.js';
import { conTooltip } from '../tooltip.js';

const CLAVE_SOLUCION = 'si-solucion';

const SOLUCIONES = {
  inventario: {
    etiqueta: 'Inventario',
    titulo: 'Inventario',
    subtitulo: 'Donde esta cada equipo, cuanto vale y que hay que reponer.',
  },
  helpdesk: {
    etiqueta: 'Helpdesk',
    titulo: 'Helpdesk',
    subtitulo: 'Los problemas reportados, el SLA y el taller.',
  },
};

export function vistaInicio({ permisos = [] } = {}) {
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

  // El selector de solucion: un control con pestanas, no un par de botones sueltos.
  const selector = crear('div', { clase: 'segmented-control', role: 'tablist' });
  const botones = {};

  for (const [clave, definicion] of Object.entries(SOLUCIONES)) {
    if (clave === 'helpdesk' && !puedeVerIncidencias) continue;

    const boton = crear('button', {
      clase: 'segmento',
      // `role` e `id` van DENTRO de `atributos`: `crear()` descarta en
      // silencio cualquier clave que no sea clase, id, texto, atributos o
      // hijos, y un `role` que se pierde deja el boton sin announce.
      atributos: {
        type: 'button',
        role: 'tab',
        id: `solucion-${clave}`,
        'aria-selected': 'false',
      },
      texto: definicion.etiqueta,
    });
    boton.addEventListener('click', () => elegirSolucion(clave));
    botones[clave] = boton;
    selector.appendChild(boton);
  }

  // La zona de numeros: tres destacados y una fila de secundarios.
  const zonaDestacados = crear('div', { clase: 'rejilla-kpi' });
  const zonaSecundarios = crear('div', { clase: 'rejilla-kpi' });
  const zonaContenido = crear('div', { clase: 'apilado' });

  tarjetaCabecera.append(encabezado, selector, zonaDestacados, zonaSecundarios);

  raiz.append(tarjetaCabecera, zonaContenido);

  // --- Estado ---------------------------------------------------------------
  let solucionActual = null;
  let datosInventario = null;
  let datosHelpdesk = null;
  let casosAbiertos = [];

  function definicionActual() {
    return SOLUCIONES[solucionActual] ?? Object.values(SOLUCIONES)[0];
  }

  function elegirSolucion(clave) {
    if (solucionActual === clave) return;
    solucionActual = clave;
    try {
      localStorage.setItem(CLAVE_SOLUCION, clave);
    } catch {
      // Sin storage (Safari privado): se muestra la solucion elegida y listo.
    }
    marcarSelector();
    pintar();
  }

  function marcarSelector() {
    for (const [nombre, boton] of Object.entries(botones)) {
      const activa = nombre === solucionActual;
      boton.classList.toggle('segmento--activo', activa);
      boton.setAttribute('aria-selected', activa ? 'true' : 'false');
    }
  }

  async function cargar() {
    try {
      // Las tres cosas se piden juntas: el operador cambia de solucion seguido,
      // y con esperas separadas el tablero tarda medio minuto.
      const [inventario, helpdesk, casos] = await Promise.all([
        api.pedir('/reports/dashboard'),
        puedeVerIncidencias ? api.metricasIncidencias().catch(() => null) : Promise.resolve(null),
        puedeVerIncidencias
          ? api.incidencias({ solo_abiertas: true, limite: 6 }).catch(() => null)
          : Promise.resolve(null),
      ]);
      datosInventario = inventario;
      datosHelpdesk = helpdesk;
      casosAbiertos = casos?.items ?? [];
    } catch (error) {
      avisar(`No se pudo cargar el tablero: ${error.message}`, 'error');
    }
    pintar();
  }

  // --- Piezas reutilizables -------------------------------------------------
  /**
   * Un numero del tablero.
   *
   * `destacado` lo hace grande: es el que se mira en los tres segundos. Los
   * secundarios van chicos, en una fila aparte.
   */
  function numero({ etiqueta, valor, ayuda, destacado = false }) {
    const caja = crear('div', {
      clase: destacado ? 'kpi kpi--destacado' : 'kpi',
      atributos: { tabindex: '0' },
    });
    caja.append(
      crear('div', { clase: 'kpi__valor', texto: String(valor) }),
      crear('div', { clase: 'kpi__etiqueta', texto: etiqueta }),
    );
    conTooltip(caja, ayuda);
    return caja;
  }

  /**
   * Una tarjeta con una lista y un "ver todos".
   *
   * EL "VER TODOS" ES OBLIGATORIO
   * -----------------------------
   * Un bloque que muestra 5 de 20 elementos, sin forma de ver los otros 15, es
   * un callejon sin salida. El boton lleva a la lista CON EL FILTER PUESTO:
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
        clase: 'boton boton--suave boton--chico',
        texto: verTodos,
        atributos: { type: 'button' },
      });
      btn.addEventListener('click', alVerTodos);
      cabecera.append(btn);
    }

    tarjeta.append(cabecera);

    if (!elementos.length) {
      tarjeta.append(
        crear('p', { clase: 'tenue', texto: 'Nada para mostrar aca. Todo en orden.' }),
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
  function fila({ titulo, meta, descripcion, insignia, claseInsignia, ayudaInsignia }) {
    const item = crear('li', { clase: 'lista__item' });
    const cuerpo = crear('div', { clase: 'lista__cuerpo' });
    cuerpo.append(crear('div', { clase: 'lista__titulo', texto: titulo }));
    if (meta) cuerpo.append(crear('div', { clase: 'lista__meta mono', texto: meta }));
    if (descripcion) cuerpo.append(crear('div', { clase: 'lista__meta', texto: descripcion }));
    item.appendChild(cuerpo);

    if (insignia) {
      const etiqueta = crear('span', { clase: `insignia ${claseInsignia ?? ''}`, texto: insignia });
      if (ayudaInsignia) conTooltip(etiqueta, ayudaInsignia);
      item.appendChild(etiqueta);
    }
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
      }),
      numero({
        destacado: true,
        etiqueta: 'En deposito',
        valor: valor(kpis.en_deposito),
        ayuda: 'Disponibles para salir. Es el stock que se puede entregar hoy.',
      }),
      numero({
        destacado: true,
        etiqueta: 'Por reponer',
        valor: valor(criticos.length),
        ayuda: 'Productos en o por debajo de su stock minimo. Click para verlos.',
      }),
    );

    // LOS SECUNDARIOS: los que se miran si hacen falta.
    zonaSecundarios.append(
      numero({
        etiqueta: 'Instalados',
        valor: valor(kpis.instalados),
        ayuda: 'Working en sitio del cliente.',
      }),
      numero({
        etiqueta: 'En transito',
        // La clave del backend es `en_transito` (con 'o'): `en_transit` daba
        // `undefined` y el numero salia como un guion.
        valor: valor(kpis.en_transito),
        ayuda: 'Salieron del deposito, todavia no instalados.',
      }),
      numero({
        etiqueta: 'Movimientos (30 d)',
        valor: valor(kpis.movimientos_30d),
        ayuda: 'Escaneos y traslados registrados en el ultimo mes.',
      }),
    );

    // BLOQUE 1: por reponer. Es la ACCION del responsable de deposito.
    const paraReponer = criticos.slice(0, 6).map((p) =>
      fila({
        titulo: `${p.brand} ${p.model}`,
        meta: p.sku,
        descripcion: `Disponibles ${p.disponibles} · Minimo ${p.min_stock} · Lead time ${p.lead_time_days ?? '?'} d`,
        insignia: `Faltan ${p.faltante}`,
        claseInsignia: 'insignia--peligro',
        ayudaInsignia: `El minimo es ${p.min_stock} y hay ${p.disponibles} disponibles.`,
      }),
    );

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Por reponer',
        subtitulo: 'Productos en o por debajo de su stock minimo.',
        elementos: paraReponer,
        verTodos: 'Ver el stock',
        alVerTodos: () => irA('inventario'),
      }),
    );

    // BLOQUE 2: donde esta el equipo.
    const stock = d.stock_por_estado ?? [];
    const porEstado = stock.map((s) => {
      const info = ESTADOS[s.estado] ?? { texto: s.estado, clase: 'insignia--baja' };
      return fila({
        titulo: info.texto,
        descripcion: `${Math.round((Number(s.cantidad) / (stock.reduce((a, x) => a + Number(x.cantidad || 0), 0) || 1)) * 100)}% del total`,
        insignia: String(s.cantidad),
        claseInsignia: info.clase,
        ayudaInsignia: `${info.texto}: ${s.cantidad} equipos`,
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

    // BLOQUE 3: los ultimos movimientos.
    const movimientos = (d.movimientos_recientes ?? []).slice(0, 6).map((m) =>
      fila({
        // La clave del backend es `movimiento` (la etiqueta del tipo de
        // movimiento): `movement_type` daba `undefined` y salia 'Movimiento'.
        titulo: m.movimiento ?? 'Movimiento',
        meta: m.serial_number ?? '',
        descripcion: `${m.brand ?? ''} ${m.model ?? ''}`.trim(),
      }),
    );

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Ultimos movimientos',
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
      }),
      numero({
        destacado: true,
        etiqueta: 'Vencidas',
        valor: valor(m.vencidas),
        ayuda: 'Ya pasaron su plazo de resolucion y siguen abiertas. Es lo primero que hay que atacar.',
      }),
      numero({
        destacado: true,
        etiqueta: 'MTTR',
        valor: m.mttr_horas !== null && m.mttr_horas !== undefined ? `${m.mttr_horas} h` : '—',
        ayuda: 'Horas promedio entre el reporte y la resolucion. El numero que le importa al responsable del taller.',
      }),
    );

    zonaSecundarios.append(
      numero({
        etiqueta: 'Criticas (S1)',
        valor: valor(m.criticas_abiertas),
        ayuda: 'Sin servicio: son las que no pueden esperar.',
      }),
      numero({
        etiqueta: 'Sin asignar',
        valor: valor(m.sin_asignar),
        ayuda: 'Nadie las todavia: son las que se van a perder.',
      }),
      numero({
        etiqueta: 'Fuera de SLA',
        valor: valor(m.fuera_de_sla),
        ayuda: 'Se resolvieron despues del plazo: el SLA incumplido.',
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

    zonaContenido.appendChild(
      tarjetaLista({
        titulo: 'Casos que vencen',
        subtitulo: 'El plazo sale del SLA de la severidad de cada caso.',
        elementos: casos,
        verTodos: 'Ver las incidencias',
        alVerTodos: () => irA('incidencias'),
      }),
    );

    // BLOQUE 2: la distribucion por severidad.
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
   * de `navegacion.js`: importarla desde aca crearia un ciclo (navegacion
   * importa las vistas, y las vistas importan navegacion).
   */
  function irA(vista) {
    document.dispatchEvent(new CustomEvent('app:ir-a', { detail: { vista } }));
  }

  // --- Arranque -------------------------------------------------------------
  // La solucion guardada: si el operador trabaja en Helpdesk, el tablero abre
  // en Helpdesk.
  let guardada = null;
  try {
    guardada = localStorage.getItem(CLAVE_SOLUCION);
  } catch {
    guardada = null;
  }

  const disponible = (clave) => clave !== 'helpdesk' || puedeVerIncidencias;
  solucionActual = guardada && disponible(guardada)
    ? guardada
    : Object.keys(SOLUCIONES).find(disponible);

  marcarSelector();
  cargar();
  return { elemento: raiz };
}
