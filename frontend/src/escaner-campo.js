/**
 * Escanear un código DIRECTAMENTE a un campo de un formulario.
 *
 * POR QUE EXISTE
 * --------------
 * El operador está dando de alta un producto que YA TRAE un código: el del
 * proveedor, impreso en la caja o en la etiqueta del fabricante. Escribirlo a
 * mano es la fuente número uno de errores de inventario: un GTIN de 13
 * dígitos transcrito de memoria tiene una probabilidad alta de salir mal, y
 * un código mal escrito es un código que no existe.
 *
 * LEERLO Y PROBARLO ES MEJOR QUE ESCRIBIRLO
 * -------------------------------------------
 * Un solo dato mal cargado se propaga a todos los equipos de ese producto, a
 * las ordenes de compra y a los reportes. Leerlo con la camara elimina la
 * transición: lo que está en la caja es lo que queda en el sistema.
 *
 * PERO NO SIEMPRE ES "EL Código DEL PRODUCTO"
 * --------------------------------------------
 * Un QR de fabricante puede contener:
 *
 *   - Un GTIN (13 o 14 dígitos): un código de barras de retail.
 *   - Una URL de GS1 Digital Link: `https://<dominio>/gtin/7891234567895`.
 *     El GTIN está adentro de la URL, no es la URL.
 *   - Un serial alfanumerico.
 *   - Una URL cualquiera.
 *
 * Por eso `analizarCodigo` mira QUE se leyo, y el operador ve la propuesta
 * antes de aceptarla. El valor leído NUNCA se guarda sin que el operador lo
 * vea: es una PROPUESTA, y el campo queda editable.
 *
 * LA REGLA QUE NO SE ROMPE
 * ------------------------
 * El QR PROPIO del producto (`PRD-<sku>`) no se pisa con lo leído. Ese
 * código es el protocolo interno de la app: el escaner de la caja lo resuelve
 * contra el maestro. Lo que se captura de la caja es el código DEL
 * PROVEEDOR, y va al campo `barcode`.
 */

import { crear } from './dom.js';
import { LectorQR } from './escaner.js';
import { abrirDialogo } from './dialogo.js';
import { avisar } from './avisos.js';

/**
 * Que se leyo, y que hay que hacer con eso.
 *
 * @param {string} texto  El contenido crudo del código leído.
 * @returns {{valor: string, tipo: string, descripción: string}}
 */
export function analizarCodigo(texto) {
  const crudo = String(texto ?? '').trim();

  if (!crudo) {
    return { valor: '', tipo: 'vacio', descripcion: 'No se leyo ningún código.' };
  }

  // Una URL de GS1 Digital Link: el GTIN va en el path o en el query.
  //   https://id.gs1.org/01/09501101530013/21/ABC   (AI 01 = GTIN)
  //   https://www.miMarca.com/gtin/7891234567895
  //   https://miMarca.com/producto?gtin=7891234567895
  const comoUrl = /^https?:\/\//i.test(crudo) ? new URL(crudo) : null;
  if (comoUrl) {
    const segmentos = comoUrl.pathname.split('/').filter(Boolean);

    // El GTIN explicito en el path: `/gtin/7891234567895`.
    const enPath = segmentos.findIndex((s) => /^(gtin|barcode|ean|upc)$/i.test(s));
    const gtinDePath = enPath >= 0 ? segmentos[enPath + 1] : null;

    // El GTIN de una Digital Link: el path es `/<AI>/<valor>/...`, y el AI
    // (01) es el GTIN. Se busca el primer segmento de 8 a 14 dígitos.
    const gtinDeAi = segmentos.find((s) => /^\d{8,14}$/.test(s));

    const gtinQuery = comoUrl.searchParams.get('gtin') || comoUrl.searchParams.get('barcode');

    const gtin =
      (gtinDePath && /^\d{8,14}$/.test(gtinDePath) ? gtinDePath : null) ||
      gtinDeAi ||
      (gtinQuery && /^\d{8,14}$/.test(gtinQuery) ? gtinQuery : null);

    if (gtin) {
      return {
        valor: gtin,
        tipo: 'gtin',
        descripcion: `Codigo de barras (GTIN) leido de una URL: ${gtin}`,
      };
    }

    return {
      valor: crudo,
      tipo: 'url',
      descripcion: `URL leida: ${crudo}`,
    };
  }

  // Un GTIN pelado: 8, 12, 13 o 14 dígitos.
  if (/^\d{8}$|^\d{12,14}$/.test(crudo)) {
    return {
      valor: crudo,
      tipo: 'gtin',
      descripcion: `Codigo de barras (GTIN): ${crudo}`,
    };
  }

  if (/^[A-Za-z0-9._\-/]+$/.test(crudo)) {
    return {
      valor: crudo,
      tipo: 'serial',
      descripcion: `Codigo alfanumerico: ${crudo}`,
    };
  }

  return {
    valor: crudo,
    tipo: 'texto',
    descripcion: 'Código leído (contiene caracteres no habituales).',
  };
}

/**
 * Abre la camara y devuelve el código leído.
 *
 * @returns {Promise<{valor: string, tipo: string, descripción: string}|null>}
 *          `null` si el operador cancela.
 */
export function leerConCamara() {
  return new Promise((resolver) => {
    const marco = crear('div', { clase: 'escaner-marco' });
    const pista = crear('p', { clase: 'escaner-formatos' });
    const estado = crear('p', { clase: 'campo__ayuda' });

    const cuerpo = crear('div', { clase: 'apilado' });
    cuerpo.append(marco, estado, pista);

    const lector = new LectorQR(marco);
    const razon = LectorQR.razonNoDisponible();

    let resuelto = false;
    let cerrarDialogo = () => {};

    const terminar = (resultado) => {
      if (resuelto) return;
      resuelto = true;
      lector.detener();
      cerrarDialogo();
      resolver(resultado);
    };

    if (razon) {
      // Sin camara no hay escaneo: se dice POR QUE, y el operador escribe el
      // código a mano en el campo, que está a dos pasos.
      estado.textContent = razon;
      estado.classList.add('campo__error');
    } else {
      estado.textContent = 'Apunta el código dentro del marco.';

      lector
        .iniciar((codigo) => {
          const analisis = analizarCodigo(codigo);
          terminar(analisis);
        })
        .catch((error) => {
          estado.textContent = error.message || 'No se pudo abrir la camara.';
          estado.classList.add('campo__error');
        });
    }

    const btnCancelar = crear('button', {
      clase: 'botón botón--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    cerrarDialogo = abrirDialogo({
      titulo: 'Escanear código',
      nota: 'El valor leído se pone en el campo, donde se puede corregir antes de guardar.',
      cuerpo: [cuerpo],
      clase: 'modal--escaner',
      acciones: [btnCancelar],
      alAbrir: (_modal, cerrar) => {
        cerrarDialogo = cerrar;
        btnCancelar.addEventListener('click', () => terminar(null));
        // Escape también cierra: el `alAbrir` de `abrirDialogo` reemplaza el
        // cierre por el de Escape, así que hay que terminarla a mano.
        _modal.addEventListener('keydown', (e) => {
          if (e.key === 'Escape') terminar(null);
        });
      },
    });
  });
}

/**
 * Escanea y llena un campo.
 *
 * @param {object} opciones
 * @param {HTMLInputElement} opciones.campo    El control a llenar.
 * @param {string} [opciones.nombre]           Nombre del campo, para el aviso.
 * @param {Function} [opciones.alUsar]         Se llama con el análisis.
 * @returns {Promise<boolean>} Si se lleno el campo.
 */
export async function escanearEnCampo({ campo, nombre = 'el campo', alUsar }) {
  const analisis = await leerConCamara();
  if (!analisis || !analisis.valor) return false;

  campo.value = analisis.valor;
  // Se selecciona el texto: si el valor leído no es el que esperaba, el
  // operador lo corrige escribiendo encima, sin borrar.
  campo.focus();
  campo.select?.();

  avisar(`Código leido en ${nombre}: ${analisis.valor}`, 'exito', 3000);
  alUsar?.(analisis);
  return true;
}

/**
 * Un botón de escaneo para poner al lado de un campo.
 *
 * @param {object} opciones
 * @param {HTMLInputElement} opciones.campo
 * @param {string} [opciones.nombre]
 * @param {string} [opciones.texto]
 * @returns {HTMLElement}
 */
export function botonEscanear({ campo, nombre = 'el campo', texto = 'Escanear' }) {
  const boton = crear('button', {
    clase: 'botón botón--suave botón--chico',
    texto,
    atributos: {
      type: 'button',
      title: `Leer el codigo con la camara y ponerlo en ${nombre}`,
      'aria-label': `Escanear el codigo de ${nombre}`,
    },
  });

  boton.addEventListener('click', async () => {
    boton.disabled = true;
    try {
      await escanearEnCampo({ campo, nombre });
    } finally {
      boton.disabled = false;
    }
  });

  return boton;
}
