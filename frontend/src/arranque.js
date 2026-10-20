/**
 * Vigilante de arranque.
 *
 * QUE HACE
 * --------
 * El `index.html` trae un esqueleto estatico con "Cargando…". Si el JavaScript
 * arranca, `main.js` lo reemplaza por la pantalla real. Si NO arranca —un
 * bundle roto en la cache, un service worker que sirve un version vieja, un
 * error de red que lo deja a medias— ese esqueleto se queda en pantalla PARA
 * SIEMPRE, girando sola, y el operador no tiene ni idea de que paso.
 *
 * Este modulo se carga antes que `main.js` y hace una sola cosa: si despues
 * de un rato la pantalla principal sigue mostrando el esqueleto, la cambia por
 * un mensaje que dice que paso y ofrece recargar.
 *
 * POR QUE ESTA SEPARADO DE `main.js` Y NO DENTRO
 * -----------------------------------------------
 * Porque si el problema esta en `main.js` —que es justo el caso que este
 * vigilante existe para detectar— un vigilante dentro de `main.js` no llega a
 * ejecutarse nunca. Va en su propio archivo, y el `index.html` lo carga antes.
 *
 * POR QUE NO HAY SCRIPT INLINE
 * ---------------------------
 * La CSP es `script-src 'self'` sin `unsafe-inline`: un `<script>` aca seria
 * bloquearse a si mismo y dejar la pagina sin ningun vigilante, que es
 * exactamente el fallo que se quiere evitar.
 */

const LIMITE_MS = 12000;

/**
 * El error que hizo fallar el arranque, si lo hubo.
 *
 * QUE ESTO RESUELVE
 * -----------------
 * Un `ReferenceError` en un modulo (un nombre mal escrito, una importacion
 * que no existe) ABORTA el arranque de la app entera: no se pinta ninguna
 * pantalla y el operador ve "Cargando…" para siempre. Sin este bloque, el
 * unico rastro estaba en la consola del navegador, que el operador no abre y
 * que ademas se pierde al reiniciar el celular.
 *
 * Con el, la pantalla dice QUE paso. El soporte no necesita el celular del
 * operador para diagnosing: alcanza con que lea el mensaje.
 */
let errorDeArranque = null;

window.addEventListener('error', (evento) => {
  errorDeArranque = evento.message || 'Error desconocido';
  // `preventDefault` evita que el error se muestre dos veces: una por el
  // listener y otra por el cartel del navegador.
  evento.preventDefault?.();
});

window.addEventListener('unhandledrejection', (evento) => {
  const motivo = evento.reason;
  errorDeArranque = motivo?.message || String(motivo || 'Promesa rechazada sin manejar');
  evento.preventDefault?.();
});

setTimeout(() => {
  // Si la app ya pinto una pantalla, no hay nada que hacer.
  //
  // `appLista` lo pone `main.js` cuando reemplaza el esqueleto por la
  // pantalla real. El chequeo del esqueleto solo (que es lo que habia) no
  // alcanzaba: con un arranque LENTO —celular con 4G de segundo plano, o un
  // servidor que tarda en servir los modulos— la app puede tardar mas de 12
  // segundos en pintar, y el vigilante le pisaba la pantalla con "La app no
  // pudo arrancar" cuando la app ya estaba funcionando.
  if (document.body.dataset.appLista === '1') return;

  const esqueleto = document.querySelector('#contenido .cargando');
  if (!esqueleto || esqueleto.offsetParent === null) return;

  const contenedor = document.getElementById('contenido');
  if (!contenedor) return;

  const caja = document.createElement('div');
  caja.className = 'tarjeta';
  caja.style.textAlign = 'center';

  const titulo = document.createElement('h2');
  titulo.className = 'tarjeta__titulo';
  titulo.textContent = 'La app no pudo arrancar';

  const texto = document.createElement('p');
  texto.className = 'tarjeta__subtitulo';
  texto.textContent =
    'Esto casi siempre es la cache del navegador con una version vieja de los archivos. ' +
    'Hacé una recarga forzada: Cmd+Shift+R (Mac) o Ctrl+Shift+R (Windows).';

  // Si hubo un error de JavaScript, se dice cual. Un `ReferenceError` en un
  // modulo aborta el arranque entero, y sin esto el operador solo ve "no
  // arranco": ni el soporte ni el developer saben que paso hasta que abren
  // la consola en un celular que no vuelve a sus manos.
  const detalle = errorDeArranque
    ? (() => {
        const p = document.createElement('p');
        p.className = 'campo__error';
        p.style.textAlign = 'left';
        p.style.whiteSpace = 'pre-wrap';
        p.textContent = `Detalle del error: ${errorDeArranque}`;
        return p;
      })()
    : null;

  const boton = document.createElement('button');
  boton.className = 'boton';
  boton.textContent = 'Recargar ahora';
  boton.addEventListener('click', () => window.location.reload());

  caja.append(titulo, texto);
  if (detalle) caja.appendChild(detalle);
  caja.appendChild(boton);
  contenedor.replaceChildren(caja);
}, LIMITE_MS);
