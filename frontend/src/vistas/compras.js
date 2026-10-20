/**
 * Vista "Compras" — Órdenes de compra y recepción de mercancía.
 *
 * EL FLUJO DE "CARGAR MERCANCIA AL INVENTARIO"
 * -------------------------------------------
 *  1. Se crea una orden de compra (que se espera recibir).
 *  2. Cuando llega la mercancía, se "recibe" la orden: por cada unidad se crea un
 *     equipo con su serial y se registra un movimiento WAREHOUSE_ENTRY. El stock
 *     agregado por producto/ubicación se actualiza solo, por trigger.
 *
 * POR QUE RECIBIR CREA EQUIPOS Y NO SOLO SUMAR UN NUMERO
 * -----------------------------------------------------
 * El sistema es de inventario por serial: cada equipo existe como fila y tiene
 * trazabilidad (donde estuvo, quien lo movio). Sumar un contador suelto daria un
 * stock que nadie puede auditar. Los seriales se generan si no se tipean: el
 * operador que recibe 30 equipos no tiene por que escribir 30 seriales a mano.
 *
 * Solo visible para quien tiene `movements:create` (ADMIN u OPERATOR).
 */

import { api } from '../api.js';
import { ICONOS, crear, etiquetaProducto, icono } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';

const ESTADOS_ORDEN = {
  BORRADOR: { texto: 'Borrador', clase: 'insignia--deposito' },
  ENVIADA: { texto: 'Enviada', clase: 'insignia--transito' },
  CONFIRMADA: { texto: 'Confirmada', clase: 'insignia--transito' },
  RECIBIDA: { texto: 'Recibida', clase: 'insignia--instalado' },
  CANCELADA: { texto: 'Cancelada', clase: 'insignia--baja' },
};

export function vistaCompras({ permisos = [] }) {
  const raiz = crear('div');

  const puedeCrear = permisos.includes('movements:create');

  const tarjetaCabecera = crear('div', { clase: 'tarjeta' });

  const encabezado = crear('div', { clase: 'flex-entre' });
  encabezado.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Compras' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Órdenes de compra y recepción de mercancía al inventario.',
        }),
      ],
    }),
  );

  if (puedeCrear) {
    const btnNueva = crear('button', {
      clase: 'boton',
      texto: 'Nueva orden',
      atributos: { type: 'button', onclick: abrirNuevaOrden },
    });
    encabezado.append(btnNueva);
  }

  const btnRecargar = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Actualizar',
    atributos: { type: 'button', onclick: () => cargar() },
  });

  tarjetaCabecera.append(encabezado, crear('div', { clase: 'fila-botones', estilo: 'margin-top:0.75rem', hijos: [btnRecargar] }));

  const zonaLista = crear('div');
  raiz.append(tarjetaCabecera, zonaLista);

  let peticionEnCurso = 0;

  async function cargar() {
    const numero = ++peticionEnCurso;
    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando órdenes…' }));

    try {
      const ordenes = await api.ordenesCompra();
      if (numero !== peticionEnCurso) return;
      pintarLista(ordenes);
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  function pintarLista(ordenes) {
    if (!ordenes.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.buscar, { tamano: 28 }),
        crear('p', {
          texto: puedeCrear
            ? 'No hay órdenes de compra. Crea una con "Nueva orden" para recibir mercancía.'
            : 'No hay órdenes de compra.',
        }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const o of ordenes) {
      const estado = ESTADOS_ORDEN[o.status] ?? { texto: o.status, clase: 'insignia--baja' };
      const recibible = o.status !== 'RECIBIDA' && o.status !== 'CANCELADA';

      const boton = crear('button', {
        clase: 'lista__item',
        atributos: {
          type: 'button',
          onclick: () => verOrden(o.id),
        },
      });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: `Orden #${o.id} · ${o.vendor_name ?? 'Sin proveedor'}` }),
        crear('div', {
          clase: 'lista__meta',
          texto: o.expected_date ? `Entrega esperada: ${o.expected_date}` : 'Sin fecha de entrega',
        }),
        crear('div', {
          clase: 'lista__meta tabular',
          texto: o.total_estimado ? `Total estimado: $${Number(o.total_estimado).toLocaleString('es-AR')}` : '',
        }),
      );

      boton.append(
        cuerpo,
        crear('span', { clase: `insignia ${estado.clase}`, texto: estado.texto }),
      );
      lista.append(crear('li', { hijos: [boton] }));
    }

    zonaLista.replaceChildren(lista);
  }

  // --- Detalle + recepción --------------------------------------------------
  async function verOrden(id) {
    let orden;
    try {
      orden = await api.obtenerOrden(id);
    } catch (error) {
      avisar(error.message, 'error');
      return;
    }

    const estado = ESTADOS_ORDEN[orden.status] ?? { texto: orden.status, clase: 'insignia--baja' };
    const pendientes = orden.lineas.filter((l) => l.pendiente > 0);

    const cuerpo = crear('div');

    const def = crear('dl', { clase: 'def' });
    const fila = (clave, valor) => {
      def.append(crear('dt', { texto: clave }), crear('dd', { texto: valor ?? '—' }));
    };
    fila('Proveedor', orden.vendor_name);
    fila('Estado', estado.texto);
    fila('Entrega esperada', orden.expected_date);
    cuerpo.append(def);

    // Inputs de cantidad a recibir por línea, para lo que queda pendiente.
    const inputs = [];

    const lista = crear('ul', { clase: 'lista' });
    for (const l of orden.lineas) {
      const item = crear('li', { clase: 'lista__item' });

      const info = crear('div', { clase: 'lista__cuerpo' });
      info.append(
        crear('div', { clase: 'lista__titulo', texto: `${l.brand} ${l.model_name}` }),
        crear('div', { clase: 'lista__meta mono', texto: l.sku }),
        crear('div', {
          clase: 'lista__meta tabular',
          texto: `Pedido ${l.cantidad} · Recibido ${l.cantidad_recibida} · Pendiente ${l.pendiente}`,
        }),
      );
      item.append(info);

      if (puedeCrear && l.pendiente > 0) {
        const input = crear('input', {
          clase: 'campo__control tabular',
          estilo: 'max-width:90px',
          atributos: { type: 'number', min: '0', max: String(l.pendiente), value: String(l.pendiente), 'aria-label': `Cantidad a recibir de ${l.sku}` },
        });
        inputs.push({ linea: l, input });
        item.append(input);
      }

      lista.append(item);
    }
    cuerpo.append(lista);

    const acciones = [];

    if (puedeCrear && pendientes.length) {
      const btnRecibir = crear('button', {
        clase: 'boton boton--exito',
        texto: 'Recibir mercancía',
        atributos: { type: 'button' },
      });
      acciones.push(btnRecibir);

      let cerrarModal = () => {};
      btnRecibir.addEventListener('click', async () => {
        const lineas = inputs
          .map(({ linea, input }) => ({
            po_line_id: linea.id,
            cantidad_recibida: parseInt(input.value || '0', 10),
          }))
          .filter((l) => l.cantidad_recibida > 0);

        if (!lineas.length) {
          avisar('Indica al menos una cantidad a recibir', 'alerta');
          return;
        }

        // Recibir crea equipos con serial propio: es irreversible y genera
        // movimientos de entrada. El operador tiene que confirmarlo.
        const total = lineas.reduce((suma, l) => suma + l.cantidad_recibida, 0);
        const ok = await confirmar({
          titulo: 'Recibir mercancia',
          mensaje: `¿Registrar la entrada de ${total} equipo(s) al inventario? Cada uno se crea con su serial y genera un movimiento de entrada.`,
          textoConfirmar: 'Recibir',
        });
        if (!ok) return;

        btnRecibir.disabled = true;
        btnRecibir.textContent = 'Recibiendo…';
        try {
          const res = await api.recibirOrden(id, { lineas });
          avisar(`${res.devices_creados} equipos ingresados al inventario`, 'exito', 3000);
          cerrarModal();
          cargar();
        } catch (error) {
          avisar(error.message, 'error');
          btnRecibir.disabled = false;
          btnRecibir.textContent = 'Recibir mercancía';
        }
      });

      abrirDialogo({
        titulo: `Orden #${orden.id}`,
        nota: 'Al recibir, cada unidad se crea como equipo con serial propio y se registra su entrada al depósito.',
        cuerpo: [cuerpo],
        clase: 'modal--formulario',
        acciones,
        alAbrir: (_modal, cerrar) => {
          cerrarModal = cerrar;
        },
      });
      return;
    }

    // Orden ya completa (o usuario sin permiso): solo mostrar.
    acciones.push(
      crear('button', {
        clase: 'boton boton--suave',
        texto: 'Cerrar',
        atributos: { type: 'button' },
      }),
    );
    const cerrar = abrirDialogo({
      titulo: `Orden #${orden.id}`,
      cuerpo: [cuerpo],
      clase: 'modal--formulario',
      acciones,
      alAbrir: (_modal, cerrarModal) => {
        acciones[acciones.length - 1].addEventListener('click', cerrarModal);
      },
    });
  }

  // --- Nueva orden ----------------------------------------------------------
  async function abrirNuevaOrden() {
    let productos = [];
    let proveedores = [];
    try {
      [productos, proveedores] = await Promise.all([api.productos(), api.proveedores()]);
    } catch (error) {
      avisar(`No se pudo cargar el catalogo: ${error.message}`, 'error');
      return;
    }

    if (!productos.length) {
      avisar('Primero crea al menos un producto en la pestaña "Productos"', 'alerta');
      return;
    }
    if (!proveedores.length) {
      avisar('No hay proveedores. Crea uno desde la API o la base antes de comprar.', 'alerta');
      return;
    }

    const selectProveedor = crear('select', { clase: 'campo__control' });
    for (const v of proveedores) {
      selectProveedor.append(crear('option', { texto: v.name, atributos: { value: v.id } }));
    }

    const selectProducto = crear('select', { clase: 'campo__control' });
    for (const p of productos) {
      selectProducto.append(
        crear('option', { texto: etiquetaProducto(p), atributos: { value: p.id } }),
      );
    }

    const campoCantidad = crear('input', {
      clase: 'campo__control tabular',
      atributos: { type: 'number', min: '1', value: '1' },
    });
    const campoCosto = crear('input', {
      clase: 'campo__control tabular',
      atributos: { type: 'number', min: '0', step: '0.01', placeholder: 'Costo unitario' },
    });
    const campoFecha = crear('input', {
      clase: 'campo__control',
      atributos: { type: 'date' },
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

    form.append(
      crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Proveedor *' }), selectProveedor] }),
      crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Producto *' }), selectProducto] }),
      crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Cantidad *' }), campoCantidad] }),
      crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Costo unitario' }), campoCosto] }),
      crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Fecha esperada de entrega' }), campoFecha] }),
    );

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Crear orden',
      atributos: { type: 'submit' },
    });
    form.append(btnGuardar);

    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    let cerrarModal = () => {};

    async function enviar() {
      const cantidad = parseInt(campoCantidad.value || '0', 10);
      if (cantidad < 1) return avisar('La cantidad debe ser al menos 1', 'alerta');

      const ok = await confirmar({
        titulo: 'Crear orden de compra',
        mensaje: `¿Crear la orden de compra por ${cantidad} unidad(es)?`,
        textoConfirmar: 'Crear orden',
      });
      if (!ok) return;

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Creando…';
      try {
        await api.crearOrden({
          vendor_id: parseInt(selectProveedor.value, 10),
          lineas: [{
            model_id: parseInt(selectProducto.value, 10),
            cantidad,
            unit_cost: campoCosto.value ? parseFloat(campoCosto.value) : 0,
          }],
          expected_date: campoFecha.value || undefined,
        });
        avisar('Orden creada. Cuando llegue la mercancía, recibila desde la lista.', 'exito', 3500);
        cerrarModal();
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Crear orden';
      }
    }

    abrirDialogo({
      titulo: 'Nueva orden de compra',
      nota: 'Una línea por orden. Para varios productos, crea varias órdenes o usa la API.',
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
