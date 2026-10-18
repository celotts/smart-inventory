/**
 * Avisos flotantes.
 *
 * Salen abajo, se van solos y se anuncian a un lector de pantalla.
 *
 * `aria-live="polite"` y no `assertive`: un aviso es informacion, no una
 * emergencia. Con `assertive` el lector de pantalla interrumpe lo que esta
 * leyendo, y en un flujo de escaneo eso pasa cada dos segundos.
 *
 * NADA DE TEXTO POR `innerHTML`: el mensaje puede venir de la API, que a su vez
 * lo arma de datos de la base.
 */

const contenedor = () => document.getElementById('avisos');

/**
 * Muestra un aviso.
 *
 * @param {string} mensaje
 * @param {'info'|'exito'|'alerta'|'error'} tipo
 * @param {number} [duracion]  Milisegundos. 0 = no se va solo.
 */
export function avisar(mensaje, tipo = 'info', duracion = 4000) {
  const destino = contenedor();
  if (!destino) return;

  const clase = {
    exito: 'aviso--exito',
    error: 'aviso--error',
    alerta: 'aviso--alerta',
  }[tipo];

  const nodo = document.createElement('div');
  nodo.className = `aviso ${clase ?? ''}`;

  // `textContent`: el mensaje es texto, siempre. Un error de la API puede
  // contener el `detail` de Postgres, que es dato de la base.
  const cuerpo = document.createElement('span');
  cuerpo.textContent = String(mensaje ?? '');
  nodo.append(cuerpo);

  destino.append(nodo);

  if (duracion > 0) {
    setTimeout(() => {
      // Se quita despues de la transicion, y sin `onend`: si la transicion no
      // corre (pestana en background, `prefers-reduced-motion`), el elemento
      // se queda pegado para siempre.
      nodo.style.opacity = '0';
      setTimeout(() => nodo.remove(), 200);
    }, duracion);
  }

  return nodo;
}

/**
 * Aviso persistente de escaneos sin enviar.
 *
 * Se muestra SOLO cuando hay algo en la cola. Un aviso permanente que dice
 * "todo bien" entrena al operador a no mirarlo, y el dia que importa no lo ve.
 */
export function marcarPendientes(cantidad) {
  const nodo = document.getElementById('aviso-offline');
  if (!nodo) return;

  if (!cantidad) {
    nodo.hidden = true;
    nodo.textContent = '';
    return;
  }

  nodo.textContent =
    cantidad === 1
      ? '1 escaneo sin enviar'
      : `${cantidad} escaneos sin enviar`;
  nodo.hidden = false;
}
