/**
 * Consultas en lenguaje natural y reportes.
 *
 * EL XSS DE Está VISTA
 * --------------------
 * El `index.html` anterior hacia:
 *
 *     box.innerHTML += `<div>Tú: ${q}</div>`;             // reflejado
 *     box.innerHTML += `<div>IA: ${j.respuesta}</div>`;   // almacenado
 *
 * El primero es XSS reflejado: el usuario escribe `<img src=x onerror=...>` y
 * se ejecuta al instante.
 *
 * El segundo es peor, porque es indirecto: la respuesta de la IA suele devolver
 * en prosa parte de lo que se le pregunto ("Los equipos con serial `<img
 * src=x onerror=...>` son..."). O sea que un texto malicioso entra por el
 * usuario, sale por el modelo y vuelve a ejecutarse. Es el camino de XSS más
 * subestimado que hay, y por eso la respuesta del modelo se trata exactamente
 * como la del usuario: `textContent`, siempre.
 *
 * `white-space: pre-wrap` para que el modelo pueda poner parrafos y listas sin
 * que haya que interpretar etiquetas.
 */

import { api } from '../api.js';
import { crear } from '../dom.js';
import { avisar } from '../avisos.js';

const PREGUNTAS_SUGERIDAS = [
  'Cuantos equipos hay en depósito?',
  'Que equipos están instalados hace más de un ano?',
  'Cuantas reparaciones abiertas hay?',
  'Que modelos se están rompiendo más?',
];

// Cuantos turnos se guardan para darle contexto a la pregunta siguiente.
// Seis es lo suficiente para "y de esos, cuales..." y lo bastante poco para
// que el pedido no crezca sin límite.
const MAX_TURNOS = 6;

// Los nombres de columna que se repiten, traducidos. El resto se muestra con
// el nombre de la columna, pero CON ESPACIOS: "serial_number" se entiende
// mucho mejor que "serial_number".
const COLUMNAS_TRADUCIDAS = {
  count: 'Cantidad',
  total: 'Total',
  serial_number: 'Número de serie',
  qr_code: 'Código QR',
  current_status: 'Estado',
  store_name: 'Ubicacion',
  address: 'Direccion',
  sku: 'SKU',
  brand: 'Marca',
  model: 'Modelo',
  model_name: 'Modelo',
  variant: 'Variante',
  category_id: 'Categoria',
  name: 'Nombre',
  full_name: 'Nombre',
  email: 'Email',
  created_at: 'Creado',
  entry_date: 'Ingreso',
  exit_date: 'Salida',
  incident_id: 'Caso',
  code: 'Codigo',
  title: 'Titulo',
  severity: 'Severidad',
  status: 'Estado',
  diagnosis: 'Diagnostico',
  work_performed: 'Trabajo realizado',
  location_name: 'Ubicacion',
  available_count: 'Disponibles',
  min_stock: 'Stock mínimo',
};

/** El nombre de una columna, para mostrarlo. */
function nombreColumna(columna) {
  if (COLUMNAS_TRADUCIDAS[columna]) return COLUMNAS_TRADUCIDAS[columna];
  const palabras = String(columna).replace(/_/g, ' ').trim();
  return palabras.charAt(0).toUpperCase() + palabras.slice(1);
}

export function vistaConsultas({ permisos = [] } = {}) {
  const raiz = crear('div');

  // El historial de la conversacion: lo que permite que "cual es ese equipo
  // que se encontro?" entienda de que equipo se trata.
  const historial = [];

  // --- Estado de la IA ------------------------------------------------------
  const tarjetaEstado = crear('div', { clase: 'tarjeta' });
  raiz.append(tarjetaEstado);
  pintarEstadoIA(tarjetaEstado);

  // --- Preguntas ------------------------------------------------------------
  // En pantallas anchas, layout de dos columnas: chat a la izquierda,
  // sugerencias y reporte a la derecha. En pantallas angostas, todo apilado.
  const layoutConsultas = crear('div', { clase: 'layout-consultas' });

  const tarjetaChat = crear('div', { clase: 'tarjeta' });

  const caja = crear('div', {
    clase: 'apilado chat-caja',
    estilo:
      'max-height:32rem;overflow-y:auto;padding:0.85rem;background:var(--superficie-2);' +
      'border-radius:var(--radio-sm);margin-bottom:1rem;white-space:pre-wrap;',
  });
  caja.setAttribute('aria-live', 'polite');
  caja.setAttribute('role', 'log');

  const entrada = crear('textarea', {
    clase: 'campo__control',
    atributos: {
      rows: 2,
      placeholder: 'Preguntale al inventario…',
      // `maxlength`: el backend también limita el largo, pero acortarlo acá
      // evita mandar un parrafo de 500 KB y esperar una respuesta de 15 s.
      maxlength: 500,
    },
  });

  const botonEnviar = crear('button', {
    clase: 'boton',
    texto: 'Preguntar',
    atributos: { type: 'submit' },
  });

  const formulario = crear('form', {
    atributos: {
      onsubmit: (evento) => {
        evento.preventDefault();
        enviar();
      },
    },
  });

  const filaEntrada = crear('div', { clase: 'fila-entrada' });
  filaEntrada.append(entrada, botonEnviar);

  formulario.append(filaEntrada);

  // Sugerencias. Saves clicks pero son la mitad del espacio de la pantalla en
  // un celular, así que se muestran solo si hay lugar.
  const sugerencias = crear('div', { clase: 'fila-botones' });
  for (const texto of PREGUNTAS_SUGERIDAS) {
    sugerencias.append(
      crear('button', {
        clase: 'boton boton--suave',
        texto: texto,
        atributos: {
          type: 'button',
          onclick: () => {
            entrada.value = texto;
            enviar();
          },
        },
      }),
    );
  }

  tarjetaChat.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Consultar al inventario' }),
    crear('p', {
      clase: 'tarjeta__subtitulo',
      texto: 'Pregunta en español. La respuesta sale de los datos reales.',
    }),
    caja,
    formulario,
  );

  // Columna lateral: sugerencias + reporte (visible en pantallas anchas)
  const lateral = crear('div', { clase: 'layout-consultas__lateral' });

  const cajaSugerencias = crear('div', { clase: 'tarjeta' });
  cajaSugerencias.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Sugerencias' }),
    sugerencias,
  );
  lateral.append(cajaSugerencias);

  // --- Reporte --------------------------------------------------------------
  if (permisos.includes('ai:report')) {
    const botonReporte = crear('button', {
      clase: 'boton boton--exito boton--bloque',
      texto: 'Generar reporte operativo',
      atributos: { type: 'button', onclick: () => generarReporte(caja) },
    });

    const tarjetaReporte = crear('div', { clase: 'tarjeta' });
    tarjetaReporte.append(
      crear('h2', { clase: 'tarjeta__titulo', texto: 'Reporte' }),
      crear('p', {
        clase: 'tarjeta__subtitulo',
        texto: 'Cifras de la base de datos, redactadas por el modelo.',
      }),
      botonReporte,
    );
    lateral.append(tarjetaReporte);
  }

  layoutConsultas.append(tarjetaChat, lateral);
  raiz.append(layoutConsultas);

  async function enviar() {
    const pregunta = entrada.value.trim();
    if (!pregunta) return;

    agregarMensaje('Tú', pregunta, 'info');
    entrada.value = '';
    botonEnviar.disabled = true;

    const pensando = crear('p', { clase: 'tenue pequeno', texto: 'Consultando…' });
    caja.append(pensando);

    try {
      // El HISTORIAL
      // -------------
      // "Cual es ese equipo que se encontro?" no se puede responder sin
      // saber de que equipo se trata. El backend lo usa para no cambiar de
      // tema y para traer el DETALLE de lo que antes se conto.
      const respuesta = await api.preguntar(pregunta, historial);
      pensando.remove();

      if (respuesta.error) {
        agregarMensaje('Sistema', respuesta.error, 'alerta');
      } else {
        agregarMensaje('Inventario', respuesta.respuesta || 'Sin respuesta.', 'exito');
      }

      // La tabla con los DATOS.
      //
      // Sin esto, el operador ve la prosa y el SELECT, pero no el producto
      // queasked: el SQL dice que se busco, y los datos quedan escondidos
      // en la respuesta de la API, que nadie mira.
      if (respuesta.datos?.length && respuesta.columnas?.length) {
        caja.lastChild.append(tablaDeDatos(respuesta));
      }

      // La accion sugerida ("anda a Productos"): un botón, no un texto.
      if (respuesta.sugerencia?.vista) {
        const btnSugerencia = crear('button', {
          clase: 'boton boton--suave boton--chico',
          texto: respuesta.sugerencia.texto,
          atributos: { type: 'button' },
        });
        btnSugerencia.addEventListener('click', () => {
          document.dispatchEvent(
            new CustomEvent('app:ir-a', { detail: { vista: respuesta.sugerencia.vista } }),
          );
        });
        caja.lastChild.append(btnSugerencia);
      }

      // El SQL se muestra solo si el backend lo devolucion. Ver la nota de
      // seguridad: el modelo escribe el SELECT, y verlo ayuda a entender de
      // donde sale el número.
      // El turno se guarda para la próxima pregunta. El resumen lo arma el
      // backend: es el tema de la consulta y los valores que identifican a
      // cada fila (el serial, el SKU).
      if (!respuesta.error) {
        historial.push({
          pregunta,
          sql: respuesta.sql ?? null,
          resumen: respuesta.resumen ?? null,
        });
        if (historial.length > MAX_TURNOS) historial.shift();
      }

      if (respuesta.sql) {
        const detalle = crear('details', { 
          clase: 'sql-detalle',
          estilo: 'margin-top:0.5rem' 
        });
        const resumen = crear('summary', { 
          clase: 'pequeno tenue sql-summary', 
          texto: 'Ver la consulta SQL' 
        });
        const codigo = crear('pre', {
          clase: 'mono pequeno sql-code',
          // Sin estilo inline - todo en CSS
        });
        // De nuevo: `textContent`. El SQL viene de un LLM.
        codigo.textContent = respuesta.sql;
        detalle.append(resumen, codigo);
        caja.lastChild.append(detalle);
      }
    } catch (error) {
      pensando.remove();
      agregarMensaje('Sistema', error.message, 'error');
    } finally {
      botonEnviar.disabled = false;
      entrada.focus();
    }
  }

  async function generarReporte(destino) {
    const pensando = crear('p', { clase: 'tenue pequeno', texto: 'Generando reporte…' });
    destino.append(pensando);

    try {
      const respuesta = await api.reporte();
      pensando.remove();
      agregarMensaje(
        'Reporte',
        respuesta.reporte || respuesta.respuesta || 'Sin contenido.',
        'exito',
      );
    } catch (error) {
      pensando.remove();
      agregarMensaje('Sistema', error.message, 'error');
    }
  }

  /**
   * La tabla con los datos de la consulta.
   *
   * POR QUE Está Y NO Solo LA PROSA
   * ------------------------------
   * El operador pregunto "cual es ese equipo que se encontro?". La prosa
   * dice "se encontro 1 equipo" y la tabla dice cual es. Con las dos, la
   * respuesta se lee sin adivinar; con solo la prosa, el operador tiene que
   * mirar el SELECT para entender de que se habla.
   *
   * TODOS LOS VALORES VAN POR `textContent`: los datos vienen de la base,
   * pero un texto puede traer HTML de un serial mal cargado, y esto es un
   * chat que se repite en cada pantalla.
   */
  function tablaDeDatos(respuesta) {
    const envoltorio = crear('div', { clase: 'ia-tabla', estilo: 'margin-top:0.6rem' });

    const tabla = crear('table', { clase: 'ia-tabla__tabla' });

    const cabecera = crear('thead');
    const filaCabecera = crear('tr');
    for (const columna of respuesta.columnas) {
      const celda = crear('th', {
        texto: nombreColumna(columna),
        atributos: { scope: 'col' },
      });
      filaCabecera.append(celda);
    }
    cabecera.append(filaCabecera);
    tabla.append(cabecera);

    const cuerpo = crear('tbody');
    for (const fila of respuesta.datos) {
      const tr = crear('tr');
      for (const columna of respuesta.columnas) {
        const valor = fila?.[columna];
        const celda = crear('td');
        // `null` se muestra como un guion y no como "null": es la diferencia
        // entre "no hay dato" y "el sistema fallo".
        celda.textContent = valor === null || valor === undefined ? '—' : String(valor);
        tr.append(celda);
      }
      cuerpo.append(tr);
    }
    tabla.append(cuerpo);
    envoltorio.append(tabla);

    // El pie solo cuando aporta: "1 fila." debajo de una tabla de una fila
    // es ruido. Cuando hay varias (o se truncaron) el número importa.
    if (respuesta.truncado || respuesta.cantidad_filas > 1) {
      envoltorio.append(
        crear('p', {
          clase: 'pequeno tenue',
          estilo: 'margin:0.4rem 0 0',
          texto: respuesta.truncado
            ? `Mostrando las primeras ${respuesta.datos.length} filas de ${respuesta.cantidad_filas}.`
            : `${respuesta.cantidad_filas} filas.`,
        }),
      );
    }

    return envoltorio;
  }

  /**
   * Un mensaje en la caja de conversacion.
   *
   * El texto va SIEMPRE por `textContent`. No hay rama de este archivo que use
   * `innerHTML`, y esa es la garantía de que la salida del modelo no puede
   * ejecutar nada.
   */
  function agregarMensaje(quien, texto, tono) {
    const colores = {
      exito: 'var(--exito)',
      error: 'var(--error)',
      alerta: 'var(--alerta)',
      info: 'var(--primario-fuerte)',
    };

    const bloque = crear('div', { estilo: 'margin-bottom:0.9rem' });

    const autor = crear('strong', {
      estilo: `color:${colores[tono] ?? 'var(--texto)'};font-size:0.82rem;`,
      texto: `${quien}:`,
    });

    const cuerpo = crear('div', { estilo: 'margin-top:0.15rem' });
    cuerpo.textContent = String(texto ?? '');

    bloque.append(autor, cuerpo);
    caja.append(bloque);

    // Se baja al final para que el último mensaje se vea sin tener que scrollear.
    caja.scrollTop = caja.scrollHeight;
  }

  if (!caja.childElementCount) {
    agregarMensaje(
      'Inventario',
      'Preguntame por el inventario. Puedo contar equipos por estado, listar ' +
        'los que llevan mucho instalados y buscar los que más visitas al taller. ' +
        'Te muestro los datos en una tabla y entiendo las preguntas de ' +
        'seguimiento ("cual es ese equipo que se encontro?").',
      'info',
    );
  }

  return { elemento: raiz };
}

async function pintarEstadoIA(tarjeta) {
  const cabecera = crear('div', { clase: 'flex-entre' });
  cabecera.append(crear('h2', { clase: 'tarjeta__titulo', texto: 'Estado de la IA' }));
  tarjeta.append(cabecera);

  const cuerpo = crear('p', { clase: 'tenue pequeno', texto: 'Consultando…' });
  tarjeta.append(cuerpo);

  try {
    const estado = await api.estadoIA();

    const ok = estado.disponible && estado.respondio;
    const clase = ok ? 'exito' : 'alerta';

    cuerpo.replaceChildren();
    cuerpo.append(
      crear('span', {
        clase: `insignia insignia--${clase === 'exito' ? 'instalado' : 'transito'}`,
        texto: ok ? 'Operativa' : 'Con problemas',
      }),
    );

    const detalle = crear('div', { estilo: 'margin-top:0.5rem' });
    detalle.append(
      crear('div', { clase: 'pequeno', texto: `Proveedor: ${estado.proveedor ?? '—'}` }),
      crear('div', { clase: 'pequeno', texto: `Modelo: ${estado.modelo_configurado ?? '—'}` }),
    );

    if (estado.latencia_ms !== undefined && estado.latencia_ms !== null) {
      detalle.append(
        crear('div', { clase: 'pequeno', texto: `Latencia: ${estado.latencia_ms} ms` }),
      );
    }

    if (!ok && estado.detalle) {
      detalle.append(
        crear('div', { clase: 'pequeno', texto: `Detalle: ${estado.detalle}` }),
      );
    }

    cuerpo.append(detalle);
  } catch (error) {
    cuerpo.textContent = `No se pudo consultar el estado: ${error.message}`;
    avisar(error.message, 'alerta');
  }
}
