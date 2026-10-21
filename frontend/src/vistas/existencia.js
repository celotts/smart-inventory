/**
 * Vista "Existencia" — el stock, rubro por rubro.
 *
 * QUE ES
 * ------
 * La respuesta a "cuanto hay de cada cosa y donde". Una fila por
 * (producto, ubicacion): el SKU, el nombre, la ubicacion, y CUANTAS
 * unidades hay de ese producto en ese lugar, separadas por estado.
 *
 * POR QUE ES UNA PANTALLA Y NO UNA COLUMNA EN "PRODUCTOS"
 * -------------------------------------------------------
 * "Productos" es el MAESTRO: la ficha del SKU, sus costos, sus atributos.
 * La existencia es el ESTADO: cambia con cada movimiento. Meterlas juntas
 * mezcla dos preguntas ("como es este producto" y "cuanto hay"), y la
 * segunda necesitaMUCHAS mas filas: un producto en 8 ubicaciones son 8
 * filas de existencia.
 *
 * Y el dato de "cuanto hay" no estaba en ninguna parte de la app: la API lo
 * tiene (`/api/stock/summary`, que mantiene el trigger de cada movimiento),
 * pero no habia ninguna pantalla que lo mostrara. El unico lugar donde se
 * veia un numero de stock era la tarjeta "Por reponer" del tablero, y solo
 * con los productos que YA estaban por debajo del minimo: los que estan
 * bien de stock, invisibles.
 *
 * QUE SE VE
 * ---------
 * - Disponible: lo que se puede entregar hoy.
 * - En transito: salio del deposito y no llego.
 * - Instalado: esta en el cliente.
 * - En taller: roto, en el taller.
 * - El minimo y el maximo del producto, y cuanto falta para el minimo.
 * - La antiguedad de la alerta, cuando esta por debajo del minimo: lo que
 *   dice si hay que comprar hoy o el jueves.
 */

import { api } from '../api.js';
import { crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { conTooltip } from '../tooltip.js';
import { crearPaginacion } from '../paginacion.js';

export function vistaExistencia() {
  const raiz = crear('div');

  const tarjetaCabecera = crear('section', { clase: 'tarjeta' });

  const encabezado = crear('div', { clase: 'flex-entre' });
  encabezado.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Existencia' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto:
            'Cuanto hay de cada rubro y donde. El disponible es lo que se puede entregar hoy.',
        }),
      ],
    }),
  );

  const zonaKpis = crear('div', { clase: 'rejilla-kpi' });

  // --- Filtros --------------------------------------------------------------
  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por SKU, marca o modelo',
      autocomplete: 'off',
      spellcheck: 'false',
    },
  });

  const selectUbicacion = crear('select', { clase: 'campo__control' });
  selectUbicacion.append(
    crear('option', { texto: 'Todas las ubicaciones', atributos: { value: '' } }),
  );

  const chkSoloBajo = crear('input', {
    clase: 'campo__control',
    atributos: { type: 'checkbox' },
  });
  chkSoloBajo.style.width = 'auto';

  const etiquetaBusqueda = crear('label', { clase: 'campo' });
  const textoBusqueda = crear('span', { clase: 'campo__etiqueta', texto: 'Buscar' });
  etiquetaBusqueda.append(textoBusqueda, inputBuscar);
  conTooltip(textoBusqueda, 'Busca por SKU, marca o modelo.');

  const etiquetaUbicacion = crear('label', { clase: 'campo' });
  etiquetaUbicacion.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Ubicacion' }),
    selectUbicacion,
  );

  const etiquetaBajo = crear('label', { clase: 'campo' });
  const textoBajo = crear('span', { clase: 'campo__etiqueta', texto: 'Solo por debajo del minimo' });
  etiquetaBajo.append(textoBajo, chkSoloBajo);
  conTooltip(textoBajo, 'Solo los productos con el stock igual o por debajo del minimo.');

  const rejillaFiltros = crear('div', { clase: 'rejilla-filtros' });
  rejillaFiltros.append(etiquetaBusqueda, etiquetaUbicacion, etiquetaBajo);

  tarjetaCabecera.append(encabezado, zonaKpis, rejillaFiltros);

  const zonaLista = crear('div');

  // LA PAGINACION
  //
  // La existencia es la lista que mas crece: un producto por ubicacion. Con
  // 800 productos en 6 ubicaciones son 4800 filas, y pintarlas todas deja la
  // app sin responder.
  let pagina = 0;
  let limite = 50;
  let total = 0;

  const paginacion = crearPaginacion({
    total: 0,
    limite,
    pagina,
    etiqueta: 'existencias',
    alCambiarPagina: (nueva) => {
      pagina = Math.max(0, nueva);
      cargar();
    },
    alCambiarLimite: (nuevo) => {
      limite = nuevo;
      pagina = 0;
      cargar();
    },
  });

  raiz.append(tarjetaCabecera, zonaLista, paginacion.elemento);

  let peticionEnCurso = 0;

  async function cargar() {
    const numero = ++peticionEnCurso;
    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Consultando la existencia…' }));

    try {
      const [respuesta, alertas] = await Promise.all([
        api.pedir('/stock/summary', {
          params: {
            limite,
            offset: pagina * limite,
            busqueda: inputBuscar.value.trim() || undefined,
            location_id: selectUbicacion.value || undefined,
            solo_bajo: chkSoloBajo.checked || undefined,
          },
        }),
        api.pedir('/stock/alertas').catch(() => []),
      ]);

      if (numero !== peticionEnCurso) return;

      const stock = respuesta.items ?? respuesta;
      total = respuesta.total ?? stock.length;

      // El filtro de ubicaciones se arma con las que VIENEN en la pagina: si
      // una ubicacion no tiene nada en esta pagina, no hay fila que filtrar.
      const valorUbicacion = selectUbicacion.value;
      selectUbicacion.replaceChildren(
        crear('option', { texto: 'Todas las ubicaciones', atributos: { value: '' } }),
      );
      const nombres = [...new Map(stock.map((f) => [f.location_id, f.location_name])).entries()]
        .filter(([, nombre]) => nombre)
        .sort((a, b) => String(a[1]).localeCompare(String(b[1])));
      for (const [id, nombre] of nombres) {
        selectUbicacion.append(crear('option', { texto: nombre, atributos: { value: String(id) } }));
      }
      if (valorUbicacion) selectUbicacion.value = valorUbicacion;

      const alertaPor = new Map(
        alertas.map((a) => [`${a.model_id}:${a.location_id}`, a]),
      );

      // El FILTRO ya lo hizo el servidor (por eso la pagina es correcta):
      // aca solo queda el ORDEN, que es de presentacion.
      const filas = stock
        .filter((f) => (chkSoloBajo.checked ? f.stock_status === 'LOW' : true))
        .sort((a, b) => {
          // Primero lo que esta por debajo del minimo, y dentro de eso, lo
          // que lleva mas tiempo por debajo: es lo que hay que resolver.
          if (a.stock_status === 'LOW' && b.stock_status !== 'LOW') return -1;
          if (b.stock_status === 'LOW' && a.stock_status !== 'LOW') return 1;
          return (a.brand ?? '').localeCompare(b.brand ?? '') || (a.model_name ?? '').localeCompare(b.model_name ?? '');
        });

      pintarKpis(filas);
      pintarLista(filas, alertaPor);
      paginacion.actualizar({ total, limite, pagina });
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  function pintarKpis(filas) {
    zonaKpis.replaceChildren();

    const disponible = filas.reduce((a, f) => a + Number(f.available_count || 0), 0);
    const enSitio = filas.reduce((a, f) => a + Number(f.installed_count || 0), 0);
    const enTaller = filas.reduce((a, f) => a + Number(f.in_repair_count || 0), 0);
    const bajos = filas.filter((f) => f.stock_status === 'LOW').length;

    const tarjetas = [
      { etiqueta: 'Disponible', valor: disponible, ayuda: 'Unidades listas para entregar hoy.' },
      { etiqueta: 'Instalados', valor: enSitio, ayuda: 'Equipos trabajando en el cliente.' },
      { etiqueta: 'En taller', valor: enTaller, ayuda: 'Equipos rotos, esperando el taller.' },
      { etiqueta: 'Por reponer', valor: bajos, ayuda: 'Productos con el stock igual o por debajo del minimo.' },
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

  function pintarLista(filas, alertaPor) {
    if (!filas.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.ok, { tamano: 28 }),
        crear('p', { texto: 'No hay existencia con esos filtros.' }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const f of filas) {
      const item = crear('li', { clase: 'lista__item' });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      const titulo = crear('div', { clase: 'lista__titulo' });
      titulo.append(
        crear('span', { clase: 'mono tenue pequeno', texto: `${f.sku ?? ''} · ` }),
        crear('span', { texto: `${f.brand ?? ''} ${f.model_name ?? ''}`.trim() }),
      );

      const meta = crear('div', { clase: 'lista__meta' });
      meta.append(
        crear('span', { texto: `${f.location_name ?? 'Sin ubicacion'} · ${f.category_name ?? ''}` }),
      );

      // EL DESGLOSE: lo que hay de este rubro, separado por estado.
      //
      // Un solo numero no sirve: "12" puede ser 12 para entregar o 12
      // instalados en un cliente. Lo que el deposito necesita es el
      // disponible, y lo que el taller necesita es lo que esta roto.
      const desglose = crear('div', { clase: 'lista__meta mono' });
      desglose.append(
        crear('span', {
          texto:
            `Disp ${f.available_count ?? 0} · ` +
            `Tránsito ${f.in_transit_count ?? 0} · ` +
            `Instalado ${f.installed_count ?? 0} · ` +
            `Taller ${f.in_repair_count ?? 0}`,
        }),
      );

      cuerpo.append(titulo, meta, desglose);
      item.append(cuerpo);

      // La insignia: el estado del stock contra el minimo.
      const bajo = f.stock_status === 'LOW';
      const insignia = crear('span', {
        clase: `insignia ${bajo ? 'insignia--peligro' : 'insignia--instalado'}`,
        texto: bajo ? `Faltan ${Math.max(0, (f.min_stock ?? 0) - (f.available_count ?? 0))}` : 'OK',
      });
      conTooltip(
        insignia,
        bajo
          ? `Minimo ${f.min_stock}: hay ${f.available_count} disponibles.`
          : `Minimo ${f.min_stock}, maximo ${f.max_stock}: hay ${f.available_count} disponibles.`,
      );
      item.append(insignia);

      // La antiguedad de la alerta: lo que decide si hay que comprar hoy.
      const alerta = alertaPor.get(`${f.model_id}:${f.location_id}`);
      if (alerta) {
        const dias = crear('span', {
          clase: `insignia ${alerta.reposicion_tarde ? 'insignia--peligro' : 'insignia--alerta'}`,
          texto: `${alerta.dias_bajo_minimo} d bajo minimo`,
        });
        conTooltip(
          dias,
          alerta.reposicion_tarde
            ? `Hace ${alerta.dias_bajo_minimo} dias esta bajo el minimo y el proveedor tarda ${alerta.lead_time_days ?? '?'} dias: la reposicion llega TARDE.`
            : `Hace ${alerta.dias_bajo_minimo} dias esta bajo el minimo. El proveedor tarda ${alerta.lead_time_days ?? '?'} dias.`,
        );
        item.append(dias);
      }

      lista.append(item);
    }

    zonaLista.replaceChildren(lista);
  }

  inputBuscar.addEventListener('input', () => cargar());
  selectUbicacion.addEventListener('change', () => cargar());
  chkSoloBajo.addEventListener('change', () => cargar());

  cargar();
  return { elemento: raiz };
}
