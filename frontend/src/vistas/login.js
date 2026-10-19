/**
 * Vista de inicio de sesion.
 *
 * NADA DE ESTA VISTA USA `innerHTML`. El email y el password van por
 * `textContent` y `value`, y el mensaje de error se arma con `crear()`.
 */

import { api } from '../api.js';
import { crear, icono } from '../dom.js';
import { PasswordStrengthMeter } from '../composables/usePasswordStrength.js';

export function vistaLogin({ alEntrar }) {
  const contenedor = crear('div', { clase: 'login' });

  const marca = crear('div', { clase: 'login__marca' });
  const logo = crear('div', { clase: 'login__logo' });
  logo.append(icono('M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10', { tamano: 28 }));

  marca.append(
    logo,
    crear('h1', { clase: 'login__titulo', texto: 'Smart Inventory' }),
    crear('p', { clase: 'tenue pequeno', texto: 'Inventario de equipos' }),
  );

  const tarjeta = crear('div', { clase: 'tarjeta' });

  const formulario = crear('form', {
    atributos: {
      novalidate: true,
      onsubmit: (evento) => {
        evento.preventDefault();
        enviar();
      },
    },
  });

  const email = crear('input', {
    clase: 'campo__control',
    texto: undefined,
    atributos: {
      type: 'email',
      name: 'email',
      id: 'email',
      autocomplete: 'username',
      required: true,
      autocapitalize: 'none',
      autocorrect: 'off',
      spellcheck: 'false',
      inputmode: 'email',
      placeholder: 'operador@empresa.com',
    },
  });

  const clave = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'password',
      name: 'password',
      id: 'clave',
      autocomplete: 'current-password',
      required: true,
      placeholder: '••••••••',
    },
  });

  // Medidor de fortaleza de contraseña
  const medidorClave = PasswordStrengthMeter({ mostrarRequisitos: true });
  clave.addEventListener('input', () => medidorClave.actualizar(clave.value));

  const error = crear('p', { clase: 'campo__error', atributos: { role: 'alert' } });
  error.hidden = true;

  const boton = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Entrar',
    atributos: { type: 'submit' },
  });

  const envolver = (etiqueta, control, ayuda) => {
    const label = crear('label', { clase: 'campo' });
    const texto = crear('span', { clase: 'campo__etiqueta', texto: etiqueta });
    label.htmlFor = control.id;
    label.append(texto, control);
    if (ayuda) label.append(crear('p', { clase: 'campo__ayuda', texto: ayuda }));
    return label;
  };

  formulario.append(
    envolver('Email', email),
    envolver('Contraseña', clave),
    medidorClave.elemento,  // Medidor de fortaleza
    error,
    boton,
  );

  tarjeta.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Iniciar sesion' }),
    crear('p', { clase: 'tarjeta__subtitulo', texto: 'Accede con tu cuenta de la empresa.' }),
    formulario,
  );

  contenedor.append(marca, tarjeta);

  // El foco va al email apenas aparece: en un celular eso abre el teclado solo,
  // que es lo que se quiere en una pantalla que es basicamente un formulario.
  queueMicrotask(() => email.focus());

  async function enviar() {
    const correo = email.value.trim();
    const palabra = clave.value;

    if (!correo || !palabra) {
      mostrarError('Completa el email y la contraseña.');
      return;
    }

    error.hidden = true;
    boton.disabled = true;
    boton.textContent = 'Entrando…';

    try {
      const datos = await api.login(correo, palabra);
      alEntrar(datos);
    } catch (excepcion) {
      mostrarError(excepcion.message || 'No se pudo iniciar sesion.');
      boton.disabled = false;
      boton.textContent = 'Entrar';
      clave.value = '';
      clave.focus();
      // Resetear medidor
      medidorClave.reiniciar();
    }
  }

  function mostrarError(mensaje) {
    error.textContent = mensaje;
    error.hidden = false;
  }

  // Sin limpieza: no hay timers, streams ni listeners fuera del propio DOM,
  // que el garbage collector se lleva solo. Se devuelve igual con el mismo
  // contrato que las otras vistas.
  return { elemento: contenedor };
}
