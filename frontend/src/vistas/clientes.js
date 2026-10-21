/**
 * Vista "Clientes" — Maestro de clientes.
 *
 * Los clientes estan asociados a las salidas de productos (movimientos
 * OUT_TO_CLIENT). Cada cliente tiene un tax_id unico.
 *
 * Solo visible/usable por quien tiene `devices:edit` (ADMIN).
 */

import { api } from '../api.js';
import { crearPaginacion } from '../paginacion.js';
import { crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';

export function vistaClientes({ permisos = [] }) {
  const raiz = crear('div');

  const puedeEditar = permisos.includes('devices:edit');

  // --- Cabecera con busqueda y boton de alta --------------------------------
  const tarjetaCabecera = crear('div', { clase: 'tarjeta' });

  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por nombre o tax_id…',
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
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Clientes' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Maestro de clientes. Se asocian a las salidas de productos.',
        }),
      ],
    }),
  );

  if (puedeEditar) {
    const btnNuevo = crear('button', {
      clase: 'boton',
      texto: 'Nuevo cliente',
      atributos: { type: 'button', onclick: abrirAlta },
    });
    encabezado.append(btnNuevo);
  }

  tarjetaCabecera.append(encabezado, etiquetaBusqueda);

  // --- Resumen de estadisticas ----------------------------------------------
  const tarjetaStats = crear('div', { clase: 'tarjeta clientes-stats' });
  const statsGrid = crear('div', { clase: 'clientes-stats__grid' });
  tarjetaStats.append(statsGrid);

  // --- Resultados -----------------------------------------------------------
  const zonaLista = crear('div');
  // LA PAGINACION
  //
  // Con miles de registros, traerlos todos y pintarlos todos deja la app
  // sin responder. La pantalla pide de a paginas y el operador ve cuantos
  // hay en total.
  let pagina = 0;
  let limite = 50;
  let total = 0;

  const paginacion = crearPaginacion({
    total: 0,
    limite,
    pagina,
    etiqueta: 'clientes',
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


  raiz.append(tarjetaCabecera, tarjetaStats, zonaLista, paginacion.elemento);

  let peticionEnCurso = 0;
  let todosLosClientes = [];

  async function cargar() {
    const numero = ++peticionEnCurso;
    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando clientes…' }));

    try {
      const respuesta = await api.clientes({
        busqueda: inputBuscar.value.trim() || undefined,
        limite,
        offset: pagina * limite,
      });
      todosLosClientes = respuesta.items ?? respuesta;
      total = respuesta.total ?? todosLosClientes.length;
      if (numero !== peticionEnCurso) return;
      pintarStats(todosLosClientes);
      pintarLista(todosLosClientes);
      paginacion.actualizar({ total, limite, pagina });
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  function pintarStats(clientes) {
    const total = clientes.length;
    const conContacto = clientes.filter(c => c.contact_name || c.contact_email).length;
    const conTelefono = clientes.filter(c => c.contact_phone).length;

    statsGrid.replaceChildren(
      statCard('Clientes', total, ICONOS.ok, 'var(--primario)'),
      statCard('Con contacto', conContacto, ICONOS.ubicacion, 'var(--exito)'),
      statCard('Con telefono', conTelefono, ICONOS.camara, 'var(--aviso, #f59e0b)'),
    );
  }

  function pintarLista(clientes) {
    // El filtro YA lo aplico el servidor: filtrar aca de nuevo, sobre los 50
    // de la pagina, dejaria filas fuera de la respuesta del servidor.
    const filtrados = clientes;

    if (!filtrados.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.buscar, { tamano: 28 }),
        crear('p', {
          texto: filtro
            ? 'No se encontraron clientes con ese criterio.'
            : 'No hay clientes. Crea uno con "Nuevo cliente".',
        }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista lista--clientes' });

    for (const c of filtrados) {
      const item = crear('li', { clase: 'lista__item cliente-card' });

      // Avatar con iniciales
      const avatar = crear('div', {
        clase: 'cliente-card__avatar',
        texto: obtenerIniciales(c.commercial_name),
      });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: c.commercial_name }),
        crear('div', { clase: 'lista__meta mono', texto: c.tax_id }),
        crear('div', {
          clase: 'lista__meta',
          texto: [c.contact_name, c.contact_email, c.contact_phone].filter(Boolean).join(' · ') || 'Sin datos de contacto',
        }),
      );

      item.append(avatar, cuerpo);

      if (puedeEditar) {
        const acciones = crear('div', { clase: 'cliente-card__acciones' });
        const btnEditar = crear('button', {
          clase: 'boton boton--suave boton--chico',
          texto: 'Editar',
          atributos: { type: 'button', onclick: () => abrirEdicion(c) },
        });
        const btnEliminar = crear('button', {
          clase: 'boton boton--chico boton--peligro',
          texto: 'Eliminar',
          atributos: { type: 'button', onclick: () => eliminar(c) },
        });
        acciones.append(btnEditar, btnEliminar);
        item.append(acciones);
      }
      lista.append(item);
    }

    zonaLista.replaceChildren(lista);
  }

  // --- Alta de cliente -----------------------------------------------------
  async function abrirAlta() {
    const campoNombre = campoTexto('Ej: Acme Corp', 'Nombre comercial *');
    const campoTaxId = campoTexto('Ej: 30-71234567-8', 'Tax ID *', { mono: true });
    const campoContacto = campoTexto('Ej: Juan Perez', 'Contacto');
    const campoEmail = campoTexto('Ej: juan@acme.com', 'Email');
    const campoTelefono = campoTexto('Ej: +54 11 5555-5555', 'Telefono');

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
      campoNombre.campo,
      campoTaxId.campo,
      campoContacto.campo,
      campoEmail.campo,
      campoTelefono.campo,
    );

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Guardar cliente',
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
      const commercial_name = campoNombre.input.value.trim();
      const tax_id = campoTaxId.input.value.trim();

      if (!commercial_name) return avisar('El nombre comercial es obligatorio', 'alerta');
      if (!tax_id) return avisar('El tax_id es obligatorio', 'alerta');

      const ok = await confirmar({
        titulo: 'Guardar cliente',
        mensaje: `¿Guardar el cliente "${commercial_name}" con el tax_id ${tax_id}?`,
        textoConfirmar: 'Guardar',
      });
      if (!ok) return;

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Guardando…';
      try {
        await api.crearCliente({
          commercial_name,
          tax_id,
          contact_name: campoContacto.input.value.trim() || undefined,
          contact_email: campoEmail.input.value.trim() || undefined,
          contact_phone: campoTelefono.input.value.trim() || undefined,
        });
        avisar('Cliente creado correctamente', 'exito', 2500);
        cerrarModal();
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Guardar cliente';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: 'Nuevo cliente',
      nota: 'El tax_id es unico. Identifica al cliente ante impuestos.',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [btnCancelar],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

  // --- Edicion de cliente ---------------------------------------------------
  async function abrirEdicion(c) {
    const campoNombre = campoTexto('Ej: Acme Corp', 'Nombre comercial *');
    campoNombre.input.value = c.commercial_name || '';
    const campoTaxId = campoTexto('Ej: 30-71234567-8', 'Tax ID *', { mono: true });
    campoTaxId.input.value = c.tax_id || '';
    const campoContacto = campoTexto('Ej: Juan Perez', 'Contacto');
    campoContacto.input.value = c.contact_name || '';
    const campoEmail = campoTexto('Ej: juan@acme.com', 'Email');
    campoEmail.input.value = c.contact_email || '';
    const campoTelefono = campoTexto('Ej: +54 11 5555-5555', 'Telefono');
    campoTelefono.input.value = c.contact_phone || '';

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
      campoNombre.campo,
      campoTaxId.campo,
      campoContacto.campo,
      campoEmail.campo,
      campoTelefono.campo,
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
      const commercial_name = campoNombre.input.value.trim();
      const tax_id = campoTaxId.input.value.trim();

      if (!commercial_name) return avisar('El nombre comercial es obligatorio', 'alerta');
      if (!tax_id) return avisar('El tax_id es obligatorio', 'alerta');

      const ok = await confirmar({
        titulo: 'Guardar cambios',
        mensaje: `¿Guardar los cambios del cliente "${commercial_name}"?`,
        textoConfirmar: 'Guardar cambios',
      });
      if (!ok) return;

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Guardando…';
      try {
        await api.actualizarCliente(c.id, {
          commercial_name,
          tax_id,
          contact_name: campoContacto.input.value.trim() || undefined,
          contact_email: campoEmail.input.value.trim() || undefined,
          contact_phone: campoTelefono.input.value.trim() || undefined,
        });
        avisar('Cliente actualizado correctamente', 'exito', 2500);
        cerrarModal();
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Guardar cambios';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: `Editar cliente — ${c.commercial_name}`,
      nota: 'El tax_id es unico. Identifica al cliente ante impuestos.',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [btnCancelar],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

  // --- Eliminar cliente -----------------------------------------------------
  function eliminar(c) {
    abrirDialogo({
      titulo: 'Eliminar cliente',
      mensaje: `¿Eliminar al cliente "${c.commercial_name}"? Esta acción no se puede deshacer.`,
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
            await api.eliminarCliente(c.id);
            avisar('Cliente eliminado', 'exito', 2500);
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

  // El buscador va al SERVIDOR: con la lista paginada, filtrar en el
  // navegador solo miraria los 50 de la pagina.
  inputBuscar.addEventListener('input', () => {
    pagina = 0;
    cargar();
  });

  cargar();
  return { elemento: raiz };
}

/** Iniciales del nombre comercial para el avatar. */
function obtenerIniciales(nombre) {
  if (!nombre) return '?';
  return nombre
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() ?? '')
    .join('');
}

/** Tarjeta de estadistica. */
function statCard(etiqueta, valor, iconoPath, color) {
  const card = crear('div', { clase: 'stat-card' });
  const iconoEl = icono(iconoPath, { tamano: 18 });
  iconoEl.style.color = color;
  card.append(
    crear('div', { clase: 'stat-card__valor tabular', texto: String(valor), estilo: `color:${color}` }),
    crear('div', { clase: 'stat-card__etiqueta', texto: etiqueta }),
    iconoEl,
  );
  return card;
}

/** Campo de texto con su etiqueta. */
function campoTexto(placeholder, etiqueta, { type = 'text', valor = '', mono = false } = {}) {
  const input = crear('input', {
    clase: `campo__control${mono ? ' mono' : ''}`,
    atributos: {
      type,
      placeholder,
      autocomplete: 'off',
      ...(valor ? { value: valor } : {}),
    },
  });
  const campo = crear('label', {
    clase: 'campo',
    hijos: [crear('span', { clase: 'campo__etiqueta', texto: etiqueta }), input],
  });
  return { campo, input };
}
