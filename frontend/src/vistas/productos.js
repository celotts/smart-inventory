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
    const btnCategorias = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Categorias',
      atributos: {
        type: 'button',
        title: 'Administrar categorias, subcategorias y atributos',
        onclick: () => abrirCategorias(),
      },
    });
    encabezado.append(btnCategorias, btnNuevo);
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
          texto: [p.categoria_ruta || p.categoria, p.variant].filter(Boolean).join(' · '),
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

  // --- Categorias y atributos ------------------------------------------------
  //
  // La jerarquia y los atributos se administran desde la MISMA vista de
  // productos: son parte del maestro. Uma pantalla aparte obligaria al
  // operador a saltar de una pestana a otra para agregar un campo de
  // "caducidad" a la categoria de consumibles.
  async function abrirCategorias() {
    const cuerpo = crear('div', { clase: 'apilado' });
    const zonaLista = crear('div');
    cuerpo.append(zonaLista);

    async function pintar() {
      zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando categorias…' }));
      try {
        const categorias = await api.categorias();
        zonaLista.replaceChildren();

        if (!categorias.length) {
          zonaLista.append(crear('p', { clase: 'tenue', texto: 'No hay categorias.' }));
          return;
        }

        const lista = crear('ul', { clase: 'lista' });
        for (const c of categorias) {
          const item = crear('li', { clase: 'lista__item' });
          const cuerpoItem = crear('div', { clase: 'lista__cuerpo' });
          cuerpoItem.append(
            crear('div', { clase: 'lista__titulo', texto: c.name }),
            crear('div', { clase: 'lista__meta', texto: c.ruta || c.name }),
          );

          const btnAtributos = crear('button', {
            clase: 'boton boton--suave boton--chico',
            texto: 'Atributos',
            atributos: { type: 'button', onclick: () => abrirAtributos(c) },
          });

          const btnEditar = crear('button', {
            clase: 'boton boton--suave boton--chico',
            texto: 'Editar',
            atributos: { type: 'button', onclick: () => abrirEdicionCategoria(c, pintar) },
          });

          item.append(cuerpoItem, btnAtributos, btnEditar);
          lista.append(item);
        }
        zonaLista.append(lista);
      } catch (error) {
        zonaLista.replaceChildren(
          crear('div', { clase: 'resultado resultado--error', texto: error.message }),
        );
      }
    }

    const btnNueva = crear('button', {
      clase: 'boton',
      texto: 'Nueva categoria',
      atributos: { type: 'button', onclick: () => abrirEdicionCategoria(null, pintar) },
    });

    const btnCerrar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cerrar',
      atributos: { type: 'button' },
    });

    abrirDialogo({
      titulo: 'Categorias y atributos',
      nota: 'Una categoria puede colgar de otra (subcategoria) y definir sus propios atributos.',
      cuerpo: [cuerpo],
      clase: 'modal--formulario',
      acciones: [btnCerrar, btnNueva],
      alAbrir: (_modal, cerrar) => {
        btnCerrar.addEventListener('click', cerrar);
      },
    });

    await pintar();
  }

  /**
   * Alta o edicion de una categoria.
   *
   * @param {object|null} categoria  La categoria a editar, o null para una nueva.
   * @param {Function} alGuardar     Se llama despues de guardar, para repintar.
   */
  async function abrirEdicionCategoria(categoria, alGuardar) {
    let categorias = [];
    try {
      categorias = await api.categorias();
    } catch (error) {
      avisar(`No se pudieron cargar las categorias: ${error.message}`, 'error');
      return;
    }

    const campoNombre = campoTexto('Ej: Notebooks 14"', 'Nombre *');
    if (categoria) campoNombre.input.value = categoria.name || '';

    const campoCodigo = campoTexto('Ej: NB14', 'Codigo corto', { mono: true });
    if (categoria) campoCodigo.input.value = categoria.code || '';

    // La ruta de la categoria editada: sus descendientes no pueden ser su padre.
    const rutaPadre = categoria?.ruta ?? '';
    const selectPadre = crear('select', { clase: 'campo__control' });
    selectPadre.append(crear('option', { texto: 'Sin categoria padre', atributos: { value: '' } }));
    for (const c of categorias) {
      // Una categoria no puede colgar de si misma, y tampoco de una de sus
      // hijas: el backend lo rechaza, pero es mejor no ofrecerlo.
      if (categoria && (c.id === categoria.id || (rutaPadre && c.ruta?.startsWith(rutaPadre)))) continue;
      selectPadre.append(crear('option', { texto: c.ruta || c.name, atributos: { value: c.id } }));
    }
    if (categoria?.parent_id) selectPadre.value = String(categoria.parent_id);

    const form = crear('form', {
      clase: 'apilado',
      atributos: {
        onsubmit: async (e) => {
          e.preventDefault();
          await guardar();
        },
      },
    });

    form.append(
      campoNombre.campo,
      campoCodigo.campo,
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Categoria padre' }), selectPadre],
      }),
    );

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: categoria ? 'Guardar cambios' : 'Crear categoria',
      atributos: { type: 'submit' },
    });
    form.append(btnGuardar);

    let cerrarModal = () => {};

    async function guardar() {
      const name = campoNombre.input.value.trim();
      if (!name) return avisar('El nombre es obligatorio', 'alerta');

      const cuerpoPeticion = {
        name,
        code: campoCodigo.input.value.trim() || undefined,
        parent_id: selectPadre.value ? parseInt(selectPadre.value, 10) : null,
      };

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Guardando…';
      try {
        if (categoria) {
          await api.pedir(`/catalog/categories/${categoria.id}`, {
            metodo: 'PUT',
            cuerpo: cuerpoPeticion,
          });
        } else {
          await api.pedir('/catalog/categories', { metodo: 'POST', cuerpo: cuerpoPeticion });
        }
        avisar(categoria ? 'Categoria actualizada' : 'Categoria creada', 'exito', 2500);
        cerrarModal();
        alGuardar?.();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = categoria ? 'Guardar cambios' : 'Crear categoria';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: categoria ? `Editar ${categoria.name}` : 'Nueva categoria',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
      },
    });
  }

  /**
   * Los atributos de una categoria: los que tiene y los que se le pueden
   * agregar.
   */
  async function abrirAtributos(categoria) {
    const cuerpo = crear('div', { clase: 'apilado' });
    const zonaLista = crear('div');
    cuerpo.append(zonaLista);

    async function pintar() {
      zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando atributos…' }));
      try {
        const atributos = await api.pedir(`/catalog/categories/${categoria.id}/attributes`);
        zonaLista.replaceChildren();

        if (!atributos.length) {
          zonaLista.append(
            crear('p', {
              clase: 'tenue',
              texto: 'Esta categoria no define atributos. Un producto de esta categoria no tendra campos extra.',
            }),
          );
          return;
        }

        const lista = crear('ul', { clase: 'lista' });
        for (const a of atributos) {
          const item = crear('li', { clase: 'lista__item' });
          const cuerpoItem = crear('div', { clase: 'lista__cuerpo' });
          cuerpoItem.append(
            crear('div', { clase: 'lista__titulo', texto: a.label }),
            crear('div', {
              clase: 'lista__meta mono',
              texto: `${a.key} · ${a.data_type}${a.is_required ? ' · obligatorio' : ''}`,
            }),
          );
          item.append(cuerpoItem);
          lista.append(item);
        }
        zonaLista.append(lista);
      } catch (error) {
        zonaLista.replaceChildren(
          crear('div', { clase: 'resultado resultado--error', texto: error.message }),
        );
      }
    }

    const campoNombre = campoTexto('Ej: caducidad', 'Clave *', { mono: true });
    const campoEtiqueta = campoTexto('Ej: Fecha de caducidad', 'Etiqueta *');
    const campoUnidad = campoTexto('Ej: kg', 'Unidad');

    const selectTipo = crear('select', { clase: 'campo__control' });
    for (const [valor, texto] of [
      ['text', 'Texto'],
      ['number', 'Numero'],
      ['date', 'Fecha'],
      ['boolean', 'Si / No'],
      ['select', 'Lista de opciones'],
    ]) {
      selectTipo.append(crear('option', { texto, atributos: { value: valor } }));
    }

    const campoOpciones = campoTexto('Ej: Nuevo, Reacondicionado', 'Opciones (separadas por coma)');
    campoOpciones.campo.hidden = true;
    selectTipo.addEventListener('change', () => {
      campoOpciones.campo.hidden = selectTipo.value !== 'select';
    });

    const form = crear('form', {
      clase: 'apilado',
      atributos: {
        onsubmit: async (e) => {
          e.preventDefault();
          await guardar();
        },
      },
    });

    form.append(
      campoNombre.campo,
      campoEtiqueta.campo,
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Tipo' }), selectTipo],
      }),
      campoUnidad.campo,
      campoOpciones.campo,
    );

    const btnAgregar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Agregar atributo',
      atributos: { type: 'submit' },
    });
    form.append(btnAgregar);

    async function guardar() {
      const key = campoNombre.input.value.trim().toLowerCase().replace(/\s+/g, '_');
      const label = campoEtiqueta.input.value.trim();
      if (!key || !label) return avisar('La clave y la etiqueta son obligatorias', 'alerta');

      const opciones = campoOpciones.input.value
        .split(',')
        .map((o) => o.trim())
        .filter(Boolean);

      if (selectTipo.value === 'select' && !opciones.length) {
        return avisar('Un atributo de tipo lista necesita sus opciones', 'alerta');
      }

      btnAgregar.disabled = true;
      btnAgregar.textContent = 'Agregando…';
      try {
        await api.pedir(`/catalog/categories/${categoria.id}/attributes`, {
          metodo: 'POST',
          cuerpo: {
            key,
            label,
            data_type: selectTipo.value,
            unit: campoUnidad.input.value.trim() || undefined,
            options: opciones.length ? opciones : undefined,
          },
        });
        avisar('Atributo agregado', 'exito', 2500);
        campoNombre.input.value = '';
        campoEtiqueta.input.value = '';
        await pintar();
      } catch (error) {
        avisar(error.message, 'error');
      } finally {
        btnAgregar.disabled = false;
        btnAgregar.textContent = 'Agregar atributo';
      }
    }

    const btnCerrar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cerrar',
      atributos: { type: 'button' },
    });

    abrirDialogo({
      titulo: `Atributos de ${categoria.name}`,
      nota: 'Los atributos son los campos extra que pide un producto de esta categoria.',
      cuerpo: [cuerpo, form],
      clase: 'modal--formulario',
      acciones: [btnCerrar],
      alAbrir: (_modal, cerrar) => {
        btnCerrar.addEventListener('click', cerrar);
      },
    });

    await pintar();
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
      // La ruta completa ("Notebook > Notebooks 14\""), no el nombre solo: con
      // subcategorias hay dos "Impresora" y el operador tiene que ver de que
      // rama sale cada una.
      selectCategoria.append(crear('option', { texto: c.ruta || c.name, atributos: { value: c.id } }));
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

    // Los atributos de la CATEGORIA elegida (caducidad, lote, dimensiones...).
    // El backend los valida contra la definicion de la categoria, asi que
    // solo se mandan los que el operador lleno.
    const zonaAtributos = crear('div', { clase: 'apilado' });

    form.append(
      campoMarca.campo,
      campoModelo.campo,
      campoSKU.campo,
      campoVariante.campo,
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Categoria *' }), selectCategoria],
      }),
      zonaAtributos,
      campoCosto.campo,
      campoMin.campo,
      campoMax.campo,
      campoQR.campo,
      campoBarras.campo,
    );

    selectCategoria.addEventListener('change', () => {
      pintarAtributos(zonaAtributos, selectCategoria.value, {});
    });

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
          atributos: leerAtributos(),
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
      // La ruta completa ("Notebook > Notebooks 14\""), no el nombre solo: con
      // subcategorias hay dos "Impresora" y el operador tiene que ver de que
      // rama sale cada una.
      selectCategoria.append(crear('option', { texto: c.ruta || c.name, atributos: { value: c.id } }));
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

    const zonaAtributos = crear('div', { clase: 'apilado' });

    form.append(
      campoMarca.campo,
      campoModelo.campo,
      campoSKU.campo,
      campoVariante.campo,
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Categoria *' }), selectCategoria],
      }),
      zonaAtributos,
      campoCosto.campo,
      campoMin.campo,
      campoMax.campo,
      campoQR.campo,
      campoBarras.campo,
    );

    selectCategoria.addEventListener('change', () => {
      pintarAtributos(zonaAtributos, selectCategoria.value, {});
    });
    // Los atributos que ya tenia el producto, con los valores cargados.
    pintarAtributos(zonaAtributos, selectCategoria.value, p.atributos || {});

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
          atributos: leerAtributos(),
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

/**
 * Los controles de los atributos de la categoria.
 *
 * Un Map de clave -> control. El Map es global al modulo porque el formulario
 * de alta y el de edicion existen a la vez en algunos momentos, y cada uno
 * pinta sus propios campos: con un Map por formulario habria que acordarse de
 * cual se esta leyendo al mandar.
 */
const controlesAtributos = new Map();

/**
 * Pinta los campos de los atributos de una categoria.
 *
 * QUE SON
 * -------
 * Cada categoria define sus propios campos: una de monitores tiene "alto" y
 * "ancho", una de consumibles tiene "caducidad" y "lote". El backend valida
 * los valores contra esa definicion, asi que el frontend no decide nada: solo
 * pide la definicion y arma el control que corresponde.
 *
 * @param {HTMLElement} zona      Donde se pintan los campos.
 * @param {string|number} categoriaId
 * @param {object} valores        Valores ya cargados (edicion).
 */
async function pintarAtributos(zona, categoriaId, valores = {}) {
  controlesAtributos.clear();
  zona.replaceChildren();

  if (!categoriaId) return;

  let atributos;
  try {
    atributos = await api.pedir(`/catalog/categories/${categoriaId}/attributes`);
  } catch (error) {
    // Los atributos son un extra: si no se pueden cargar, el producto se
    // guarda igual, sin sus atributos.
    console.warn('No se pudieron cargar los atributos:', error);
    return;
  }

  if (!atributos.length) return;

  const titulo = crear('p', { clase: 'campo__ayuda', texto: 'Atributos de la categoria' });
  zona.append(titulo);

  for (const atributo of atributos) {
    const valor = valores[atributo.key] ?? '';

    let control;
    if (atributo.data_type === 'select') {
      const opciones = Array.isArray(atributo.options)
        ? atributo.options
        : (atributo.options ? JSON.parse(atributo.options) : []);
      control = crear('select', { clase: 'campo__control' });
      control.append(
        crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true } }),
      );
      for (const opcion of opciones) {
        control.append(crear('option', { texto: String(opcion), atributos: { value: opcion } }));
      }
      if (valor !== '') control.value = String(valor);
    } else if (atributo.data_type === 'boolean') {
      control = crear('input', {
        clase: 'campo__control',
        atributos: { type: 'checkbox' },
      });
      control.checked = valor === true || String(valor).toLowerCase() === 'true';
    } else {
      control = crear('input', {
        clase: 'campo__control',
        atributos: {
          type: atributo.data_type === 'number' ? 'number' : atributo.data_type === 'date' ? 'date' : 'text',
          autocomplete: 'off',
          ...(valor !== '' && valor !== null ? { value: String(valor) } : {}),
        },
      });
    }

    const etiqueta = crear('span', {
      clase: 'campo__etiqueta',
      texto: atributo.label + (atributo.is_required ? ' *' : '') + (atributo.unit ? ` (${atributo.unit})` : ''),
    });

    zona.append(crear('label', { clase: 'campo', hijos: [etiqueta, control] }));
    controlesAtributos.set(atributo.key, control);
  }
}

/**
 * Los valores de los atributos que el operador lleno.
 *
 * Un control vacio NO se manda: el backend lo tomaria como "borrar el valor"
 * en la edicion, y en el alta seria un atributo sin valor.
 */
function leerAtributos() {
  const valores = {};
  for (const [clave, control] of controlesAtributos) {
    if (control.type === 'checkbox') {
      if (control.checked) valores[clave] = true;
      continue;
    }
    const valor = (control.value ?? '').toString().trim();
    if (valor !== '') valores[clave] = valor;
  }
  return valores;
}
