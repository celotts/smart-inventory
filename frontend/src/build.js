/**
 * El sello de build y la deteccion de una version nueva.
 *
 * QUE ESTE Y POR QUE ES UN ARCHIVO APARTE
 * ---------------------------------------
 * Son dos cosas que NO son del arranque: una es una consulta al servidor, y
 * la otra decide si hay que recargar la pagina. Estaban en `main.js` junto al
 * service worker, y con el pie y la campana tambien, ese archivo era el
 * lugar donde todo lo que no es una vista terminaba.
 *
 * EL MECANISMO
 * ------------
 * El servidor expone `/build.txt` con el hash de todo el front (lo calcula
 * el Dockerfile al construir la imagen). La app guarda ese hash en
 * `localStorage` la ultima vez que cargo. Si el del servidor es distinto, este
 * navegador esta corriendo codigo viejo: se recarga.
 *
 * POR QUE NO HAY BUCLE
 * --------------------
 * Porque el hash nuevo se guarda ANTES de recargar. En la recarga siguiente
 * los dos coinciden y no pasa nada. Ademas se marca una bandera en
 * `sessionStorage`: si la recarga no alcanza (el service worker todavia
 * manda), no se vuelve a intentar en esta pestana.
 *
 * POR QUE NO SE ESCRIBE NADA EN EL SERVIDOR
 * ------------------------------------------
 * Es una comparacion de una linea. No necesita un endpoint que devuelva la
 * version de la API: alcanza con el hash de los archivos que se sirven.
 */

let selloDelServidor = null;

/**
 * El hash de build que dice el servidor.
 *
 * Se pide UNA vez y se guarda en memoria: el pie lo muestra y la verificacion
 * lo compara, y pedirlo dos veces son dos esperas de red por carga de pagina.
 *
 * @returns {Promise<string|null>}
 */
export async function obtenerSelloDelServidor() {
  if (selloDelServidor) return selloDelServidor;

  try {
    const respuesta = await fetch('/build.txt', { cache: 'no-store' });
    if (!respuesta.ok) return null;
    const texto = (await respuesta.text()).trim();
    if (texto) selloDelServidor = texto;
  } catch {
    // Sin red: el pie se queda con el sello manual.
  }

  return selloDelServidor;
}

/**
 * Verifica si el servidor tiene una version mas nueva que la del navegador.
 */
export async function verificarVersionDelServidor() {
  const CLAVE = 'si-build';
  const BANDERA = 'si-build-reintentado';

  try {
    const respuesta = await fetch('/build.txt', { cache: 'no-store' });
    if (!respuesta.ok) return;

    const sello = (await respuesta.text()).trim();
    if (!sello) return;

    // Se guarda en memoria: el pie lo muestra y no hace falta volver a pedirlo.
    selloDelServidor = sello;

    const guardado = localStorage.getItem(CLAVE);

    // Primera visita: no hay con que comparar. Se guarda y se sigue.
    if (!guardado) {
      localStorage.setItem(CLAVE, sello);
      return;
    }

    if (guardado === sello) return;

    // Hay version nueva. Si ya se intento recargar en esta pestana, no se
    // insiste: significaria que el service worker sigue sirviendo el bundle
    // viejo y recargar no lo arregla (hace falta una recarga forzada).
    if (sessionStorage.getItem(BANDERA)) return;
    sessionStorage.setItem(BANDERA, '1');
    localStorage.setItem(CLAVE, sello);

    window.location.reload();
  } catch {
    // Sin red, o el archivo no existe (desarrollo). No hay nada que hacer.
  }
}
