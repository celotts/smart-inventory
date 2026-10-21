/**
 * Medidor de fortaleza de contraseña.
 *
 * Es un componente de DOM plano, no de Vue: el proyecto construye las vistas
 * con `crear()` de `dom.js`. Por eso `strength` es un objeto plano y no un
 * `ref`, y `actualizar()` repinta el elemento en vez de pedir un re-render.
 *
 * Uso:
 *     const medidor = PasswordStrengthMeter({ mostrarRequisitos: true });
 *     clave.addEventListener('input', () => medidor.actualizar(clave.value));
 *     contenedor.append(medidor.elemento);
 */

import { crear } from '../dom.js';
import { api } from '../api.js';

const VACIO = { entropia: 0, nivel: 'débil', requisitos: [], es_valida: false };

/** El color sale de las mismas variables de las clases del CSS. */
function colorDe(nivel) {
  switch (nivel) {
    case 'fuerte': return 'var(--success)';
    case 'media': return 'var(--warning)';
    default: return 'var(--error)';
  }
}

function etiquetaDe(nivel) {
  switch (nivel) {
    case 'fuerte': return 'Fuerte';
    case 'media': return 'Media';
    default: return 'Débil';
  }
}

export function PasswordStrengthMeter({ modelValue = '', mostrarRequisitos = true } = {}) {
  const elemento = crear('div', { clase: 'password-strength-meter' });

  const barraContenedor = crear('div', { clase: 'strength-bar-container' });
  const barra = crear('div', { clase: 'strength-bar' });
  barraContenedor.append(barra);

  const etiqueta = crear('div', { clase: 'strength-label' });
  const lista = crear('ul', { clase: 'strength-requisitos' });

  elemento.append(barraContenedor, etiqueta);
  if (mostrarRequisitos) elemento.append(lista);

  // Estado publico: `login.js` lo resetea cuando el login falla.
  const medidor = {
    elemento,
    password: modelValue,
    strength: { ...VACIO },

    /** Consulta al servidor con debounce. El backend define los requisitos. */
    actualizar(valor) {
      medidor.password = valor;

      if (medidor.timer) clearTimeout(medidor.timer);
      medidor.timer = setTimeout(() => verificar(valor), 150);
    },

    reiniciar() {
      medidor.password = '';
      medidor.strength = { ...VACIO };
      pintar(medidor.strength);
    },
  };

  async function verificar(valor) {
    if (!valor) {
      medidor.strength = { ...VACIO };
      pintar(medidor.strength);
      return;
    }

    try {
      const respuesta = await api.pedir('/auth/password-strength', {
        params: { password: valor },
      });
      // La respuesta puede llegar tarde si el usuario sigue escribiendo.
      if (medidor.password !== valor) return;
      medidor.strength = respuesta;
      pintar(respuesta);
    } catch (excepcion) {
      // La fortaleza es ayuda, no un control: si falla, se sigue escribiendo.
      console.warn('No se pudo calcular la fortaleza de la contraseña:', excepcion);
    }
  }

  function pintar({ entropia = 0, nivel = 'débil', requisitos = [] }) {
    const color = colorDe(nivel);

    barra.style.width = `${Math.min(100, entropia)}%`;
    barra.style.backgroundColor = color;

    etiqueta.textContent = etiquetaDe(nivel);
    etiqueta.style.color = color;

    lista.replaceChildren();
    for (const requisito of requisitos) {
      lista.append(crear('li', {
        clase: requisito.cumplido ? 'cumplido' : 'pendiente',
        // Los hijos van dentro de las opciones: `crear` no tiene tercer
        // parámetro y los descartaria en silencio, dejando la lista vacía.
        hijos: [
          crear('span', { clase: 'req-icon', texto: requisito.cumplido ? '✓' : '✗' }),
          crear('span', {
            clase: 'req-texto',
            texto: requisito.minimo
              ? `${requisito.nombre} (mín ${requisito.minimo})`
              : requisito.nombre,
          }),
        ],
      }));
    }
  }

  pintar(medidor.strength);

  return medidor;
}