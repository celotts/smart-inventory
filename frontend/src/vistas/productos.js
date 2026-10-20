/**
 * Vista "Productos" — Maestro de productos (SKUs).
 *
 * QUE ES UN PRODUCTO ACA
 * ----------------------
 * Un producto es una combinacion marca + modelo + SKU con su categoria, sus
 * puntos de reposicion (min/max) y su costo. NO es un equipo fisico: cada laptop
 * con su serial es un `device` que apunta a un producto. El stock agregado
 * ("tengo 5 Dell Latitude en deposito") se cuenta por producto, no por serial.
 *
 * POR QUE HAY DOS PASOS Y NO UNO
 * ------------------------------
 * Primero se da de alta el producto (el "que"), y despues se cargan las unidades
 * (el "cuanto") desde Compras. Mezclar los dos en un formulario obliga a cargar
 * un equipo nuevo cada vez que llega mercancia de un producto que ya existe.
 *
 * Solo visible/usable por quien tiene `devices:edit` (ADMIN).
 */

import { api } from '../api.js';
import { ICONOS, crear, etiquetaProducto, icono } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { imagenBarcodeProducto, imagenQrProducto, svgsDeEtiqueta } from '../etiqueta.js';

export function vistaProductos({ permisos = [] }) {
  const raiz = crear('div');

  const puedeEditar = permisos.includes('devices:edit');

  // --- Cabecera con busqueda y boton de alta --------------------------------
  const tarjetaCabecera = crear('div', { clase: 'tarjeta' });

  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por marca, modelo o SKU',
      autocomplete: 'off',
      spellcheck: 'false',
    },
  });

  const etiquetaBusqueda = crear('label', { clase: 'campo' });
  etiquetaBusqueda.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Buscar' }),
    inputBuscar,
  );

  const encabezado = crear('div', { clase: 'flex-entre' });
  encabezado.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Productos' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Maestro de SKUs. Aca se define que se puede cargar al inventario.',
        }),
      ],
    }),
  );

  if (puedeEditar) {
    const btnNuevo = crear('button', {
      clase: 'boton',
      texto: 'Nuevo producto',
      atributos: { type: 'button', onclick: abrirAlta },
    });
    encabezado.append(btnNuevo);
  }

  tarjetaCabecera.append(encabezado, etiquetaBusqueda);

  // --- Resultados -----------------------------------------------------------
  const zonaLista = crear('div');

  raiz.append(tarjetaCabecera, zonaLista);

  let peticionEnCurso = 0;

  async function cargar() {
    const numero = ++peticionEnCurso;
    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando productos…' }));

    try {
      const productos = await api.productos({ busqueda: inputBuscar.value.trim() || undefined });
      if (numero !== peticionEnCurso) return;
      pintarLista(productos);
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  function pintarLista(productos) {
    if (!productos.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.buscar, { tamano: 28 }),
        crear('p', {
          texto: 'No hay productos. Crea uno con "Nuevo producto" para poder cargar mercancia.',
        }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const p of productos) {
      const item = crear('li', { clase: 'lista__item' });

      // El QR del producto, chiquito, al lado del nombre: es la etiqueta de la
      // caja, y de un vistazo dice "este producto ya esta identificado". Se
      // pide perezoso (cuando la fila entra en pantalla) porque una lista de
      // cincuenta productos son cincuenta peticiones de imagen.
      const qr = imagenQrProducto(p.id, p.qr_code || p.sku, 56, { perezoso: true });
      qr.classList.add('lista__qr');

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: `${p.brand} ${p.model}` }),
        crear('div', { clase: 'lista__meta mono', texto: p.sku }),
        crear('div', {
          clase: 'lista__meta',
          texto: [p.categoria, p.variant].filter(Boolean).join(' · '),
        }),
        crear('div', {
          clase: 'lista__meta tabular',
          texto: `Min ${p.min_stock} · Max ${p.max_stock} · ${p.equipos} equipos` +
            (p.unit_cost ? ` · $${Number(p.unit_cost).toLocaleString('es-AR')}` : ''),
        }),
        crear('div', {
          clase: 'lista__meta mono',
          texto: `QR ${p.qr_code || '—'} · Barras ${p.barcode || '—'}`,
        }),
      );

      item.append(qr, cuerpo);

      const btnEtiqueta = crear('button', {
        clase: 'boton boton--suave boton--chico',
        texto: 'Etiqueta',
        atributos: { type: 'button', onclick: () => abrirEtiqueta(p) },
      });
      item.append(btnEtiqueta);

      if (puedeEditar) {
        const btnEditar = crear('button', {
          clase: 'boton boton--suave boton--chico',
          texto: 'Editar',
          atributos: { type: 'button', onclick: () => abrirEdicion(p) },
        });
        const btnDescontinuar = crear('button', {
          clase: 'boton boton--chico boton--peligro',
          texto: p.is_active ? 'Descontinuar' : 'Reactivar',
          atributos: { type: 'button', onclick: () => confirmarAlternarActivo(p) },
        });
        const btnEliminar = crear('button', {
          clase: 'boton boton--chico boton--peligro',
          texto: 'Eliminar',
          atributos: { type: 'button', onclick: () => confirmarEliminarProducto(p) },
        });
        item.append(btnEditar, btnDescontinuar, btnEliminar);
      }
      lista.append(item);
    }

    zonaLista.replaceChildren(lista);
  }

  // --- Etiqueta del producto -------------------------------------------------
  //
  // La etiqueta del PRODUCTO: la de la caja, la del rack. No es la del
  // equipo, que se imprime desde la ficha del componente y lleva el
  // `qr_code` del equipo (`INV-...`).
  async function abrirEtiqueta(p) {
    const cuerpo = crear('div', { clase: 'etiqueta' });

    const datos = crear('div', { clase: 'etiqueta__datos' });
    datos.append(
      crear('div', { clase: 'etiqueta__nombre', texto: `${p.brand} ${p.model}` }),
      crear('div', { clase: 'etiqueta__meta mono', texto: `SKU ${p.sku}` }),
      crear('div', {
        clase: 'etiqueta__meta',
        texto: [p.categoria, p.variant].filter(Boolean).join(' · '),
      }),
    );

    const qr = imagenQrProducto(p.id, p.qr_code || p.sku, 180);
    const barras = imagenBarcodeProducto(p.id, p.barcode || p.sku, 280);

    const codigos = crear('div', { clase: 'etiqueta__codigos mono' });
    codigos.append(
      crear('div', { texto: `QR: ${p.qr_code || p.sku}` }),
      crear('div', { texto: `Barras: ${p.barcode || p.sku}` }),
    );

    cuerpo.append(datos, qr, barras, codigos);

    const btnImprimir = crear('button', {
      clase: 'boton',
      texto: 'Imprimir',
      atributos: { type: 'button' },
    });
    const btnCerrar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cerrar',
      atributos: { type: 'button' },
    });

    abrirDialogo({
      titulo: `Etiqueta — ${p.brand} ${p.model}`,
      nota: 'La etiqueta del producto: la de la caja, no la del equipo.',
      cuerpo: [cuerpo],
      clase: 'modal--etiqueta',
      acciones: [btnCerrar, btnImprimir],
      alAbrir: (_modal, cerrar) => {
        btnCerrar.addEventListener('click', cerrar);
        btnImprimir.addEventListener('click', () => imprimirEtiqueta(p));
      },
    });
  }

  /**
   * Abre la etiqueta en una ventana aparte y la imprime.
   *
   * POR QUE UNA VENTANA NUEVA Y NO UN `window.print()` DE LA PAGINA
   * ---------------------------------------------------------------
   * Imprimir la pagina entera imprime la barra de navegacion, la tabla de
   * productos y toda la interfaz: en la impresora de un deposito eso es un
   * papel con diez paginas de ruido. La etiqueta tiene que SALIR sola.
   *
   * La ventana nueva no puede pedir los SVG a la API —no tiene el token—,
   * asi que se traen antes y se escriben en el documento nuevo.
   */
  async function imprimirEtiqueta(p) {
    let svgs;
    try {
      svgs = await svgsDeEtiqueta(p.id);
    } catch (error) {
      avisar(`No se pudo preparar la etiqueta: ${error.message}`, 'error');
      return;
    }

    const ventana = window.open('', '_blank', 'width=460,height=620');
    if (!ventana) {
      avisar('El navegador bloqueó la ventana de impresión.', 'error');
      return;
    }

    const doc = ventana.document;
    doc.title = `Etiqueta ${p.sku || p.model || ''}`;

    const estilo = doc.createElement('style');
    estilo.textContent = `
      body { font-family: system-ui, sans-serif; margin: 16px; color: #000; }
      .etiqueta { display: flex; flex-direction: column; align-items: center; gap: 10px; }
      .nombre { font-size: 15px; font-weight: 700; text-align: center; }
      .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
      .qr { width: 190px; height: 190px; }
      .barras { width: 100%; max-width: 320px; height: auto; }
      .codigos { text-align: center; }
      @page { margin: 8mm; }
    `;
    doc.head.appendChild(estilo);

    const caja = doc.createElement('div');
    caja.className = 'etiqueta';

    const nombre = doc.createElement('div');
    nombre.className = 'nombre';
    nombre.textContent = `${p.brand} ${p.model}`;
    caja.appendChild(nombre);

    const sku = doc.createElement('div');
    sku.className = 'mono';
    sku.textContent = `SKU ${p.sku || ''}`;
    caja.appendChild(sku);

    for (const [svgTexto, clase] of [[svgs.qr, 'qr'], [svgs.barcode, 'barras']]) {
      const parseado = new DOMParser().parseFromString(svgTexto, 'image/svg+xml');
      const raiz = parseado.documentElement;
      if (!raiz || raiz.nodeName.toLowerCase() !== 'svg' || parseado.querySelector('parsererror')) continue;
      for (const script of raiz.querySelectorAll('script')) script.remove();
      const nodo = doc.importNode(raiz, true);
      nodo.setAttribute('class', clase);
      caja.appendChild(nodo);
    }

    const codigos = doc.createElement('div');
    codigos.className = 'mono codigos';
    codigos.textContent = `${p.qr_code || p.sku || ''} · ${p.barcode || p.sku || ''}`;
    caja.appendChild(codigos);

    doc.body.appendChild(caja);

    ventana.focus();
    ventana.print();
  }

  // --- Alta de producto -----------------------------------------------------
  async function abrirAlta() {
    let categorias = [];
    try {
      categorias = await api.categorias();
    } catch (error) {
      avisar(`No se pudieron cargar las categorias: ${error.message}`, 'error');
      return;
    }

    const campoMarca = campoTexto('Ej: Dell, HP, Cisco', 'Marca *');
    const campoModelo = campoTexto('Ej: Latitude 5520', 'Modelo *');
    const campoSKU = campoTexto('Ej: NB-DELL-5520', 'SKU *', { mono: true });
    const campoVariante = campoTexto('Ej: i5/16GB/512GB (opcional)', 'Variante');
    const campoQR = campoTexto('Ej: PRD-NB-DELL-5520 (se genera si vacío)', 'Código QR', { mono: true });
    const campoBarras = campoTexto('Ej: el SKU (opcional)', 'Código de barras', { mono: true });

    const selectCategoria = crear('select', { clase: 'campo__control' });
    selectCategoria.append(
      crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true, selected: true } }),
    );
    for (const c of categorias) {
      selectCategoria.append(crear('option', { texto: c.name, atributos: { value: c.id } }));
    }

    const campoCosto = campoTexto('Ej: 850.00', 'Costo unitario (USD)', { type: 'number', paso: '0.01' });
    const campoMin = campoTexto('Ej: 3', 'Stock minimo', { type: 'number', valor: '0' });
    const campoMax = campoTexto('Ej: 20', 'Stock maximo', { type: 'number', valor: '100' });

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
      campoMarca.campo,
      campoModelo.campo,
      campoSKU.campo,
      campoVariante.campo,
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Categoria *' }), selectCategoria],
      }),
      campoCosto.campo,
      campoMin.campo,
      campoMax.campo,
      campoQR.campo,
      campoBarras.campo,
    );

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Guardar producto',
      atributos: { type: 'submit' },
    });
    form.append(btnGuardar);

    let cerrarModal = () => {};
    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    async function enviar() {
      const brand = campoMarca.input.value.trim();
      const model = campoModelo.input.value.trim();
      const sku = campoSKU.input.value.trim();
      const category_id = selectCategoria.value ? parseInt(selectCategoria.value, 10) : null;

      if (!brand || !model || !sku) return avisar('Marca, modelo y SKU son obligatorios', 'alerta');
      if (!category_id) return avisar('Selecciona una categoria', 'alerta');

      // Confirmacion antes de escribir: el alta de un producto genera stock y
      // claves de compra, y un SKU equivocado se propaga a los movimientos.
      const ok = await confirmar({
        titulo: 'Guardar producto',
        mensaje: `¿Guardar el producto "${brand} ${model}" con el SKU ${sku}?`,
        textoConfirmar: 'Guardar',
      });
      if (!ok) return;

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Guardando…';
      try {
        await api.crearProducto({
          brand,
          model,
          sku,
          variant: campoVariante.input.value.trim() || undefined,
          category_id,
          unit_cost: campoCosto.input.value ? parseFloat(campoCosto.input.value) : undefined,
          min_stock: parseInt(campoMin.input.value || '0', 10),
          max_stock: parseInt(campoMax.input.value || '100', 10),
          qr_code: campoQR.input.value.trim() || undefined,
          barcode: campoBarras.input.value.trim() || undefined,
        });
        avisar('Producto creado correctamente', 'exito', 2500);
        cerrarModal();
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Guardar producto';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: 'Nuevo producto',
      nota: 'El SKU es unico. Es la clave con la que se compra y se cuenta el stock.',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [btnCancelar],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

  // --- Edicion de producto ---------------------------------------------------
  async function abrirEdicion(p) {
    let categorias = [];
    try {
      categorias = await api.categorias();
    } catch (error) {
      avisar(`No se pudieron cargar las categorias: ${error.message}`, 'error');
      return;
    }

    const campoMarca = campoTexto('Ej: Dell, HP, Cisco', 'Marca *');
    campoMarca.input.value = p.brand || '';
    const campoModelo = campoTexto('Ej: Latitude 5520', 'Modelo *');
    campoModelo.input.value = p.model || '';
    const campoSKU = campoTexto('Ej: NB-DELL-5520', 'SKU (no editable)', { mono: true });
    campoSKU.input.value = p.sku || '';
    campoSKU.input.disabled = true;
    const campoVariante = campoTexto('Ej: i5/16GB/512GB (opcional)', 'Variante');
    campoVariante.input.value = p.variant || '';
    const campoQR = campoTexto('Ej: PRD-NB-DELL-5520', 'Código QR', { mono: true });
    campoQR.input.value = p.qr_code || '';
    const campoBarras = campoTexto('Ej: el SKU', 'Código de barras', { mono: true });
    campoBarras.input.value = p.barcode || '';

    const selectCategoria = crear('select', { clase: 'campo__control' });
    selectCategoria.append(
      crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true } }),
    );
    for (const c of categorias) {
      selectCategoria.append(crear('option', { texto: c.name, atributos: { value: c.id } }));
    }
    selectCategoria.value = String(p.category_id || '');

    const campoCosto = campoTexto('Ej: 850.00', 'Costo unitario (USD)', { type: 'number', paso: '0.01' });
    campoCosto.input.value = p.unit_cost ?? '';
    const campoMin = campoTexto('Ej: 3', 'Stock minimo', { type: 'number', valor: '0' });
    campoMin.input.value = String(p.min_stock ?? 0);
    const campoMax = campoTexto('Ej: 20', 'Stock maximo', { type: 'number', valor: '100' });
    campoMax.input.value = String(p.max_stock ?? 100);

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
      campoMarca.campo,
      campoModelo.campo,
      campoSKU.campo,
      campoVariante.campo,
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Categoria *' }), selectCategoria],
      }),
      campoCosto.campo,
      campoMin.campo,
      campoMax.campo,
      campoQR.campo,
      campoBarras.campo,
    );

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Guardar cambios',
      atributos: { type: 'submit' },
    });
    form.append(btnGuardar);

    let cerrarModal = () => {};
    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    async function enviar() {
      const brand = campoMarca.input.value.trim();
      const model = campoModelo.input.value.trim();
      const category_id = selectCategoria.value ? parseInt(selectCategoria.value, 10) : null;

      if (!brand || !model) return avisar('Marca y modelo son obligatorios', 'alerta');
      if (!category_id) return avisar('Selecciona una categoria', 'alerta');

      const ok = await confirmar({
        titulo: 'Guardar cambios',
        mensaje: `¿Guardar los cambios del producto "${etiquetaProducto(p)}"?`,
        textoConfirmar: 'Guardar cambios',
      });
      if (!ok) return;

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Guardando…';
      try {
        await api.actualizarProducto(p.id, {
          brand,
          model,
          category_id,
          variant: campoVariante.input.value.trim() || undefined,
          unit_cost: campoCosto.input.value ? parseFloat(campoCosto.input.value) : undefined,
          min_stock: parseInt(campoMin.input.value || '0', 10),
          max_stock: parseInt(campoMax.input.value || '100', 10),
          qr_code: campoQR.input.value.trim() || undefined,
          barcode: campoBarras.input.value.trim() || undefined,
        });
        avisar('Producto actualizado correctamente', 'exito', 2500);
        cerrarModal();
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Guardar cambios';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: `Editar producto — ${p.sku || p.model || p.brand}`,
      nota: 'El SKU no se puede modificar: es la clave historica del producto.',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [btnCancelar],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

  // --- Confirmaciones --------------------------------------------------------
  function confirmarEliminarProducto(p) {
    abrirDialogo({
      titulo: 'Eliminar producto',
      mensaje: `¿Eliminar el producto "${etiquetaProducto(p)}"? Esta acción no se puede deshacer.`,
      clase: 'modal--confirmacion modal--peligro',
      acciones: [
        crear('button', {
          clase: 'boton boton--suave',
          texto: 'Cancelar',
          atributos: { type: 'button' },
        }),
        crear('button', {
          clase: 'boton boton--peligro',
          texto: 'Eliminar',
          atributos: { type: 'button' },
        }),
      ],
      alAbrir: (modal, cerrar) => {
        const [btnCancelar, btnConfirmar] = modal.querySelectorAll('.modal__acciones .boton');
        btnCancelar.addEventListener('click', cerrar);
        btnConfirmar.addEventListener('click', async () => {
          btnConfirmar.disabled = true;
          btnConfirmar.textContent = 'Eliminando…';
          try {
            await api.eliminarProducto(p.id);
            avisar('Producto eliminado', 'exito', 2500);
            cerrar();
            cargar();
          } catch (error) {
            avisar(error.message, 'error');
            btnConfirmar.disabled = false;
            btnConfirmar.textContent = 'Eliminar';
          }
        });
      },
    });
  }

  function confirmarAlternarActivo(p) {
    abrirDialogo({
      titulo: `${p.is_active ? 'Descontinuar' : 'Reactivar'} producto`,
      mensaje: `¿${p.is_active ? 'Descontinuar' : 'Reactivar'} el producto "${etiquetaProducto(p)}"?`,
      clase: 'modal--confirmacion',
      acciones: [
        crear('button', {
          clase: 'boton boton--suave',
          texto: 'Cancelar',
          atributos: { type: 'button' },
        }),
        crear('button', {
          clase: 'boton',
          texto: p.is_active ? 'Descontinuar' : 'Reactivar',
          atributos: { type: 'button' },
        }),
      ],
      alAbrir: (modal, cerrar) => {
        const [btnCancelar, btnConfirmar] = modal.querySelectorAll('.modal__acciones .boton');
        btnCancelar.addEventListener('click', cerrar);
        btnConfirmar.addEventListener('click', async () => {
          btnConfirmar.disabled = true;
          btnConfirmar.textContent = 'Procesando…';
          try {
            await api.actualizarProducto(p.id, { is_active: !p.is_active });
            avisar(`Producto ${p.is_active ? 'descontinuado' : 'reactivado'}`, 'exito', 2500);
            cerrar();
            cargar();
          } catch (error) {
            avisar(error.message, 'error');
            btnConfirmar.disabled = false;
            btnConfirmar.textContent = p.is_active ? 'Descontinuar' : 'Reactivar';
          }
        });
      },
    });
  }

  inputBuscar.addEventListener('input', cargar);

  cargar();
  return { elemento: raiz };
}

/** Campo de texto con su etiqueta, para no repetir el bloque en cada alta. */
function campoTexto(placeholder, etiqueta, { type = 'text', paso, valor = '', mono = false } = {}) {
  const input = crear('input', {
    clase: `campo__control${mono ? ' mono' : ''}`,
    atributos: {
      type,
      placeholder,
      autocomplete: 'off',
      ...(paso ? { step: paso } : {}),
      ...(valor ? { value: valor } : {}),
    },
  });
  const campo = crear('label', {
    clase: 'campo',
    hijos: [crear('span', { clase: 'campo__etiqueta', texto: etiqueta }), input],
  });
  return { campo, input };
}
