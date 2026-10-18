/**
 * Consultas en lenguaje natural y reportes.
 *
 * EL XSS DE ESTA VISTA
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
 * usuario, sale por el modelo y vuelve a ejecutarse. Es el camino de XSS mas
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
  'Cuantos equipos hay en deposito?',
  'Que equipos estan instalados hace mas de un ano?',
  'Cuantas reparaciones abiertas hay?',
  'Que modelos se estan rompiendo mas?',
];

export function vistaConsultas({ permisos = [] } = {}) {
  const raiz = crear('div');

  // --- Estado de la IA ------------------------------------------------------
  const tarjetaEstado = crear('div', { clase: 'tarjeta' });
  raiz.append(tarjetaEstado);
  pintarEstadoIA(tarjetaEstado);

  // --- Preguntas ------------------------------------------------------------
  const tarjetaChat = crear('div', { clase: 'tarjeta' });

  const caja = crear('div', {
    clase: 'apilado',
    estilo:
      'max-height:26rem;overflow-y:auto;padding:0.85rem;background:var(--superficie-2);' +
      'border-radius:var(--radio-sm);margin-bottom:1rem;white-space:pre-wrap;',
  });
  caja.setAttribute('aria-live', 'polite');
  caja.setAttribute('role', 'log');

  const entrada = crear('textarea', {
    clase: 'campo__control',
    atributos: {
      rows: 2,
      placeholder: 'Preguntale al inventario…',
      // `maxlength`: el backend tambien limita el largo, pero acortarlo aca
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

  const filaEntrada = crear('div', { clase: 'fila-botones' });
  filaEntrada.append(entrada, botonEnviar);

  formulario.append(filaEntrada);

  // Sugerencias. Saves clicks pero son la mitad del espacio de la pantalla en
  // un celular, asi que se muestran solo si hay lugar.
  const sugerencias = crear('div', { clase: 'fila-botones', estilo: 'margin-top:0.75rem' });
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
    sugerencias,
  );

  raiz.append(tarjetaChat);

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
    raiz.append(tarjetaReporte);
  }

  async function enviar() {
    const pregunta = entrada.value.trim();
    if (!pregunta) return;

    agregarMensaje('Tú', pregunta, 'info');
    entrada.value = '';
    botonEnviar.disabled = true;

    const pensando = crear('p', { clase: 'tenue pequeno', texto: 'Consultando…' });
    caja.append(pensando);

    try {
      const respuesta = await api.preguntar(pregunta);
      pensando.remove();

      if (respuesta.error) {
        agregarMensaje('Sistema', respuesta.error, 'alerta');
      } else {
        agregarMensaje('Inventario', respuesta.respuesta || 'Sin respuesta.', 'exito');
      }

      // El SQL se muestra solo si el backend lo devolucion. Ver la nota de
      // seguridad: el modelo escribe el SELECT, y verlo ayuda a entender de
      // donde sale el numero.
      if (respuesta.sql) {
        const detalle = crear('details', { estilo: 'margin-top:0.5rem' });
        const resumen = crear('summary', { clase: 'pequeno tenue', texto: 'Ver la consulta' });
        const codigo = crear('pre', {
          clase: 'mono pequeno',
          estilo:
            'overflow-x:auto;background:var(--superficie);padding:0.6rem;' +
            'border-radius:var(--radio-sm);margin-top:0.5rem;',
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
   * Un mensaje en la caja de conversacion.
   *
   * El texto va SIEMPRE por `textContent`. No hay rama de este archivo que use
   * `innerHTML`, y esa es la garantia de que la salida del modelo no puede
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

    // Se baja al final para que el ultimo mensaje se vea sin tener que scrollear.
    caja.scrollTop = caja.scrollHeight;
  }

  if (!caja.childElementCount) {
    agregarMensaje(
      'Inventario',
      'Preguntame por el inventario. Puedo contar equipos por estado, listar ' +
        'los que llevan mucho instalados y buscar los que mas visitas al taller.',
      'info',
    );
  }

  return raiz;
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
