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
import { crear, icono, ICONOS, ESTADOS, MOVIMIENTOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo } from '../dialogo.js';

const ESTADOS_FILTRO = [
  { valor: '', texto: 'Todos' },
  { valor: 'IN_WAREHOUSE', texto: 'En deposito' },
  { valor: 'IN_TRANSIT', texto: 'En transito' },
  { valor: 'INSTALLED', texto: 'Instalados' },
  { valor: 'IN_REPAIR', texto: 'En taller' },
  { valor: 'PERMANENTLY_DISPOSED', texto: 'Dados de baja' },
];

export function vistaInventario({ alAbrirDispositivo }) {
  const raiz = crear('div');

  // --- Filtros --------------------------------------------------------------
  const tarjetaFiltros = crear('div', { clase: 'tarjeta' });

  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por serial o codigo QR',
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

  const etiquetaBusqueda = crear('label', { clase: 'campo' });
  etiquetaBusqueda.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Buscar' }),
    inputBuscar,
  );

  const etiquetaEstado = crear('label', { clase: 'campo' });
  etiquetaEstado.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Estado' }),
    selectEstado,
  );

  tarjetaFiltros.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Inventario' }),
    etiquetaBusqueda,
    etiquetaEstado,
  );

  // --- Resultados -----------------------------------------------------------
  const zonaLista = crear('div');
  const zonaPaginacion = crear('div', {
    clase: 'fila-botones',
    estilo: 'margin-top:1rem',
  });

  raiz.append(tarjetaFiltros, zonaLista, zonaPaginacion);

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
        limite: POR_PAGINA,
        offset: pagina * POR_PAGINA,
      });

      if (numero !== peticionEnCurso) return; // respuesta vieja

      total = datos.total;
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
  inputBuscar.addEventListener('input', () => {
    pagina = 0;
    cargar();
  });

  selectEstado.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });

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

    const campoSerial = campoTexto(ficha.serial_number, 'Serial', { mono: true });
    const campoQR = campoTexto(ficha.qr_code, 'Codigo QR', { mono: true });

    const selectProducto = crear('select', { clase: 'campo__control' });
    for (const p of productos) {
      const opcion = crear('option', {
        texto: `${p.brand} ${p.model} (${p.sku})`,
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
      (ficha.installation_date ?? '').slice(0, 10),
      'Fecha de instalacion (AAAA-MM-DD)',
      { type: 'date' },
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
        cerrarModal();
        return;
      }

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Guardando…';
      try {
        await api.actualizarDispositivo(deviceId, cambios);
        avisar('Componente actualizado', 'exito', 2500);
        cerrarModal();
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
