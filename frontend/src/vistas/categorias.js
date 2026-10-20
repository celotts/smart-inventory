/**
 * El gestor de categorias y sus atributos.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * Son DOS pantallas (la lista de categorias y la de atributos) y un formulario
 * (alta y edicion de una categoria), con sus propios datos y sus propias
 * validaciones. Estaban dentro de `productos.js`, que tiene la lista de
 * productos, la etiqueta, el alta, la edicion y las confirmaciones: mil
 * lineas donde un cambio en la etiqueta podia romper la edicion.
 *
 * LAS CATEGORIAS SON UN ARBOL
 * --------------------------
 * Una categoria puede colgar de otra (`parent_id`), y el filtro por
 * categoria trae tambien las subcategorias. Por eso las rutas se muestran
 * completas ("Notebook > Notebooks 14\"").
 */

import { api } from '../api.js';
import { crear } from '../dom.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { avisar } from '../avisos.js';
import { conTooltip } from '../tooltip.js';
import { campoTexto } from '../formulario.js';

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
    conTooltip(btnNueva, 'Crea una categoria. Si le pones una categoria padre, pasa a ser una subcategoria.');

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

    const campoNombre = campoTexto({ etiqueta: 'Nombre *', placeholder: 'Ej: Notebooks 14"' });
    if (categoria) campoNombre.input.value = categoria.name || '';

    const campoCodigo = campoTexto({ etiqueta: 'Codigo corto', placeholder: 'Ej: NB14', mono: true });
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

    const campoNombre = campoTexto({ etiqueta: 'Clave *', placeholder: 'Ej: caducidad', mono: true });
    const campoEtiqueta = campoTexto({ etiqueta: 'Etiqueta *', placeholder: 'Ej: Fecha de caducidad' });
    const campoUnidad = campoTexto({ etiqueta: 'Unidad', placeholder: 'Ej: kg' });

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

    const campoOpciones = campoTexto({ etiqueta: 'Opciones (separadas por coma)', placeholder: 'Ej: Nuevo, Reacondicionado' });
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

// La vista de productos las llama. Se exportan para que este modulo sea
// testeable por separado y para que el orden de lectura del archivo sea el
// mismo que el del flujo.
export { abrirCategorias, abrirEdicionCategoria, abrirAtributos };
