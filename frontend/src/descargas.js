/**
 * Descarga de archivos del backend.
 *
 * POR QUE NO UN `<a download="inventario.csv" href="...">`
 * ----------------------------------------------------
 * La API exige el token en el header `Authorization`, y una etiqueta no puede
 * mandarlo: el navegador bajaria el archivo con un 401 y el operador se
 * llevaria un JSON de error en vez del CSV. Por eso el archivo se pide con
 * `api.pedir` (que si manda el token) y se arma el Blob en memoria.
 *
 * POR QUE EL CSV SE PIDE COMO TEXTO Y NO COMO BLOB
 * ------------------------------------------------
 * `api.pedir` con `comoTexto` devuelve el cuerpo crudo. Con el `Blob` se
 * mantiene el BOM UTF-8 que pone el backend, que es lo que hace que Excel
 * abra los acentos bien.
 */

import { api } from './api.js';
import { avisar } from './avisos.js';

/**
 * Descarga un CSV del backend y lo guarda en el dispositivo.
 *
 * @param {string} ruta   Ruta del CSV. Ej: '/export/inventario.csv'
 * @param {string} nombre Nombre del archivo que se descarga.
 */
export async function descargarCsv(ruta, nombre, contenido) {
  try {
    // Si el contenido viene dado, es un CSV armado en el navegador (el recibo
    // de escaneos, por ejemplo). Si no, se pide a la API.
    const texto = contenido ?? (await api.pedir(ruta, { comoTexto: true }));

    // El BOM UTF-8 lo pone el backend: sin el, Excel abre los acentos rotos.
    const blob = new Blob([texto], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    const enlace = document.createElement('a');
    enlace.href = url;
    enlace.download = nombre;
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();

    // El objeto URL queda en memoria hasta que se revoca. Sin esto, cada
    // descarga deja un Blob vivo en el navegador.
    setTimeout(() => URL.revokeObjectURL(url), 1000);

    avisar(`${nombre} descargado`, 'exito', 2500);
  } catch (error) {
    avisar(`No se pudo descargar: ${error.message}`, 'error');
  }
}
