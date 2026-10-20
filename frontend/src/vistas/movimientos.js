/**
 * Vista "Movimientos" — mover mercancía del inventario por producto.
 *
 * QUE PROBLEMA RESUELVE
 * ---------------------
 * El escáner mueve UN equipo por vez, leyendo su QR. Eso sirve para el campo,
 * pero no para el depósito: cuando llega una caja con 5 laptops o cuando hay que
 * asignar 3 equipos a una sede, nadie tiene los seriales a mano. Aca el operador
 * elige un PRODUCTO, una CANTIDAD y una OPERACIÓN, y el backend elige los
 * equipos y los mueve.
 *
 * POR QUE LA PANTALLA ARRANCA MOSTRANDO STOCK Y NO UN FORMULARIO
 * -------------------------------------------------------------
 * Porque antes de mover hay que saber QUE hay. Un formulario en blanco obliga al
 * operador a recordar de memoria cuantos equipos quedan de un modelo en una
 * sede, y el resultado es que pide mover 5 cuando hay 2. Con el stock a la
 * vista, la decisión se toma mirando el dato.
 *
 * LAS OPERACIONES SON LAS SIETE DEL NEGOCIO
 * -----------------------------------------
 * Entrada, salida a cliente, instalación, transferencia, retiro a taller,
 * devolución y baja. Cada una tiene su dirección (de qué estado saca y a qué
 * estado lleva), que es la razón por la que el select de origen cambia según la
 * operación: una "instalación" saca de tránsito, una "salida" saca de depósito.
 */

import { api } from '../api.js';
import { crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { descargarCsv } from '../descargas.js';

/**
 * Las operaciones de movimiento, con todo lo que la UI necesita para ofrecerlas.
 *
 * `estados_origen` NO es decorativo: es lo que permite calcular si hay stock
 * para la operación ANTES de mandarla. Sin esto, el operador elige "instalar",
 * pide 3, y el backend responde "no hay unidades en tránsito" — un error que se
 * podía prever en pantalla.
 *
 * `pide_destino` separa la baja (que no va a ninguna sede) del resto.
 * `pide_origen` es para transferencias: solo ahí importa de dónde salen.
 */
const OPERACIONES = [
  {
    valor: 'WAREHOUSE_ENTRY',
    texto: 'Entrada a depósito',
    descripcion: 'Mercancía que ya está en el sistema ingresa al depósito.',
    clases_origen: ['IN_TRANSIT', 'IN_WAREHOUSE'],
    pide_destino: true,
    icono: 'ok',
  },
  {
    valor: 'OUT_TO_CLIENT',
    texto: 'Salida a cliente',
    descripcion: 'Los equipos salen del depósito hacia una sede o cliente.',
    clases_origen: ['IN_WAREHOUSE'],
    pide_destino: true,
    pide_origen: true,
    icono: 'ubicacion',
  },
  {
    valor: 'INSTALLATION',
    texto: 'Instalación en cliente',
    descripcion: 'Los equipos se instalan en el sitio del cliente.',
    clases_origen: ['IN_TRANSIT'],
    pide_destino: true,
    icono: 'ok',
  },
  {
    valor: 'WAREHOUSE_REENTRY',
    texto: 'Devolución a depósito',
    descripcion: 'El cliente devuelve los equipos y vuelven al depósito.',
    clases_origen: ['INSTALLED', 'IN_TRANSIT', 'IN_REPAIR'],
    pide_destino: true,
    icono: 'refrescar',
  },
  {
    valor: 'WITHDRAWAL_TO_WORKSHOP',
    texto: 'Retiro a taller',
    descripcion: 'Los equipos van a reparación.',
    clases_origen: ['IN_WAREHOUSE', 'INSTALLED', 'IN_TRANSIT'],
    pide_destino: true,
    icono: 'relojito',
  },
  {
    valor: 'DISPOSAL',
    texto: 'Baja definitiva',
    descripcion: 'Los equipos se dan de baja. No vuelven al inventario.',
    clases_origen: ['IN_WAREHOUSE', 'IN_TRANSIT', 'INSTALLED', 'IN_REPAIR'],
    pide_destino: false,
    icono: 'error',
  },
];

/** El nombre legible de cada estado, para las insignias y los selects. */
const ESTADOS = {
  IN_WAREHOUSE: 'Depósito',
  IN_TRANSIT: 'En tránsito',
  INSTALLED: 'Instalado',
  IN_REPAIR: 'En taller',
};

export function vistaMovimientos({ permisos = [] } = {}) {
  const raiz = crear('div');

  const puedeMover = permisos.includes('movements:create');

  // --- Cabecera: stock por producto -----------------------------------------
  const tarjetaCabecera = crear('div', { clase: 'tarjeta' });

  const encabezado = crear('div', { clase: 'flex-entre' });
  encabezado.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Movimientos' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Entrada, salida, transferencia, devolución y baja de mercancía por producto.',
        }),
      ],
    }),
  );

  const btnActualizar = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Actualizar',
    atributos: { type: 'button', onclick: () => cargar() },
  });

  const btnExportar = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Exportar CSV',
    atributos: {
      type: 'button',
      title: 'Descargar el historial de movimientos en CSV',
      onclick: () => descargarCsv('/export/movimientos.csv', 'movimientos.csv'),
    },
  });

  encabezado.append(btnExportar, btnActualizar);

  tarjetaCabecera.append(encabezado);

  // --- Resultados -----------------------------------------------------------
  const zonaLista = crear('div');

  raiz.append(tarjetaCabecera, zonaLista);

  let peticionEnCurso = 0;
  // El stock se guarda para que el modal calcule disponibilidad sin volver a
  // pedirlo: entre que se abre el modal y se confirma, el dato no cambia tanto
  // como para justificar otra consulta, y una petición de más se ve como un
  // parpadeo.
  let filasStock = [];

  async function cargar() {
    const numero = ++peticionEnCurso;
    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando stock…' }));

    try {
      const filas = await api.resumenStock();
      if (numero !== peticionEnCurso) return;
      filasStock = filas;
      pintarStock(filas);
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  /**
   * Agrupa el stock por producto.
   *
   * El endpoint devuelve una fila por producto Y sede. Para mover, al operador le
   * importa el total por producto ("tengo 12 Dell"), y de ahí hacia abajo el
   * desglose por sede. Se agrupa acá y no en el backend porque el backend ya
   * tiene el desglose como dato primario, y colapsarlo allá obligaría a una
   * segunda consulta para ver las sedes.
   */
  function agrupar(filas) {
    const porProducto = new Map();

    for (const fila of filas) {
      const clave = fila.model_id;
      if (!porProducto.has(clave)) {
        porProducto.set(clave, {
          model_id: fila.model_id,
          sku: fila.sku,
          nombre: [fila.brand, fila.model_name].filter(Boolean).join(' '),
          categoria: fila.category_name,
          unit_cost: fila.unit_cost,
          min_stock: fila.min_stock,
          sedes: [],
          total_por_estado: {
            IN_WAREHOUSE: 0,
            IN_TRANSIT: 0,
            INSTALLED: 0,
            IN_REPAIR: 0,
          },
        });
      }

      const producto = porProducto.get(clave);
      producto.sedes.push({
        location_id: fila.location_id,
        nombre: fila.location_name,
        disponible: fila.available_count,
        en_transito: fila.in_transit_count,
        instalado: fila.installed_count,
        en_taller: fila.in_repair_count,
      });

      producto.total_por_estado.IN_WAREHOUSE += fila.available_count;
      producto.total_por_estado.IN_TRANSIT += fila.in_transit_count;
      producto.total_por_estado.INSTALLED += fila.installed_count;
      producto.total_por_estado.IN_REPAIR += fila.in_repair_count;
    }

    return [...porProducto.values()];
  }

  function pintarStock(filas) {
    const productos = agrupar(filas);

    if (!productos.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.buscar, { tamano: 28 }),
        crear('p', {
          texto: 'Todavía no hay stock cargado. Da de alta equipos desde "Nuevo componente".',
        }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const producto of productos) {
      const item = crear('li', { clase: 'lista__item' });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: producto.nombre }),
        crear('div', { clase: 'lista__meta mono', texto: producto.sku }),
      );

      // Las cuatro cifras que importan para mover. Van juntas porque la
      // decisión ("puedo sacar 3 a cliente") se lee del conjunto, no de una.
      const cifras = crear('div', { clase: 'lista__meta tabular' });
      const t = producto.total_por_estado;
      cifras.append(
        crear('span', { texto: `${t.IN_WAREHOUSE} en depósito` }),
        crear('span', { texto: ` · ${t.IN_TRANSIT} en tránsito` }),
        crear('span', { texto: ` · ${t.INSTALLED} instalados` }),
        crear('span', { texto: ` · ${t.IN_REPAIR} en taller` }),
      );
      cuerpo.append(cifras);

      // El desglose por sede, que es lo que habilita una transferencia real
      // ("esto está en Norte, no en Central").
      const sedes = producto.sedes
        .filter((s) => s.disponible + s.en_transito + s.instalado + s.en_taller > 0)
        .map((s) => `${s.nombre}: ${s.disponible + s.en_transito + s.instalado + s.en_taller}`)
        .join(' · ');
      if (sedes) {
        cuerpo.append(crear('div', { clase: 'lista__meta tenue pequeno', texto: sedes }));
      }

      item.append(cuerpo);

      if (puedeMover) {
        const btnMover = crear('button', {
          clase: 'boton',
          texto: 'Mover',
          atributos: { type: 'button', onclick: () => abrirMovimiento(producto) },
        });
        item.append(btnMover);
      }

      lista.append(item);
    }

    zonaLista.replaceChildren(lista);
  }

  // ---------------------------------------------------------------------------
  // Mover
  // ---------------------------------------------------------------------------

  async function abrirMovimiento(producto) {
    let ubicaciones = [];
    try {
      const respuesta = await api.ubicaciones();
      ubicaciones = respuesta.items ?? [];
    } catch (error) {
      avisar(`No se pudieron cargar las sedes: ${error.message}`, 'error');
      return;
    }

    const selectOperacion = crear('select', { clase: 'campo__control' });
    for (const op of OPERACIONES) {
      selectOperacion.append(
        crear('option', { texto: op.texto, atributos: { value: op.valor } }),
      );
    }

    const campoCantidad = crear('input', {
      clase: 'campo__control',
      atributos: { type: 'number', min: '1', max: '200', value: '1' },
    });

    const selectOrigen = crear('select', { clase: 'campo__control' });
    const selectDestino = crear('select', { clase: 'campo__control' });
    for (const u of ubicaciones) {
      selectOrigen.append(crear('option', { texto: u.nombre, atributos: { value: u.id } }));
      selectDestino.append(crear('option', { texto: u.nombre, atributos: { value: u.id } }));
    }
    // El destino por defecto es la sede del depósito si se puede adivinar por
    // nombre; si no, la primera. Es una comodidad: el operador casi siempre
    // mueve hacia o desde un depósito.
    const deposito = ubicaciones.find((u) => /dep[oó]sito/i.test(u.nombre));
    if (deposito) selectDestino.value = String(deposito.id);

    const campoObservaciones = crear('input', {
      clase: 'campo__control',
      atributos: { type: 'text', placeholder: 'Ej: Orden de compra 1234 (opcional)' },
    });

    const avisoDisponible = crear('div', { clase: 'resultado resultado--info' });

    const etiquetaOrigen = crear('label', {
      clase: 'campo',
      hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Desde' }), selectOrigen],
    });
    const etiquetaDestino = crear('label', {
      clase: 'campo',
      hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Hacia' }), selectDestino],
    });

    const form = crear('form', {
      clase: 'apilado',
      atributos: {
        onsubmit: async (e) => {
          e.preventDefault();
          await enviar();
        },
      },
    });

    const campoCantidadEnvuelto = crear('label', {
      clase: 'campo',
      hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Unidades' }), campoCantidad],
    });

    form.append(
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Operación' }), selectOperacion],
      }),
      campoCantidadEnvuelto,
      etiquetaOrigen,
      etiquetaDestino,
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Observaciones' }), campoObservaciones],
      }),
      avisoDisponible,
    );

    const btnMover = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Mover mercancía',
      atributos: { type: 'submit' },
    });
    form.append(btnMover);

    let cerrarModal = () => {};
    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    const operacionActual = () => OPERACIONES.find((o) => o.valor === selectOperacion.value);

    /**
     * Redibuja según la operación: qué campos se ven y cuánto hay disponible.
     *
     * Se hace en cada cambio y no una sola vez porque las operaciones son
     * distintas de verdad: una transferencia necesita origen Y destino, una baja
     * no necesita destino, y una entrada saca de tránsito. Mostrar siempre todos
     * los campos obliga al operador a adivinar cuáles importan.
     */
    function refrescar() {
      const op = operacionActual();
      const estado = op.clases_origen[0]; // el estado "natural" de la operación

      // Cuánto hay en ese estado, sumando las sedes que el backend permite.
      let disponible = 0;
      for (const sede of producto.sedes) {
        if (op.clases_origen.includes('IN_WAREHOUSE')) disponible += sede.disponible;
        if (op.clases_origen.includes('IN_TRANSIT')) disponible += sede.en_transito;
        if (op.clases_origen.includes('INSTALLED')) disponible += sede.instalado;
        if (op.clases_origen.includes('IN_REPAIR')) disponible += sede.en_taller;
      }

      avisoDisponible.replaceChildren(
        crear('span', { texto: `${op.descripcion} ` }),
        crear('strong', { texto: `Hay ${disponible} unidades que se pueden mover.` }),
      );

      campoCantidad.max = String(Math.max(disponible, 1));
      if (Number(campoCantidad.value) > disponible) campoCantidad.value = String(disponible);

      // La baja no lleva destino. El resto sí.
      etiquetaDestino.hidden = !op.pide_destino;

      // El origen solo se muestra en las operaciones donde importa de dónde
      // sale (una transferencia). En el resto el backend elige bien, y un campo
      // de más es una decisión de más que el operador puede equivocar.
      etiquetaOrigen.hidden = !op.pide_origen;
    }

    selectOperacion.addEventListener('change', refrescar);
    refrescar();

    async function enviar() {
      const op = operacionActual();
      const cantidad = parseInt(campoCantidad.value || '0', 10);

      if (!cantidad || cantidad < 1) {
        return avisar('Elegí cuántas unidades mover', 'alerta');
      }

      const cuerpo = {
        model_id: producto.model_id,
        cantidad,
        tipo_movimiento: op.valor,
      };
      if (op.pide_destino) cuerpo.ubicacion_id = parseInt(selectDestino.value, 10);
      if (op.pide_origen) cuerpo.ubicacion_origen_id = parseInt(selectOrigen.value, 10);
      const observaciones = campoObservaciones.value.trim();
      if (observaciones) cuerpo.observations = observaciones;

      // La baja definitiva saca los equipos del inventario y no tiene vuelta
      // atras: por eso pide confirmacion explicita, y el mensaje dice que
      // dejan de estar disponibles.
      const esBaja = op.valor === 'DISPOSAL';
      const ok = await confirmar({
        titulo: esBaja ? 'Confirmar baja definitiva' : 'Confirmar movimiento',
        mensaje: esBaja
          ? `¿Dar de baja ${cantidad} unidad(es) de ${producto.nombre}? Los equipos dejan de estar disponibles en el inventario.`
          : `¿Mover ${cantidad} unidad(es) de ${producto.nombre}?`,
        textoConfirmar: esBaja ? 'Dar de baja' : 'Mover mercancía',
        peligro: esBaja,
      });
      if (!ok) return;

      btnMover.disabled = true;
      btnMover.textContent = 'Moviendo…';
      try {
        const resultado = await api.moverPorProducto(cuerpo);
        // El resultado puede ser PARCIAL: 3 de 5. Se dice tal cual, con el
        // detalle de lo que falló, porque "listo" cuando fallaron 2 es la clase
        // de mensaje que hace que alguien cierre la app creyendo que terminó.
        if (resultado.fallidas > 0) {
          const detalles = resultado.resultados
            .filter((r) => !r.ok)
            .map((r) => `${r.serial_number}: ${r.error}`)
            .join('\n');
          avisar(`${resultado.mensaje}\n${detalles}`, 'alerta', 9000);
        } else {
          avisar(resultado.mensaje, 'exito', 3000);
        }
        cerrarModal();
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnMover.disabled = false;
        btnMover.textContent = 'Mover mercancía';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: `Mover ${producto.nombre}`,
      nota: 'El sistema elige los equipos que llevan más tiempo sin moverse (FIFO).',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [btnCancelar],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

  cargar();
  return { elemento: raiz };
}
