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
import { crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo } from '../dialogo.js';

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
      );

      item.append(cuerpo);
      if (puedeEditar) {
        const btnEditar = crear('button', {
          clase: 'boton boton--suave boton--chico',
          texto: 'Editar',
          atributos: { type: 'button', onclick: () => abrirEdicion(p) },
        });
        const btnDescontinuar = crear('button', {
          clase: 'boton boton--chico boton--peligro',
          texto: p.is_active ? 'Descontinuar' : 'Reactivar',
          atributos: { type: 'button', onclick: () => alternarActivo(p) },
        });
        const btnEliminar = crear('button', {
          clase: 'boton boton--chico boton--peligro',
          texto: 'Eliminar',
          atributos: { type: 'button', onclick: () => eliminarProducto(p) },
        });
        item.append(btnEditar, btnDescontinuar, btnEliminar);
      }
      lista.append(item);
    }

    zonaLista.replaceChildren(lista);
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
      titulo: `Editar producto — ${p.sku}`,
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
