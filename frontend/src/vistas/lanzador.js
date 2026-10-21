/**
 * La pantalla de entrada: elegir módulo.
 *
 * QUE ES
 * ------
 * La pantalla que aparece Después de iniciar sesión, antes de entrar a la app.
 * Es la "pantalla previa" que separa las dos soluciones: el operador entra a
 * Inventario, el técnico a Service Desk, y el admin elige.
 *
 * POR QUE NO ES UN Menú DENTRO DE LA APP
 * ---------------------------------------
 * Un menú "Cambiar módulo" arriba de todo deja la duda de siempre: si el
 * operador tiene Inventario abierto y el admin Service Desk, el que está
 * mal es el que está mirando el otro. Elegir UNA vez, al entrar, y que la app
 * sea una sola mientras se trabaja, es lo que hace que las pestañas, el
 * tablero y los permisos de la pantalla sean coherentes entre si.
 *
 * SE ACUERDA
 * -----------
 * La eleccion se guarda en el navegador: el operador que siempre trabaja en
 * el depósito entra directo a Inventario, sin un clic de más. Y en la
 * cabecera hay un selector para cambiar, así que la decisión no es definitiva.
 */

import { crear, icono } from '../dom.js';
import { MODULOS, modulosDe } from '../modulos.js';
import { conTooltip } from '../tooltip.js';

const CLAVE_MODULO = 'si-modulo';

/**
 * El botón de un módulo: el ícono, el nombre y que hace.
 */
function botonDeModulo(modulo, alElegir) {
  const boton = crear('button', {
    clase: 'lanzador__opcion',
    // `data-módulo` es lo que le da el color a la tarjeta. Es el mismo
    // atributo que usa la cabecera, así que el color es el mismo en los dos
    // lugares: el módulo se reconoce de un vistazo, en la pantalla de entrada
    // y en la cabecera.
    atributos: { type: 'button', 'data-modulo': modulo.clave },
  });

  boton.replaceChildren(
    crear('span', { clase: 'lanzador__icono', hijos: [icono(modulo.icono, { tamano: 24 })] }),
    crear('span', {
      clase: 'lanzador__texto',
      hijos: [
        crear('strong', { texto: modulo.titulo }),
        crear('span', { clase: 'lanzador__detalle', texto: modulo.detalle }),
      ],
    }),
  );

  boton.addEventListener('click', alElegir);
  return boton;
}

/**
 * Muestra la pantalla de entrada.
 *
 * @param {object} opciones
 * @param {object} opciones.usuario     El usuario conectado.
 * @param {Function} opciones.alEntrar   Recibe la clave del módulo elegido.
 * @returns {HTMLElement}
 */
export function pantallaDeEntrada({ usuario, alEntrar }) {
  const raiz = crear('div', { clase: 'lanzador' });

  const tarjeta = crear('div', { clase: 'lanzador__tarjeta' });

  const marca = crear('div', { clase: 'lanzador__marca' });
  marca.append(
    crear('h1', { clase: 'lanzador__titulo', texto: 'Smart Inventory' }),
    crear('p', {
      clase: 'lanzador__subtitulo',
      texto: `${usuario?.full_name ?? ''} · ${usuario?.role_name ?? ''}`.trim(),
    }),
  );

  const disponibles = modulosDe(usuario);

  const zona = crear('div', { clase: 'lanzador__opciones' });

  if (disponibles.length === 1) {
    // Con un solo módulo no hay nada que elegir: se entra directo, sin una
    // pantalla de por medio que no le aporta nada al operador.
    zona.appendChild(botonDeModulo(MODULOS[disponibles[0]], () => alEntrar(disponibles[0])));
  } else {
    for (const clave of disponibles) {
      const modulo = MODULOS[clave];
      const boton = botonDeModulo(modulo, () => alEntrar(clave));
      conTooltip(boton, modulo.descripcion);
      zona.appendChild(boton);
    }
  }

  tarjeta.append(marca, zona);

  if (disponibles.length > 1) {
    tarjeta.append(
      crear('p', {
        clase: 'lanzador__nota',
        texto: 'Elegí con que módulo vas a trabajar. Podés cambiarlo desde la cabecera.',
      }),
    );
  }

  raiz.appendChild(tarjeta);
  return raiz;
}

/**
 * El módulo guardado en el navegador, si el usuario puede usarlo.
 *
 * @param {object} usuario
 * @returns {string|null}
 */
export function moduloGuardado(usuario) {
  let guardado = null;
  try {
    guardado = localStorage.getItem(CLAVE_MODULO);
  } catch {
    return null;
  }
  if (!guardado) return null;
  return modulosDe(usuario).includes(guardado) ? guardado : null;
}

/**
 * Guarda el módulo elegido.
 */
export function guardarModulo(clave) {
  try {
    localStorage.setItem(CLAVE_MODULO, clave);
  } catch {
    // Sin storage: se elige en cada arranque. No es un problema.
  }
}
