/**
 * Vista "Inicio" — El tablero.
 *
 * QUE ES
 * ------
 * La primera pantalla que ve el operador. Responde, en este orden:
 *
 *   1. ¿Cuanto equipo tengo y donde esta?
 *   2. ¿Que se esta por quedarse sin stock?
 *   3. ¿Que paso hace un rato?
 *
 * POR QUE ES UNA SOLA LLAMADA
 * ---------------------------
 * La pantalla necesita varias consultas. Si cada una fuera un endpoint
 * distinto, abrirla haria cinco pedidos, y en un celular con 4G eso son cinco
 * esperas: la pantalla tarda medio minuto en aparecer, o aparece a medias con
 * la mitad de los numeros.
 *
 * POR QUE NO ES EL REPORTE DE LA IA
 * ---------------------------------
 * El reporte de la IA redacta en prosa y tarda. Este tablero es el dato crudo,
 * para el operador que tiene que actuar en el momento. Son dos preguntas
 * distintas: "que pasa" (esta) y "explicame que pasa" (la IA).
 */

import { api } from '../api.js';
import { ESTADOS, crear } from '../dom.js';
import { descargarCsv } from '../descargas.js';
import { conTooltip } from '../tooltip.js';

export { descargarCsv };

/**
 * Etiqueta legible de un estado, con su insignia.
 */
function estadoDe(clave) {
  return ESTADOS[clave] ?? { texto: clave, clase: 'insignia--baja' };
}

export function vistaInicio({ alAbrirDispositivo } = {}) {
  const raiz = crear('div');

  const zona = crear('div', { clase: 'apilado' });
  raiz.append(zona);

  async function cargar() {
    zona.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando tablero…' }));

    let datos;
    try {
      datos = await api.pedir('/reports/dashboard');
    } catch (error) {
      zona.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
      return;
    }

    zona.replaceChildren(
      seccionKpis(datos.kpis ?? {}),
      seccionEstado(datos.stock_por_estado ?? []),
      seccionCriticos(datos.productos_criticos ?? []),
      seccionAlertas(datos.alertas ?? []),
      seccionMovimientos(datos.movimientos_recientes ?? []),
    );
  }

  // --- KPIs -----------------------------------------------------------------
  function seccionKpis(kpis) {
    const tarjeta = crear('section', { clase: 'tarjeta' });

    tarjeta.append(
      crear('h2', { clase: 'tarjeta__titulo', texto: 'Resumen' }),
      crear('p', {
        clase: 'tarjeta__subtitulo',
        texto: 'El estado del inventario ahora mismo.',
      }),
    );

    const rejilla = crear('div', { clase: 'rejilla-kpi' });

    const valor = (kpi) => (kpi === null || kpi === undefined ? '—' : String(kpi));

    // Cada numero lleva su explicacion: un "0" al lado de "Mantenimientos
    // pendientes" no dice si es bueno o malo, y el operador no tiene tiempo
    // de averiguar que mide cada cosa.
    const tarjetas = [
      {
        etiqueta: 'Equipos',
        valor: valor(kpis.total_devices),
        ayuda: 'Componentes activos en el inventario, sin los dados de baja.',
      },
      {
        etiqueta: 'En deposito',
        valor: valor(kpis.en_deposito),
        ayuda: 'Disponibles para salir. Es el stock que se puede entregar hoy.',
      },
      {
        etiqueta: 'En transito',
        valor: valor(kpis.en_transit),
        ayuda: 'Salieron del deposito hacia el cliente, todavia no instalados.',
      },
      {
        etiqueta: 'Instalados',
        valor: valor(kpis.instalados),
        ayuda: 'Working en sitio del cliente.',
      },
      {
        etiqueta: 'En taller',
        valor: valor(kpis.en_taller),
        ayuda: 'En reparacion: no se pueden mover hasta que se reparen.',
      },
      {
        etiqueta: 'Movimientos (30 d)',
        valor: valor(kpis.movimientos_30d),
        ayuda: 'Escaneos y traslados registrados en los ultimos 30 dias.',
      },
      {
        etiqueta: 'Mantenimientos pendientes',
        valor: valor(kpis.mantenimientos_pendientes),
        ayuda: 'Planes de mantenimiento que ya estan vencidos o por vencer.',
      },
      {
        etiqueta: 'Ordenes de compra abiertas',
        valor: valor(kpis.pos_abiertas),
        ayuda: 'Pedidos a proveedores que todavia no llegaron.',
      },
    ];

    for (const t of tarjetas) {
      // `tabindex="0"`: la tarjeta lleva una descripcion (el tooltip), y sin
      // esto el teclado no puede llegar a ella. Un `div` no es enfocable, y
      // el tooltip de una tarjeta que solo se ve al pasar el mouse es
      // informacion que el teclado no tiene.
      const caja = crear('div', {
        clase: 'kpi',
        atributos: { tabindex: '0' },
      });
      caja.append(
        crear('div', { clase: 'kpi__valor', texto: t.valor }),
        crear('div', { clase: 'kpi__etiqueta', texto: t.etiqueta }),
      );
      // El tooltip es la DESCRIPCION del numero: el nombre del KPI sigue
      // siendo el texto, completo, sin depender de que el mouse pase por
      // encima.
      conTooltip(caja, t.ayuda);
      rejilla.append(caja);
    }

    tarjeta.append(rejilla);
    return tarjeta;
  }

  // --- Stock por estado -----------------------------------------------------
  function seccionEstado(stock) {
    const tarjeta = crear('section', { clase: 'tarjeta' });
    tarjeta.append(crear('h2', { clase: 'tarjeta__titulo', texto: 'Equipos por estado' }));

    if (!stock.length) {
      tarjeta.append(crear('p', { clase: 'tenue', texto: 'No hay equipos en el inventario.' }));
      return tarjeta;
    }

    const total = stock.reduce((suma, s) => suma + Number(s.cantidad || 0), 0) || 1;

    const lista = crear('ul', { clase: 'lista' });
    for (const s of stock) {
      const info = estadoDe(s.estado);
      const porcentaje = Math.round((Number(s.cantidad || 0) / total) * 100);

      const item = crear('li', { clase: 'lista__item' });
      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: info.texto }),
        crear('div', { clase: 'lista__meta', texto: `${s.cantidad} equipos · ${porcentaje}%` }),
      );

      const insignia = crear('span', {
        clase: `insignia ${info.clase}`,
        texto: String(s.cantidad),
      });
      conTooltip(insignia, `${info.texto}: ${s.cantidad} equipos.`);

      item.append(cuerpo, insignia);
      lista.append(item);
    }

    tarjeta.append(lista);
    return tarjeta;
  }

  // --- Productos criticos ---------------------------------------------------
  function seccionCriticos(productos) {
    const tarjeta = crear('section', { clase: 'tarjeta' });
    tarjeta.append(
      crear('h2', { clase: 'tarjeta__titulo', texto: 'Por reponer' }),
      crear('p', {
        clase: 'tarjeta__subtitulo',
        texto: 'Productos que estan en o por debajo del minimo.',
      }),
    );

    if (!productos.length) {
      tarjeta.append(crear('p', { clase: 'tenue', texto: 'Todos los productos estan por encima del minimo.' }));
      return tarjeta;
    }

    const lista = crear('ul', { clase: 'lista' });
    for (const p of productos) {
      const item = crear('li', { clase: 'lista__item' });
      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: `${p.brand} ${p.model}` }),
        crear('div', { clase: 'lista__meta mono', texto: p.sku }),
        crear('div', {
          clase: 'lista__meta',
          texto: `Disponibles ${p.disponibles} · Minimo ${p.min_stock} · Lead time ${p.lead_time_days ?? '?'} d`,
        }),
      );
      const insignia = crear('span', {
        clase: 'insignia insignia--peligro',
        texto: `Faltan ${p.faltante}`,
      });
      item.append(cuerpo, insignia);
      lista.append(item);
    }

    tarjeta.append(lista);
    return tarjeta;
  }

  // --- Alertas --------------------------------------------------------------
  function seccionAlertas(alertas) {
    const tarjeta = crear('section', { clase: 'tarjeta' });
    tarjeta.append(crear('h2', { clase: 'tarjeta__titulo', texto: 'Alertas' }));

    if (!alertas.length) {
      tarjeta.append(crear('p', { clase: 'tenue', texto: 'Sin alertas.' }));
      return tarjeta;
    }

    const lista = crear('ul', { clase: 'lista' });
    for (const a of alertas) {
      const item = crear('li', { clase: 'lista__item' });
      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: `${a.tipo} · ${a.sku}` }),
        crear('div', { clase: 'lista__meta', texto: `${a.modelo ?? ''} · ${a.ubicacion ?? ''}` }),
      );
      item.append(cuerpo);
      lista.append(item);
    }

    tarjeta.append(lista);
    return tarjeta;
  }

  // --- Movimientos recientes ------------------------------------------------
  function seccionMovimientos(movimientos) {
    const tarjeta = crear('section', { clase: 'tarjeta' });
    tarjeta.append(
      crear('h2', { clase: 'tarjeta__titulo', texto: 'Ultimos movimientos' }),
    );

    if (!movimientos.length) {
      tarjeta.append(crear('p', { clase: 'tenue', texto: 'Todavia no hay movimientos.' }));
      return tarjeta;
    }

    const lista = crear('ul', { clase: 'lista' });
    for (const m of movimientos) {
      const item = crear('li', { clase: 'lista__item' });
      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: m.movimiento ?? '' }),
        crear('div', { clase: 'lista__meta mono', texto: m.serial_number ?? '' }),
        crear('div', {
          clase: 'lista__meta',
          texto: [m.origen, m.destino].filter(Boolean).join(' → ') || '—',
        }),
      );
      item.append(cuerpo);
      lista.append(item);
    }

    tarjeta.append(lista);
    return tarjeta;
  }

  cargar();
  return { elemento: raiz };
}
