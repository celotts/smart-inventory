/**
 * Vista de inventario: listado, busqueda y ficha de un componente.
 *
 * LA FICHA ES DONDE SE VIVE LA TRAZABILIDAD
 * ------------------------------------------
 * Mostra el estado actual, los dias que lleva instalado y el historial completo
 * de movimientos con su hora, su sede y quien lo registro. Esa combinacion es la
 * que responde "donde estuvo este equipo", que es la pregunta que se hace
 * cuando algo no aparece.
 *
 * Sin `innerHTML`: los nombres de sede y de usuario vienen de la base y los
 * digita gente, asi que son texto de usuario para efectos de seguridad.
 */

import { api } from '../api.js';
import { ESTADOS, ICONOS, MOVIMIENTOS, crear, etiquetaProducto, icono } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { imagenQr } from '../qr.js';
import { descargarCsv } from '../descargas.js';
import { conTooltip } from '../tooltip.js';

const ESTADOS_FILTRO = [
  { valor: '', texto: 'Todos' },
  { valor: 'IN_WAREHOUSE', texto: 'En deposito' },
  { valor: 'IN_TRANSIT', texto: 'En transito' },
  { valor: 'INSTALLED', texto: 'Instalados' },
  { valor: 'IN_REPAIR', texto: 'En taller' },
  { valor: 'PERMANENTLY_DISPOSED', texto: 'Dados de baja' },
];

/**
 * Campo de texto con su etiqueta.
 *
 * MISMA FIRMA QUE EN PRODUCTOS Y CLIENTES
 * ---------------------------------------
 * `(placeholder, etiqueta, { valor, mono, type })`.
 *
 * ESTA FUNCION NO EXISTIA EN ESTA VISTA
 * -------------------------------------
 * Se usaba en el formulario de edicion y en el de baja, pero no estaba
 * definida ni importada: la primera llamada tiraba `ReferenceError: campoTexto
 * is not defined`, que MATABA la funcion `abrirEdicion` entera. Como el
 * error era una promesa rechazada sin manejar, no se veia nada en pantalla:
 * el boton "Editar" no hacia nada y no aparecia ningun error.
 *
 * Es el mismo bug de fondo que el de `crear('div', {clase}, [hijo])`: una
 * funcion que se usa y no existe, y el error se pierde en el camino.
 */
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

export function vistaInventario({ alAbrirDispositivo, filtros }) {
  const raiz = crear('div');

  // --- Filtros --------------------------------------------------------------
  const tarjetaFiltros = crear('div', { clase: 'tarjeta' });

  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por serial, QR, SKU o modelo',
      autocomplete: 'off',
      autocapitalize: 'characters',
      spellcheck: 'false',
    },
  });

  const selectEstado = crear('select', { clase: 'campo__control' });
  for (const opcion of ESTADOS_FILTRO) {
    selectEstado.append(
      crear('option', { texto: opcion.texto, atributos: { value: opcion.valor } }),
    );
  }

  // Categoria y ubicacion: filtros COMBINABLES, que bajan a las subcategorias
  // y a los pasillos. Filtrar por "Deposito Central" tiene que traer lo que
  // esta en sus estantes: el operador pregunta por la bodega.
  const selectCategoria = crear('select', { clase: 'campo__control' });
  selectCategoria.append(crear('option', { texto: 'Todas las categorias', atributos: { value: '' } }));

  const selectUbicacion = crear('select', { clase: 'campo__control' });
  selectUbicacion.append(crear('option', { texto: 'Todas las ubicaciones', atributos: { value: '' } }));

  const etiquetaBusqueda = crear('label', { clase: 'campo' });
  const textoBusqueda = crear('span', { clase: 'campo__etiqueta', texto: 'Buscar' });
  etiquetaBusqueda.append(textoBusqueda, inputBuscar);
  conTooltip(
    textoBusqueda,
    'Busqueda de texto completo: serial, codigo de etiqueta, SKU, marca o modelo. ' +
      'Tambien acepta un fragmento ("notebook dell" encuentra el "Dell Latitude").',
  );

  const etiquetaEstado = crear('label', { clase: 'campo' });
  etiquetaEstado.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Estado' }),
    selectEstado,
  );

  const etiquetaCategoria = crear('label', { clase: 'campo' });
  const textoCategoria = crear('span', { clase: 'campo__etiqueta', texto: 'Categoria' });
  etiquetaCategoria.append(textoCategoria, selectCategoria);
  conTooltip(textoCategoria, 'Filtra por la categoria del producto. Trae tambien las subcategorias.');

  const etiquetaUbicacion = crear('label', { clase: 'campo' });
  const textoUbicacion = crear('span', { clase: 'campo__etiqueta', texto: 'Ubicacion' });
  etiquetaUbicacion.append(textoUbicacion, selectUbicacion);
  conTooltip(textoUbicacion, 'Filtra por la ubicacion. Trae tambien sus pasillos y estantes.');

  const btnExportar = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Exportar CSV',
    atributos: { type: 'button' },
  });
  conTooltip(btnExportar, 'Descarga el inventario completo en CSV, listo para abrir en Excel.');

  const filaBotones = crear('div', { clase: 'fila-botones' });
  filaBotones.append(btnExportar);

  const rejillaFiltros = crear('div', { clase: 'rejilla-filtros' });
  rejillaFiltros.append(etiquetaBusqueda, etiquetaEstado, etiquetaCategoria, etiquetaUbicacion);

  tarjetaFiltros.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Inventario' }),
    rejillaFiltros,
    filaBotones,
  );

  // --- Resultados -----------------------------------------------------------
  //
  // `aria-live="polite"`: la lista cambia con cada tecla del buscador, y sin
  // esto un lector de pantalla no se entera de nada. Es `polite` y no
  // `assertive` porque el cambio NO es urgente: no interrumpe lo que el
  // operador esta haciendo.
  const zonaLista = crear('div', {
    atributos: { 'aria-live': 'polite', 'aria-label': 'Resultados de la busqueda' },
  });
  const contadorResultados = crear('p', { clase: 'tenue pequeno' });
  const zonaPaginacion = crear('div', {
    clase: 'fila-botones',
    estilo: 'margin-top:1rem',
  });

  raiz.append(tarjetaFiltros, contadorResultados, zonaLista, zonaPaginacion);

  let pagina = 0;
  const POR_PAGINA = 25;
  let total = 0;
  let peticionEnCurso = 0;

  /**
   * Se pide por parametro y no por `debounce` con `setTimeout`.
   *
   * Un debounce tiene una condicion de carrera clasica: si el usuario escribe
   * "ABC" (request 1, lento) y despues "ABCD" (request 2, rapido), el request 2
   * vuelve primero y el 1 despues sobreescribe con "ABC". Con un contador, cada
   * respuesta se descarta si no es la ultima pedida.
   */
  async function cargar() {
    const numero = ++peticionEnCurso;

    zonaLista.replaceChildren(
      crear('div', { clase: 'cargando', texto: 'Buscando…' }),
    );

    try {
      const datos = await api.listarDispositivos({
        busqueda: inputBuscar.value.trim(),
        estado: selectEstado.value || undefined,
        categoria_id: selectCategoria.value || undefined,
        ubicacion_id: selectUbicacion.value || undefined,
        limite: POR_PAGINA,
        offset: pagina * POR_PAGINA,
      });

      if (numero !== peticionEnCurso) return; // respuesta vieja

      total = datos.total;
      contadorResultados.textContent = datos.total
        ? `${datos.total} ${datos.total === 1 ? 'equipo' : 'equipos'}`
        : 'Sin resultados';
      pintarLista(datos.items);
      pintarPaginacion();
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  function pintarLista(items) {
    if (!items.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.buscar, { tamano: 28 }),
        crear('p', { texto: 'Ningun componente coincide con la busqueda.' }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const item of items) {
      const estadoInfo = ESTADOS[item.current_status] ?? {
        texto: '—',
        clase: 'insignia--baja',
      };

      const boton = crear('button', {
        clase: 'lista__item',
        atributos: {
          type: 'button',
          onclick: () => alAbrirDispositivo(item.id),
        },
      });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', {
          clase: 'lista__titulo mono',
          texto: item.serial_number,
        }),
        crear('div', {
          clase: 'lista__meta',
          texto: [item.marca, item.modelo].filter(Boolean).join(' ') || 'Sin modelo',
        }),
      );

      const insignia = crear('span', {
        clase: `insignia ${estadoInfo.clase}`,
        texto: estadoInfo.texto,
      });

      boton.append(cuerpo, insignia);
      lista.append(crear('li', { hijos: [boton] }));
    }

    zonaLista.replaceChildren(lista);
  }

  function pintarPaginacion() {
    zonaPaginacion.replaceChildren();

    const totalPaginas = Math.ceil(total / POR_PAGINA);
    if (totalPaginas <= 1) return;

    const contador = crear('span', {
      clase: 'tenue pequeno centro',
      texto: `${pagina + 1} de ${totalPaginas} · ${total} componentes`,
    });

    const anterior = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Anterior',
      atributos: {
        type: 'button',
        disabled: pagina === 0,
        onclick: () => {
          pagina -= 1;
          cargar();
        },
      },
    });

    const siguiente = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Siguiente',
      atributos: {
        type: 'button',
        disabled: pagina >= totalPaginas - 1,
        onclick: () => {
          pagina += 1;
          cargar();
        },
      },
    });

    zonaPaginacion.append(anterior, contador, siguiente);
  }

  // Cada `input` dispara una busqueda. Con `limite: 25` y una consulta que usa
  // indice, no es caro; y una busqueda instantanea se siente mucho mas rapida
  // que una que espera medio segundo.
  // El filtro con el que se llega desde el tablero: el select queda puesto
  // ANTES de la primera carga, para que la lista abra filtrada.
  if (filtros?.estado) selectEstado.value = filtros.estado;

  inputBuscar.addEventListener('input', () => {
    pagina = 0;
    cargar();
  });

  selectEstado.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });

  selectCategoria.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });

  selectUbicacion.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });

  btnExportar.addEventListener('click', () => {
    descargarCsv('/export/inventario.csv', 'inventario.csv');
  });

  /**
   * Carga los filtros de categoria y ubicacion.
   *
   * Las dos listas se piden juntas con `Promise.all`: son la misma pantalla y
   * una tras otra seria dos esperas.
   */
  async function cargarFiltros() {
    try {
      const [categorias, ubicaciones] = await Promise.all([
        api.categorias(),
        api.pedir('/locations'),
      ]);

      for (const c of categorias) {
        // La ruta completa ("Notebook > Notebooks 14\"") y no el nombre solo:
        // con subcategorias hay dos "Impresora" y el operador tiene que ver
        // de que rama sale cada una.
        selectCategoria.append(
          crear('option', {
            texto: c.ruta || c.name,
            atributos: { value: c.id },
          }),
        );
      }

      for (const u of ubicaciones) {
        selectUbicacion.append(
          crear('option', {
            texto: u.ruta || u.store_name,
            atributos: { value: u.id },
          }),
        );
      }
    } catch (error) {
      // Los filtros son una ayuda: si no se pueden cargar, la lista igual
      // tiene que andar. El error se deja pasar en silencio a proposito.
      console.warn('No se pudieron cargar los filtros:', error);
    }
  }

  // Recargar lista cuando se crea un componente nuevo
  const handleRecargar = () => {
    pagina = 0;
    cargar();
  };
  document.addEventListener('inventario:recargar', handleRecargar);

  // Limpieza al destruir la vista
  const destruir = () => {
    document.removeEventListener('inventario:recargar', handleRecargar);
  };

  cargar();
  cargarFiltros();
  return { elemento: raiz };
}

/**
 * Ficha de un componente.
 *
 * @param {number} deviceId
 * @param {{alVolver: Function}} opciones
 */
export function vistaDispositivo(deviceId, { alVolver, permisos = [], alEliminar } = {}) {
  const raiz = crear('div');

  const puedeEditar = permisos.includes('devices:edit');

  const cabecera = crear('div', {
    clase: 'flex-entre',
    estilo: 'margin-bottom:1rem',
  });

  const botonVolver = crear('button', {
    clase: 'boton boton--suave',
    texto: 'Volver',
    atributos: {
      type: 'button',
      onclick: () => alVolver?.(),
    },
  });

  cabecera.append(botonVolver);

  // Las acciones de escritura van en la ficha y no en la lista: editar o dar de
  // baja un equipo es una decision que se toma DESPUES de ver su historial, no
  // desde una fila con el serial a medias.
  const botonEditar = crear('button', {
    clase: 'boton',
    texto: 'Editar',
    atributos: { type: 'button', onclick: () => abrirEdicion() },
  });

  const botonBaja = crear('button', {
    clase: 'boton boton--peligro',
    texto: 'Dar de baja',
    atributos: { type: 'button', onclick: () => abrirBaja() },
  });

  if (puedeEditar) {
    cabecera.append(crear('div', { clase: 'fila-botones', hijos: [botonEditar, botonBaja] }));
  }

  const zona = crear('div');
  zona.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando ficha…' }));

  raiz.append(cabecera, zona);

  // La ficha vive en una variable porque los modales la necesitan para
  // prellenar el formulario sin volver a pedirla (y sin riesgo de mostrar un
  // dato viejo si la peticion de prellenado vuelve despues de la de la ficha).
  let fichaActual = null;

  cargar();

  async function cargar() {
    try {
      const [ficha, historial] = await Promise.all([
        api.obtenerDispositivo(deviceId),
        api.historial(deviceId, { limite: 50 }),
      ]);

      fichaActual = ficha;
      zona.replaceChildren(tarjetaFicha(ficha), tarjetaHistorial(historial));
    } catch (error) {
      zona.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
      avisar(error.message, 'error');
    }
  }

  /**
   * Formulario de edicion.
   *
   * Solo manda los campos que CAMBIARON. El backend acepta un PATCH parcial, y
   * mandar todo lo que muestra la pantalla es la via por la que se pisan datos:
   * la pantalla no conoce todos los campos del equipo (no ve, por ejemplo, el
   * `is_warehouse` denormalizado), asi que un "guardar todo" borra lo que no ve.
   */
  async function abrirEdicion() {
    console.log('[QA] abrirEdicion, ficha=', JSON.stringify(fichaActual));
    const ficha = fichaActual;
    if (!ficha) { console.log('[QA] salida: sin ficha'); return; }

    let productos = [];
    let ubicaciones = [];
    try {
      [productos, ubicaciones] = await Promise.all([
        api.productos(),
        api.ubicaciones(),
      ]);
    } catch (error) {
      avisar(`No se pudo cargar el catalogo: ${error.message}`, 'error');
      return;
    }

    const campoSerial = campoTexto('Ej: SN-12345678', 'Serial', {
      valor: ficha.serial_number,
      mono: true,
    });
    const campoQR = campoTexto('Ej: INV-a1b2c3d4', 'Codigo QR', {
      valor: ficha.qr_code,
      mono: true,
    });

    const selectProducto = crear('select', { clase: 'campo__control' });
    for (const p of productos) {
      const opcion = crear('option', {
        texto: etiquetaProducto(p),
        atributos: { value: p.id },
      });
      if (p.id === ficha.modelo_id) opcion.selected = true;
      selectProducto.append(opcion);
    }

    const selectUbicacion = crear('select', { clase: 'campo__control' });
    selectUbicacion.append(crear('option', { texto: 'Sin ubicacion', atributos: { value: '' } }));
    for (const u of ubicaciones.items ?? []) {
      const opcion = crear('option', { texto: u.nombre, atributos: { value: u.id } });
      if (u.id === ficha.ubicacion_id) opcion.selected = true;
      selectUbicacion.append(opcion);
    }

    // Solo las transiciones que el backend acepta desde el estado actual. Un
    // select con todas las opciones es una trampa: el operador elige una que el
    // servidor rechaza y el error se ve como un fallo de la app, no como una
    // regla del negocio.
    const TRANSICIONES = {
      IN_WAREHOUSE: ['IN_WAREHOUSE', 'IN_TRANSIT', 'IN_REPAIR'],
      IN_TRANSIT: ['IN_WAREHOUSE', 'IN_TRANSIT', 'IN_REPAIR', 'INSTALLED'],
      INSTALLED: ['INSTALLED', 'IN_TRANSIT'],
      IN_REPAIR: ['IN_REPAIR', 'IN_WAREHOUSE', 'IN_TRANSIT', 'INSTALLED'],
    };
    const selectEstado = crear('select', { clase: 'campo__control' });
    for (const estado of TRANSICIONES[ficha.current_status] ?? [ficha.current_status]) {
      const info = ESTADOS[estado] ?? { texto: estado };
      const opcion = crear('option', { texto: info.texto, atributos: { value: estado } });
      if (estado === ficha.current_status) opcion.selected = true;
      selectEstado.append(opcion);
    }

    const campoInstalacion = campoTexto(
      'AAAA-MM-DD',
      'Fecha de instalacion (AAAA-MM-DD)',
      { type: 'date', valor: (ficha.installation_date ?? '').slice(0, 10) },
    );

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
      campoSerial.campo,
      campoQR.campo,
      // El QR de la etiqueta, junto al campo que lo edita.
      //
      // El operador que esta corrigiendo el codigo tiene que ver a que
      // etiqueta se refiere: sin la imagen, "INV-a1b2c3d4" es un texto que
      // puede estar mal. Con la, ve la imagen escaneable y sabe si esta
      // Tocando el equipo correcto.
      crear('div', {
        clase: 'qr-formulario',
        hijos: [imagenQr(ficha.id, ficha.qr_code, 132)],
      }),
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Producto' }), selectProducto],
      }),
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Ubicacion' }), selectUbicacion],
      }),
      crear('label', {
        clase: 'campo',
        hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Estado' }), selectEstado],
      }),
      campoInstalacion.campo,
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
      const cambios = {};
      const serial = campoSerial.input.value.trim();
      const qr = campoQR.input.value.trim();
      const ubicacion = selectUbicacion.value;
      const fecha = campoInstalacion.input.value;

      if (serial && serial !== ficha.serial_number) cambios.serial_number = serial;
      if (qr && qr !== ficha.qr_code) cambios.qr_code = qr;
      if (selectProducto.value && parseInt(selectProducto.value, 10) !== ficha.modelo_id) {
        cambios.model_id = parseInt(selectProducto.value, 10);
      }
      if (ubicacion && parseInt(ubicacion, 10) !== ficha.ubicacion_id) {
        cambios.location_id = parseInt(ubicacion, 10);
      }
      if (selectEstado.value !== ficha.current_status) cambios.current_status = selectEstado.value;
      if (fecha && fecha !== (ficha.installation_date ?? '').slice(0, 10)) {
        cambios.installation_date = fecha;
      }

      if (!Object.keys(cambios).length) {
        cerrarModal(true);
        return;
      }

      const ok = await confirmar({
        titulo: 'Guardar cambios',
        mensaje: `¿Guardar los cambios del componente ${ficha.serial_number}?`,
        textoConfirmar: 'Guardar cambios',
      });
      if (!ok) return;

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Guardando…';
      try {
        await api.actualizarDispositivo(deviceId, cambios);
        avisar('Componente actualizado', 'exito', 2500);
        cerrarModal(true);
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Guardar cambios';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: 'Editar componente',
      nota: 'Solo se aplican los campos que cambiaste.',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [btnCancelar],
      // Avisar antes de perder los cambios escritos.
      avanzarAlCerrar: true,
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

  /**
   * Confirmacion de baja.
   *
   * El modal dice explicitamente que el historial SE CONSERVA, porque "borrar"
   * en cualquier otra app significa que el dato desaparece, y aca no: el equipo
   * sale del inventario activo pero el registro queda para la auditoria. Si no
   * se dice, el operador asume lo peor y no usa la funcion.
   */
  function abrirBaja() {
    const ficha = fichaActual;
    if (!ficha) return;

    const campoMotivo = campoTexto('Ej: Fin de contrato con el cliente', 'Motivo (opcional)');

    const form = crear('form', {
      clase: 'apilado',
      atributos: {
        onsubmit: async (e) => {
          e.preventDefault();
          await enviar();
        },
      },
    });
    form.append(campoMotivo.campo);

    const btnConfirmar = crear('button', {
      clase: 'boton boton--peligro boton--bloque',
      texto: 'Confirmar baja',
      atributos: { type: 'submit' },
    });
    form.append(btnConfirmar);

    let cerrarModal = () => {};
    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    async function enviar() {
      btnConfirmar.disabled = true;
      btnConfirmar.textContent = 'Dando de baja…';
      try {
        await api.darDeBajaDispositivo(deviceId, {
          motivo: campoMotivo.input.value.trim() || undefined,
        });
        avisar('Componente dado de baja', 'exito', 3000);
        cerrarModal();
        alEliminar?.();
      } catch (error) {
        avisar(error.message, 'error');
        btnConfirmar.disabled = false;
        btnConfirmar.textContent = 'Confirmar baja';
      }
    }

    cerrarModal = abrirDialogo({
      titulo: `Dar de baja ${ficha.serial_number}`,
      mensaje: 'El equipo deja de aparecer en el inventario y no se puede volver a usar la etiqueta. El historial de movimientos SE CONSERVA para la auditoria.',
      cuerpo: [form],
      clase: 'modal--formulario',
      acciones: [btnCancelar],
      alAbrir: (_modal, cerrar) => {
        cerrarModal = cerrar;
        btnCancelar.addEventListener('click', cerrar);
      },
    });
  }

  function tarjetaFicha(ficha) {
    const estadoInfo = ESTADOS[ficha.current_status] ?? {
      texto: 'Estado desconocido',
      clase: 'insignia--baja',
    };

    const def = crear('dl', { clase: 'def' });
    const fila = (clave, valor, opciones = {}) => {
      const dd = crear('dd', { clase: opciones.clase ?? '' });
      if (opciones.insignia) {
        dd.append(crear('span', { clase: `insignia ${opciones.insignia}`, texto: valor }));
      } else {
        dd.textContent = valor ?? '—';
      }
      def.append(crear('dt', { texto: clave }), dd);
    };

    fila('Serial', ficha.serial_number, { clase: 'mono' });
    fila('Codigo QR', ficha.qr_code, { clase: 'mono' });
    fila('Equipo', [ficha.marca, ficha.modelo].filter(Boolean).join(' ') || null);
    fila('Categoria', ficha.categoria);
    fila('Ubicacion actual', ficha.ubicacion_nombre);
    fila('Estado', estadoInfo.texto, { insignia: estadoInfo.clase });

    // Los dias instalados son el dato que hace legible el desgaste: 900 dias
    // en el mismo cliente es una senal que un humano tiene que ver.
    if (ficha.dias_instalado !== null && ficha.dias_instalado !== undefined) {
      fila('Instalado hace', `${ficha.dias_instalado} dias`);
    }
    fila('Movimientos registrados', ficha.movimientos_totales);

    const tarjeta = crear('div', { clase: 'tarjeta' });
    tarjeta.append(
      crear('h2', { clase: 'tarjeta__titulo', texto: 'Ficha del componente' }),
      def,
    );

    // El QR de la etiqueta.
    //
    // El texto del codigo esta arriba, en el `def`. Esta es la parte que
    // sirve de verdad: la imagen escaneable, que es lo que va pegado al
    // equipo y lo que el operador necesita para reimprimir la etiqueta o
    // para verificar de un vistazo que el equipo es este.
    const qr = imagenQr(ficha.id, ficha.qr_code);
    const contenedorQr = crear('div', { clase: 'qr-contenedor' });
    const captionQr = crear('div', { clase: 'qr-caption' });
    captionQr.append(
      crear('span', { clase: 'mono', texto: ficha.qr_code }),
      crear('span', { clase: 'pequeno tenue', texto: 'Codigo de la etiqueta' }),
    );
    contenedorQr.append(qr, captionQr);
    tarjeta.append(contenedorQr);

    if (ficha.esta_en_taller) {
      tarjeta.append(
        crear('div', {
          clase: 'resultado resultado--alerta',
          estilo: 'margin-top:1rem',
          texto: 'Tiene una reparacion abierta en el taller.',
        }),
      );
    }

    return tarjeta;
  }

  function tarjetaHistorial(historial) {
    const tarjeta = crear('div', { clase: 'tarjeta' });
    tarjeta.append(
      crear('h2', { clase: 'tarjeta__titulo', texto: 'Donde estuvo' }),
      crear('p', {
        clase: 'tarjeta__subtitulo',
        texto: historial.length
          ? `${historial.length} movimientos, del mas reciente al mas antiguo.`
          : '',
      }),
    );

    if (!historial.length) {
      tarjeta.append(
        crear('p', { clase: 'tenue', texto: 'Este componente todavia no fue movido.' }),
      );
      return tarjeta;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const movimiento of historial) {
      const item = crear('li', { clase: 'lista__item' });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', {
          clase: 'lista__titulo',
          texto: MOVIMIENTOS[movimiento.movement_type] ?? movimiento.movement_type,
        }),
      );

      // El origen y el destino se arman con texto, separando con "→". Nunca
      // con innerHTML: son nombres de sede, que escriben personas.
      const partes = [movimiento.origen, '→', movimiento.destino].filter(Boolean);
      const meta = crear('div', { clase: 'lista__meta' });
      meta.append(
        crear('span', { texto: partes.join(' ') }),
      );

      const fecha = crear('span', {
        clase: 'lista__meta tabular',
        texto: formatearFecha(movimiento.movement_date),
      });

      const detalle = crear('div', { clase: 'lista__meta' });
      detalle.append(
        crear('span', { texto: movimiento.registrado_por ?? 'Usuario desconocido' }),
      );

      cuerpo.append(meta, fecha, detalle);

      // Un escaneo fuera del radio de la sede se marca. No es un error: es el
      // dato que sirve para revisar despues, y esconderlo seria mentir.
      if (movimiento.is_valid_location === false) {
        const marca = crear('span', {
          clase: 'insignia insignia--peligro',
          texto: 'Fuera de sede',
        });
        cuerpo.append(marca);
      } else if (movimiento.is_valid_location === true) {
        cuerpo.append(crear('span', {
          clase: 'insignia insignia--instalado',
          texto: 'Verificado',
        }));
      }

      if (movimiento.equipo_reemplazado) {
        cuerpo.append(
          crear('div', {
            clase: 'lista__meta',
            texto: `Reemplazo a ${movimiento.equipo_reemplazado}${movimiento.replacement_reason ? `: ${movimiento.replacement_reason}` : ''}`,
          }),
        );
      }

      item.append(cuerpo);
      lista.append(item);
    }

    tarjeta.append(lista);
    return tarjeta;
  }

  return { elemento: raiz };
}

/**
 * Formatea una fecha en la zona horaria del dispositivo.
 *
 * `Intl.DateTimeFormat` con las opciones que el usuario ya tiene en su sistema.
 * Se pasa `undefined` como zona para que use la del navegador y no la del
 * servidor: un operador en otra zona tiene que ver su hora local, no la del
 * servidor, que es la que va en la base.
 */
export function formatearFecha(valor) {
  if (!valor) return '';

  const fecha = new Date(valor);
  if (Number.isNaN(fecha.getTime())) return String(valor);

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(fecha);
}
