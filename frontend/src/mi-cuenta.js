/**
 * "Mi cuenta": cambiar la propia contraseña.
 *
 * POR QUE ES PARTE DE LA APP Y NO UN PROCEDIMIENTO
 * ------------------------------------------------
 * El usuario con la sesión abierta es la persona que mejor sabe si su clave
 * está comprometida. Pedirle que llame al soporte para cambiarla es la forma
 * de que las claves舊 no cambien nunca.
 *
 * PIDE LA Contraseña ACTUAL
 * -------------------------
 * Sin eso, cualquiera con un token robado se cambia la clave y echa al
 * dueño de su propia cuenta. El backend también lo exige, y revoca las
 * demas sesiones: si el cambió se hizo porque se sospecho de una intrusion,
 * dejarlas vivas es justo lo que no hay que hacer.
 */

import { api } from './api.js';
import { crear } from './dom.js';
import { avisar } from './avisos.js';
import { abrirDialogo } from './dialogo.js';
import { campoTexto, resumenDeErrores } from './formulario.js';
import { PasswordStrengthMeter } from './composables/usePasswordStrength.js';

/**
 * Abre el modal de "cambiar mi contraseña".
 */
export function abrirMiCuenta(usuario) {
  const campoActual = campoTexto({
    etiqueta: 'Contraseña actual *',
    tipo: 'password',
    autocomplete: 'current-password',
    descripcion: 'La que usas hoy. Se pide para confirmar que sos vos.',
  });

  const campoNueva = campoTexto({
    etiqueta: 'Contraseña nueva *',
    tipo: 'password',
    autocomplete: 'new-password',
    descripcion: 'Mínimo 8 caracteres, con mayúscula, minúscula y número.',
  });

  const campoRepetir = campoTexto({
    etiqueta: 'Repetir la nueva *',
    tipo: 'password',
    autocomplete: 'new-password',
    descripcion: 'Las dos tienen que coincidir.',
  });

  const medidor = PasswordStrengthMeter({ mostrarRequisitos: true });
  campoNueva.control.addEventListener('input', () => medidor.actualizar(campoNueva.control.value));

  const zonaResumen = crear('div');

  const form = crear('form', {
    clase: 'apilado',
    atributos: {
      onsubmit: async (e) => {
        e.preventDefault();
        await guardar();
      },
    },
  });

  form.append(zonaResumen, campoActual.campo, campoNueva.campo, campoRepetir.campo, medidor.elemento);

  const btnGuardar = crear('button', {
    clase: 'botón botón--bloque',
    texto: 'Cambiar contraseña',
    atributos: { type: 'submit' },
  });
  form.append(btnGuardar);

  let cerrarModal = () => {};

  async function guardar() {
    const actual = campoActual.control.value;
    const nueva = campoNueva.control.value;
    const repetida = campoRepetir.control.value;

    [campoActual, campoNueva, campoRepetir].forEach((c) => c.limpiar());
    zonaResumen.replaceChildren();

    if (!actual) campoActual.marcar('Escribi tu contraseña actual.');
    if (nueva.length < 8) campoNueva.marcar('La nueva necesita al menos 8 caracteres.');
    if (nueva !== repetida) campoRepetir.marcar('Las dos contrasenas no coinciden.');

    const resumen = resumenDeErrores([campoActual, campoNueva, campoRepetir]);
    if (resumen) {
      zonaResumen.replaceChildren(resumen);
      return;
    }

    btnGuardar.disabled = true;
    btnGuardar.textContent = 'Cambiando…';
    try {
      await api.cambiarMiPassword({ password_actual: actual, password_nueva: nueva });
      avisar('Contraseña actualizada. Vuelve a entrar con la nueva.', 'exito', 4000);
      cerrarModal(true);
      // Las demas sesiones se cerraron: la de está también. La app lo detecta
      // en el próximo pedido y vuelve al login.
      setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
      avisar(error.message, 'error');
      btnGuardar.disabled = false;
      btnGuardar.textContent = 'Cambiar contraseña';
    }
  }

  // El botón de CERRAR: sin el, el modal solo se cierra con Escape, y en
  // un celular no hay Escape.
  const btnCancelar = crear('button', {
    clase: 'botón botón--suave',
    texto: 'Cancelar',
    atributos: { type: 'button' },
  });

  cerrarModal = abrirDialogo({
    titulo: 'Mi cuenta',
    nota: `Sesion de ${usuario?.email ?? 'este usuario'}. Al cambiar la contrasena se cierran las demas sesiones abiertas.`,
    cuerpo: [form],
    clase: 'modal--formulario',
    acciones: [btnCancelar],
    avanzarAlCerrar: true,
    alAbrir: (_modal, cerrar) => {
      cerrarModal = cerrar;
      btnCancelar.addEventListener('click', cerrar);
    },
  });
}
