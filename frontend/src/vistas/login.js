/**
 * Vista de inicio de sesión.
 *
 * NADA DE Está VISTA USA `innerHTML`. El email y el password van por
 * `textContent` y `value`, y el mensaje de error se arma con `crear()`.
 *
 * POR QUE NO HAY MEDIDOR DE FORTALEZA Acá
 * ---------------------------------------
 * El medidor de fortaleza de clave tiene sentido cuando se CREA una clave: el
 * operador elige una y hay que decirle si es debil. En el INGRESO la clave ya
 * existe, su fortaleza no es decisión del operador, y mostrarla confunde:
 * "tu clave es debil" le dice al operador que tiene que cambiarla, cuando lo
 * único que está haciendo es entrar.
 *
 * El error de un ingreso fallido va en el formulario y no en un campo: el
 * problema no es el email o la clave, es la COMBINACION. Marcaria de más un
 * campo que está bien.
 */

import { api } from '../api.js';
import { crear, icono } from '../dom.js';
import { campoTexto } from '../formulario.js';

export function vistaLogin({ alEntrar }) {
  const contenedor = crear('div', { clase: 'login' });

  const marca = crear('div', { clase: 'login__marca' });
  const logo = crear('div', { clase: 'login__logo' });
  logo.append(icono('M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10', { tamano: 28 }));

  marca.append(
    logo,
    crear('h1', { clase: 'login__titulo', texto: 'Gestor de Activos' }),
    crear('p', { clase: 'tenue pequeno', texto: 'Activos, inventario e incidencias' }),
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

  const email = campoTexto({
    etiqueta: 'Email',
    placeholder: 'operador@empresa.com',
    tipo: 'email',
    autocomplete: 'username',
    inputmode: 'email',
    requerido: true,
  });

  const clave = campoTexto({
    etiqueta: 'Contraseña',
    placeholder: '••••••••',
    tipo: 'password',
    autocomplete: 'current-password',
    requerido: true,
  });

  // El error general del formulario (credenciales incorrectas, cuenta
  // bloqueada, servidor caido). Va con `role="alert"` para que el lector de
  // pantalla lo anuncie.
  const error = crear('p', { clase: 'campo__error', atributos: { role: 'alert' } });
  error.hidden = true;

  const boton = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Entrar',
    atributos: { type: 'submit' },
  });

  formulario.append(email.campo, clave.campo, error, boton);

  tarjeta.append(
    crear('h2', { clase: 'tarjeta__titulo', texto: 'Iniciar sesión' }),
    crear('p', { clase: 'tarjeta__subtitulo', texto: 'Accede con tu cuenta de la empresa.' }),
    formulario,
  );

  contenedor.append(marca, tarjeta);

  // El foco va al email apenas aparece: en un celular eso abre el teclado solo,
  // que es lo que se quiere en una pantalla que es basicamente un formulario.
  queueMicrotask(() => email.control.focus());

  async function enviar() {
    const correo = email.control.value.trim();
    const palabra = clave.control.value;

    email.limpiar();
    clave.limpiar();
    error.hidden = true;

    if (!correo) {
      email.marcar('Escribi tu email.');
      email.control.focus();
      return;
    }
    if (!palabra) {
      clave.marcar('Escribi tu contraseña.');
      clave.control.focus();
      return;
    }

    boton.disabled = true;
    boton.textContent = 'Entrando…';

    try {
      const datos = await api.login(correo, palabra);
      alEntrar(datos);
    } catch (excepcion) {
      error.textContent = excepcion.message || 'No se pudo iniciar sesión.';
      error.hidden = false;
      boton.disabled = false;
      boton.textContent = 'Entrar';
      // Se borra la clave: el operador la va a escribir de nuevo, y dejarla
      // en el campo es una señal de que la app no lo registro.
      clave.control.value = '';
      clave.control.focus();
    }
  }

  // Sin limpieza: no hay timers, streams ni listeners fuera del propio DOM,
  // que el garbage collector se lleva solo. Se devuelve igual con el mismo
  // contrato que las otras vistas.
  return { elemento: contenedor };
}
