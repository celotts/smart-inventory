/**
 * La paginación, como componente compartido.
 *
 * POR QUE UN COMPONENTE Y NO UNA COPIA POR PANTALLA
 * -------------------------------------------------
 * La paginación se Was copiada a mano en el inventario y en la auditoría, y
 * las dos hacian cosas distintas: una permitia cambiar la cantidad por
 * página y la otra no; una mostraba el total y la otra no. Con 5000
 * registros, la diferencia no es estetica: en una pantalla el operador cree
 * que vio todo cuando vio 50 de 5000.
 *
 * Acá hay UNA sola implementacion, y todas las pantallas que muestran una
 * tabla la usan: los mismos botones, el mismo texto y el mismo
 * comportamiento.
 *
 * QUE MUESTRA
 * -----------
 *   [‹ Anterior]  Página 3 de 15 · 248 registros   [25 | 50 | 100 por página]  [Siguiente ›]
 *
 * El TOTAL va siempre: sin el, no hay forma de saber si la lista está
 * completa o si hay 4000 registros más allá de la última página.
 */

import { crear } from './dom.js';
import { conTooltip } from './tooltip.js';

// Las cantidades por página que se ofrecen. Ni tan pocas que haya que
// scrollear, ni tantas que la pantalla se vuelva un mapa.
export const TAMANOS_PAGINA = [25, 50, 100];

/**
 * Crea la barra de paginación.
 *
 * @param {object} opciones
 * @param {number} opciones.total       Cuantos registros hay en total.
 * @param {number} [opciones.límite]    Cuantos se muestran por página.
 * @param {number} [opciones.página]    La página actual (base 0).
 * @param {Function} opciones.alCambiarPagina  Se llama con la página nueva.
 * @param {Function} [opciones.alCambiarLimite] Se llama con el límite nuevo.
 * @param {string} [opciones.etiqueta]  Que se cuenta ("registros", "casos"...).
 * @returns {object}  `{ elemento, actualizar }`.
 */
export function crearPaginacion({
  total = 0,
  limite = 50,
  pagina = 0,
  alCambiarPagina,
  alCambiarLimite,
  etiqueta = 'registros',
}) {
  const zona = crear('div', { clase: 'paginacion' });

  const btnAnterior = crear('button', {
    clase: 'boton boton--suave boton--chico',
    texto: '‹ Anterior',
    atributos: { type: 'button' },
  });
  conTooltip(btnAnterior, 'Vuelve a la página anterior.');

  const btnSiguiente = crear('button', {
    clase: 'boton boton--suave boton--chico',
    texto: 'Siguiente ›',
    atributos: { type: 'button' },
  });
  conTooltip(btnSiguiente, 'Avanza a la página siguiente.');

  const info = crear('span', { clase: 'paginacion__info' });

  // LA CANTIDAD POR Página
  //
  // Es parte de la paginación y no un detalle: con 5000 registros, ver 50
  // por página son 100 pantallas de scrollear, y ver 500 son 10. El que
  // conoce el trabajo sabe cual necesita.
  const selectorCantidad = crear('select', { clase: 'paginacion__cantidad' });
  for (const tamano of TAMANOS_PAGINA) {
    selectorCantidad.append(
      crear('option', { texto: String(tamano), atributos: { value: String(tamano) } }),
    );
  }
  selectorCantidad.addEventListener('change', () => {
    alCambiarLimite?.(Number(selectorCantidad.value));
  });

  const zonaCantidad = crear('label', { clase: 'paginacion__grupo' });
  zonaCantidad.append(
    crear('span', { clase: 'paginacion__etiqueta', texto: 'por página' }),
    selectorCantidad,
  );

  btnAnterior.addEventListener('click', () => alCambiarPagina?.(pagina - 1));
  btnSiguiente.addEventListener('click', () => alCambiarPagina?.(pagina + 1));

  zona.append(btnAnterior, info, zonaCantidad, btnSiguiente);

  /** Vuelve a pintar la barra con los números nuevos. */
  function actualizar({ total: nuevoTotal, limite: nuevoLimite, pagina: nuevaPagina }) {
    if (nuevoTotal !== undefined) total = nuevoTotal;
    if (nuevoLimite !== undefined) limite = nuevoLimite;
    if (nuevaPagina !== undefined) pagina = nuevaPagina;

    const paginas = limite > 0 ? Math.max(1, Math.ceil(total / limite)) : 1;
    if (pagina > paginas - 1) pagina = Math.max(0, paginas - 1);
    if (pagina < 0) pagina = 0;

    const desde = total === 0 ? 0 : pagina * limite + 1;
    const hasta = Math.min(total, (pagina + 1) * limite);

    info.textContent =
      total === 0
        ? `Sin ${etiqueta}`
        : `Página ${pagina + 1} de ${paginas} · ${desde}-${hasta} de ${total} ${etiqueta}`;

    btnAnterior.disabled = pagina <= 0;
    btnSiguiente.disabled = pagina >= paginas - 1;
    btnAnterior.setAttribute('aria-disabled', btnAnterior.disabled ? 'true' : 'false');
    btnSiguiente.setAttribute('aria-disabled', btnSiguiente.disabled ? 'true' : 'false');

    if (selectorCantidad.value !== String(limite)) {
      selectorCantidad.value = String(limite);
    }

    zona.hidden = total === 0 && pagina === 0;
  }

  actualizar({});

  return { elemento: zona, actualizar };
}
