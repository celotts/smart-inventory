/**
 * El alta y la edicion de un producto del maestro.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * Son dos formularios (alta y edicion) que son EL MISMO formulario con los
 * datos cargados o no. Estaban en `productos.js` junto con la lista, la
 * etiqueta y el gestor de categorias, y el archivo era de 1300 lineas.
 *
 * LAS DIFERENCIAS ENTRE ALTA Y EDICION
 * ------------------------------------
 * Solo tres: el titulo, el texto del boton y que la edicion manda la
 * `version` (el bloqueo optimista). Todo lo demas —los campos, los atributos
 * dinamicos, la validacion— es el MISMO codigo, y por eso estan en el mismo
 * archivo y no en dos copias.
 */

import { api } from '../api.js';
import { crear, etiquetaProducto } from '../dom.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { avisar } from '../avisos.js';
import { conTooltip } from '../tooltip.js';
import { campoTexto, campoSelect, errorDe, resumenDeErrores } from '../formulario.js';
import { etiquetaConDescripcion, leerAtributos, pintarAtributos } from './atributos.js';
import { botonEscanear } from '../escaner-campo.js';

  // --- Alta de producto -----------------------------------------------------
  /**
   * El alta de un producto, opcionalmente a partir de otro.
   *
   * POR QUE DUPLICAR
   * ----------------
   * Cargar el catalogo de un deposito es veinte productos que se parecen:
   * la misma marca, la misma linea, distinto modelo o distinto tamano. Sin
   * duplicar, el operador tiene que copiar campo por campo desde el
   * producto anterior, y termina dejando un valor mal copiado (el costo del
   * modelo pasado, que es el error mas caro del maestro).
   *
   * El SKU NO se copia: es UNIQUE, y tiene que ser distinto. Es lo unico que
   * el operador tiene que escribir.
   *
   * @param {object} [base]  El producto a partir del cual se copia.
   */
  async function abrirAlta(base = null) {
    let categorias = [];
    try {
      categorias = await api.categorias();
    } catch (error) {
      avisar(`No se pudieron cargar las categorias: ${error.message}`, 'error');
      return;
    }

    const campoMarca = campoTexto({ etiqueta: 'Marca *', placeholder: 'Ej: Dell, HP, Cisco', descripcion: 'La marca del fabricante. Es parte del nombre del producto.' });
    const campoModelo = campoTexto({ etiqueta: 'Modelo *', placeholder: 'Ej: Latitude 5520', descripcion: 'El modelo exacto. Con la marca, identifica el producto.' });
    const campoSKU = campoTexto({ etiqueta: 'SKU *', placeholder: 'Ej: NB-DELL-5520', mono: true, descripcion: 'La clave del producto: es con la que se compra, se cuenta el stock y se etiqueta. No se puede cambiar despues.' });
    const campoVariante = campoTexto({ etiqueta: 'Variante', placeholder: 'Ej: i5/16GB/512GB (opcional)', descripcion: 'La diferencia entre dos productos de la misma marca y modelo: capacidad, color, tamaño.' });
    const campoQR = campoTexto({ etiqueta: 'Código QR', placeholder: 'Ej: PRD-NB-DELL-5520 (se genera si vacío)', mono: true, descripcion: 'El codigo de la etiqueta del producto. Si lo dejas vacio se genera uno propio (PRD-<sku>).' });
    const campoBarras = campoTexto({ etiqueta: 'Código de barras', placeholder: 'Ej: el SKU (opcional)', mono: true,
      descripcion: 'El codigo de barras del producto: el del proveedor (un GTIN) o el SKU. Es lo que se imprime en la caja.', });

    // ESCANEAR EL CODIGO DE LA CAJA
    //
    // El producto llega con un codigo del proveedor: un GTIN en la caja, o el
    // QR del fabricante. Copiarlo a mano es la fuente numero uno de errores de
    // inventario, asi que se LEE.
    //
    // Lo leido va al campo de CODIGO DE BARRAS (el del proveedor), nunca al
    // QR propio: ese (`PRD-<sku>`) es el protocolo interno de la app, y el
    // escaner de la caja lo resuelve contra el maestro. Pisarlo haria que la
    // etiqueta propia dejara de funcionar.
    const filaBarras = crear('div', { clase: 'campo-con-boton' });
    filaBarras.append(
      campoBarras.campo,
      botonEscanear({ campo: campoBarras.input, nombre: 'el codigo de barras' }),
    );

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
    const errorCategoria = errorDe(selectCategoria);

    const campoCosto = campoTexto({ etiqueta: 'Costo unitario (USD)', placeholder: 'Ej: 850.00', type: 'number', paso: '0.01', descripcion: 'Lo que costo una unidad. Con esto se calcula el valor del inventario.' });
    const campoMin = campoTexto({ etiqueta: 'Stock minimo', placeholder: 'Ej: 3', type: 'number', valor: '0', descripcion: 'Por debajo de este numero, el producto aparece para reponer.' });
    const campoMax = campoTexto({ etiqueta: 'Stock maximo', placeholder: 'Ej: 20', type: 'number', valor: '100', descripcion: 'El tope de stock: arriba de este numero, el producto esta por sobre stockeado.' });

    // EL PRELLENADO (cuando se duplica un producto)
    //
    // Se copian los campos que describen la LINEA del producto: la marca, el
    // modelo, la variante, la categoria, el costo y los puntos de stock.
    //
    // NO se copian el SKU, el QR ni el codigo de barras:
    //   - El SKU es UNIQUE: tiene que ser distinto, y es lo unico que el
    //     operador tiene que escribir.
    //   - El QR (`PRD-<sku>`) y el codigo de barras se derivan del SKU, y
    //     copiarlos seria un conflicto de unicidad.
    if (base) {
      campoMarca.control.value = base.brand ?? '';
      campoModelo.control.value = base.model ?? '';
      campoVariante.control.value = base.variant ?? '';
      campoCosto.control.value = base.unit_cost ?? '';
      campoMin.control.value = base.min_stock ?? 0;
      campoMax.control.value = base.max_stock ?? 100;
      if (base.category_id) selectCategoria.value = String(base.category_id);
    }

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

    // El resumen de errores va arriba: en un formulario de diez campos, el
    // operador tiene que enterarse de que hay un problema antes de llegar al
    // final del formulario.
    const zonaResumen = crear('div');

    form.append(
      zonaResumen,
      campoMarca.campo,
      campoModelo.campo,
      campoSKU.campo,
      campoVariante.campo,
      etiquetaConDescripcion('Categoria *', selectCategoria, 'La rama del maestro a la que pertenece el producto. Define que atributos tiene.'),
      zonaAtributos,
      campoCosto.campo,
      campoMin.campo,
      campoMax.campo,
      campoQR.campo,
      filaBarras,
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

      // Los errores van debajo de cada campo, con el control marcado, y el
      // foco salta al primero. Un cartel flotante que se va solo no dice que
      // campo esta mal.
      campoMarca.limpiar();
      campoModelo.limpiar();
      campoSKU.limpiar();
      errorCategoria.limpiar();
      zonaResumen.replaceChildren();

      if (!brand) campoMarca.marcar('La marca es obligatoria.');
      if (!model) campoModelo.marcar('El modelo es obligatorio.');
      if (!sku) campoSKU.marcar('El SKU es obligatorio: es la clave con la que se compra y se cuenta el stock.');
      if (!category_id) errorCategoria.marcar('Selecciona una categoria.');

      const resumen = resumenDeErrores([campoMarca, campoModelo, campoSKU, errorCategoria]);
      if (resumen) {
        zonaResumen.replaceChildren(resumen);
        campoMarca.control.focus();
        return;
      }
      if (campoMarca.control.getAttribute('aria-invalid') === 'true') {
        campoMarca.control.focus();
        return;
      }
      if (campoModelo.control.getAttribute('aria-invalid') === 'true') {
        campoModelo.control.focus();
        return;
      }
      if (campoSKU.control.getAttribute('aria-invalid') === 'true') {
        campoSKU.control.focus();
        return;
      }
      if (selectCategoria.getAttribute('aria-invalid') === 'true') {
        selectCategoria.focus();
        return;
      }

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
        cerrarModal(true);
        cargar();
      } catch (error) {
        // Un 409 es un problema de un campo (el SKU ya existe, el QR esta
        // repetido). Va al campo, no a un cartel que se va solo.
        if (error.status === 409) {
          const mensaje = error.message || 'Revisa los datos.';
          if (/sku/i.test(mensaje)) campoSKU.marcar(mensaje);
          else if (/barras/i.test(mensaje)) campoBarras.marcar(mensaje);
          else if (/qr|c[oó]digo/i.test(mensaje)) campoQR.marcar(mensaje);
          else zonaResumen.replaceChildren(crear('p', { clase: 'campo__error', texto: mensaje }));
        } else {
          avisar(error.message, 'error');
        }
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
      // Si el operador cerro sin guardar, se le avisa: perder diez campos
      // escritos sin decir nada es la peor forma de perder el trabajo.
      avanzarAlCerrar: true,
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

    const campoMarca = campoTexto({ etiqueta: 'Marca *', placeholder: 'Ej: Dell, HP, Cisco', descripcion: 'La marca del fabricante. Es parte del nombre del producto.' });
    campoMarca.input.value = p.brand || '';
    const campoModelo = campoTexto({ etiqueta: 'Modelo *', placeholder: 'Ej: Latitude 5520', descripcion: 'El modelo exacto. Con la marca, identifica el producto.' });
    campoModelo.input.value = p.model || '';
    const campoSKU = campoTexto({ etiqueta: 'SKU (no editable)', placeholder: 'Ej: NB-DELL-5520', mono: true });
    campoSKU.input.value = p.sku || '';
    campoSKU.input.disabled = true;
    const campoVariante = campoTexto({ etiqueta: 'Variante', placeholder: 'Ej: i5/16GB/512GB (opcional)', descripcion: 'La diferencia entre dos productos de la misma marca y modelo: capacidad, color, tamaño.' });
    campoVariante.input.value = p.variant || '';
    const campoQR = campoTexto({ etiqueta: 'Código QR', placeholder: 'Ej: PRD-NB-DELL-5520', mono: true });
    campoQR.input.value = p.qr_code || '';
    const campoBarras = campoTexto({ etiqueta: 'Código de barras', placeholder: 'Ej: el SKU', mono: true,
      descripcion: 'El codigo de barras del producto: el del proveedor (un GTIN) o el SKU. Es lo que se imprime en la caja.', });

    // El mismo escaner que en el alta: el codigo del proveedor se lee de la
    // caja, no se escribe. Ver la nota del alta.
    const filaBarras = crear('div', { clase: 'campo-con-boton' });
    filaBarras.append(
      campoBarras.campo,
      botonEscanear({ campo: campoBarras.input, nombre: 'el codigo de barras' }),
    );
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

    const campoCosto = campoTexto({ etiqueta: 'Costo unitario (USD)', placeholder: 'Ej: 850.00', type: 'number', paso: '0.01', descripcion: 'Lo que costo una unidad. Con esto se calcula el valor del inventario.' });
    campoCosto.input.value = p.unit_cost ?? '';
    const campoMin = campoTexto({ etiqueta: 'Stock minimo', placeholder: 'Ej: 3', type: 'number', valor: '0', descripcion: 'Por debajo de este numero, el producto aparece para reponer.' });
    campoMin.input.value = String(p.min_stock ?? 0);
    const campoMax = campoTexto({ etiqueta: 'Stock maximo', placeholder: 'Ej: 20', type: 'number', valor: '100', descripcion: 'El tope de stock: arriba de este numero, el producto esta por sobre stockeado.' });
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

    // El resumen de errores va arriba: en un formulario de diez campos, el
    // operador tiene que enterarse de que hay un problema antes de llegar al
    // final del formulario.
    const zonaResumen = crear('div');

    form.append(
      zonaResumen,
      campoMarca.campo,
      campoModelo.campo,
      campoSKU.campo,
      campoVariante.campo,
      etiquetaConDescripcion('Categoria *', selectCategoria, 'La rama del maestro a la que pertenece el producto. Define que atributos tiene.'),
      zonaAtributos,
      campoCosto.campo,
      campoMin.campo,
      campoMax.campo,
      campoQR.campo,
      filaBarras,
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

      campoMarca.limpiar();
      campoModelo.limpiar();
      errorCategoria.limpiar();
      zonaResumen.replaceChildren();

      if (!brand) campoMarca.marcar('La marca es obligatoria.');
      if (!model) campoModelo.marcar('El modelo es obligatorio.');
      if (!category_id) errorCategoria.marcar('Selecciona una categoria.');

      const resumen = resumenDeErrores([campoMarca, campoModelo, errorCategoria]);
      if (resumen) {
        zonaResumen.replaceChildren(resumen);
        campoMarca.control.focus();
        return;
      }
      if (campoMarca.control.getAttribute('aria-invalid') === 'true') {
        campoMarca.control.focus();
        return;
      }
      if (campoModelo.control.getAttribute('aria-invalid') === 'true') {
        campoModelo.control.focus();
        return;
      }
      if (selectCategoria.getAttribute('aria-invalid') === 'true') {
        selectCategoria.focus();
        return;
      }

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
        cerrarModal(true);
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
      avanzarAlCerrar: true,
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

// La vista de productos las llama. Se exportan para que este modulo sea
// testeable por separado y para que el orden de lectura del archivo sea el
// mismo que el del flujo.
export { abrirAlta, abrirEdicion };
