/**
 * Vista "Nuevo componente" — Alta de dispositivo en inventario.
 *
 * Solo visible para ADMIN (permiso devices:edit).
 * Accesible desde la barra de navegacion (pestaña "Nuevo").
 */

import { api } from '../api.js';
import { ICONOS, crear, etiquetaProducto, icono } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { campoTexto, campoSelect, resumenDeErrores } from '../formulario.js';

export function vistaNuevo({ permisos = [] }) {
  const raiz = crear('div');

  // Verificar permiso
  if (!permisos.includes('devices:edit')) {
    raiz.append(
      crear('div', {
        clase: 'tarjeta centrado',
        hijos: [
          icono(ICONOS.error, { tamano: 48 }),
          crear('h2', { clase: 'tarjeta__titulo', texto: 'Acceso denegado' }),
          crear('p', { clase: 'tenue', texto: 'Solo los administradores pueden crear componentes.' }),
        ],
      }),
    );
    return { elemento: raiz };
  }

  // --- Formulario ---
  const form = crear('form', {
    clase: 'apilado',
    estilo: 'max-width: 520px; margin: 0 auto; gap: 1rem',
    atributos: {
      // `novalidate` APAGA la validación nativa del navegador.
      //
      // POR QUE
      // -----
      // Sin esto, el navegador intercepta el submit cuando falta un campo
      // obligatorio y muestra SU burbuja ("Completa este campo"), en el
      // idioma del navegador y sin decir cual es el problema de la app. La
      // validación de está vista —que está escrita en castellano, explica
      // que falta y lleva el foco al campo— nunca llega a correr: el submit
      // muere antes de `enviarFormulario()`.
      //
      // El sintoma para el operador es "no me deja crear componente": el
      // botón no hace nada y no hay ningún mensaje de la app.
      novalidate: true,
      onsubmit: async (e) => {
        e.preventDefault();
        await enviarFormulario();
      },
    },
  });

  // Campos
  //
  // Con `campoTexto` y `campoSelect` el error va DEBAJO del campo, con el
  // control marcado y el foco movido al primer problema. Un aviso flotante
  // que se va solo a los 4 segundos deja al operador con la duda de que
  // campo está mal.
  const campoSerial = campoTexto({
    etiqueta: 'Serial',
    placeholder: 'Ej: SN-12345678',
    ayuda: 'El serial del fabricante. Es único en toda la base.',
    requerido: true,
  });

  const campoQR = campoTexto({
    etiqueta: 'QR propio',
    placeholder: 'Ej: INV-a1b2c3 (se genera si vacío)',
    ayuda: 'Opcional. Si lo dejas vacío, el sistema genera uno propio.',
  });

  // --- Producto, desde el maestro -------------------------------------------
  //
  // ANTES este formulario tenia campos sueltos de marca/modelo/categoría y los
  // mandaba como texto. El backend los RECHAZA: `CrearDispositivoRequest` exige
  // `model_id` (un id del catalogo) y tiene `extra="forbid"`, así que el alta
  // fallaba con 422 siempre. Un equipo no lleva marca y modelo escritos a mano:
  // apunta a un producto del maestro, y de ahí hereda marca, modelo y categoría.
  const campoProducto = campoSelect({
    etiqueta: 'Producto',
    ayuda: 'El equipo hereda marca, modelo y categoría del producto del maestro.',
    requerido: true,
  });
  campoProducto.control.append(
    crear('option', { texto: 'Cargando productos…', atributos: { value: '', disabled: true } }),
  );

  const campoUbicacion = campoSelect({
    etiqueta: 'Ubicación inicial',
    ayuda: 'Donde queda el equipo. Se puede cambiar después.',
    requerido: true,
  });
  campoUbicacion.control.append(
    crear('option', { texto: 'Cargando ubicaciones…', atributos: { value: '', disabled: true } }),
  );

  const btnSubmit = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Crear componente',
    atributos: { type: 'submit' },
  });

  // El resumen de errores va arriba de todo: si hay tres campos con problema,
  // el operador tiene que enterarse antes de llegar al final del formulario.
  const zonaResumen = crear('div');

  form.append(
    crear('h2', { clase: 'tarjeta__titulo', estilo: 'text-align:center; margin-bottom:0.5rem', texto: 'Nuevo componente' }),
    crear('p', { clase: 'tarjeta__subtitulo centrado', texto: 'Completa los datos para dar de alta un equipo en el inventario.' }),
    zonaResumen,
    campoProducto.campo,
    campoSerial.campo,
    campoQR.campo,
    campoUbicacion.campo,
    btnSubmit,
  );

  raiz.append(form);

  // Cargar productos (maestro) y ubicaciones
  async function cargarProductos() {
    try {
      const catalogo = await api.productos();
      const productos = catalogo.items ?? catalogo;
      campoProducto.control.replaceChildren();
      campoProducto.control.append(crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true, selected: true } }));
      if (!productos.length) {
        campoProducto.control.replaceChildren(
          crear('option', { texto: 'No hay productos: crea uno en la pestaña "Productos"', atributos: { value: '', disabled: true, selected: true } }),
        );
        return;
      }
      for (const p of productos) {
        campoProducto.control.append(
          crear('option', { texto: etiquetaProducto(p), atributos: { value: p.id } }),
        );
      }
    } catch (error) {
      campoProducto.control.replaceChildren(crear('option', { texto: 'Error al cargar productos', atributos: { value: '', disabled: true } }));
      console.error(error);
    }
  }

  // Cargar ubicaciones
  async function cargarUbicaciones() {
    try {
      const datos = await api.pedir('/devices/locations');
      campoUbicacion.control.replaceChildren();
      campoUbicacion.control.append(crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true, selected: true } }));
      for (const loc of datos.items) {
        campoUbicacion.control.append(crear('option', { texto: loc.nombre, atributos: { value: loc.id } }));
      }
    } catch (error) {
      campoUbicacion.control.replaceChildren(crear('option', { texto: 'Error al cargar ubicaciones', atributos: { value: '', disabled: true } }));
      console.error(error);
    }
  }
  cargarUbicaciones();
  cargarProductos();

  /**
   * Un error que NO es de un campo concreto (el servidor cayo, la sesión
   * expiro): va arriba del formulario, no flotando.
   */
  function avisoFormulario(mensaje) {
    const caja = crear('p', { clase: 'campo__error', atributos: { role: 'alert' }, texto: mensaje });
    zonaResumen.replaceChildren(caja);
  }

  // Enviar formulario
  async function enviarFormulario() {
    const serial = campoSerial.control.value.trim();
    const qr = campoQR.control.value.trim() || undefined;
    const model_id = campoProducto.control.value ? parseInt(campoProducto.control.value, 10) : null;
    const ubicacion_id = campoUbicacion.control.value ? parseInt(campoUbicacion.control.value, 10) : null;

    // Los errores van DEBAJO del campo, con el control marcado, y el foco
    // salta al primero. Un aviso flotante que se va solo a los 4 segundos
    // deja al operador con la duda de QUE campo está mal.
    campoProducto.limpiar();
    campoSerial.limpiar();
    campoUbicacion.limpiar();
    zonaResumen.replaceChildren();

    if (!model_id) {
      campoProducto.marcar('Selecciona un producto del catalogo.');
    }
    if (!serial) {
      campoSerial.marcar('El serial es obligatorio.');
    }
    if (!ubicacion_id) {
      campoUbicacion.marcar('Selecciona una ubicación inicial.');
    }

    const resumen = resumenDeErrores([campoProducto, campoSerial, campoUbicacion]);
    if (resumen) {
      zonaResumen.replaceChildren(resumen);
      resumen.querySelector('button')?.focus();
      return;
    }

    const ok = await confirmar({
      titulo: 'Crear componente',
      mensaje: `¿Dar de alta el equipo con serial ${serial}? Queda registrado en el inventario desde este momento.`,
      textoConfirmar: 'Crear componente',
    });
    if (!ok) return;

    btnSubmit.disabled = true;
    btnSubmit.textContent = 'Creando…';

    try {
      await api.pedir('/devices', {
        metodo: 'POST',
        cuerpo: { serial_number: serial, model_id, qr_code: qr, location_id: ubicacion_id },
      });
      avisar('Componente creado correctamente', 'exito', 2500);
      // Limpiar formulario
      form.reset();
      campoProducto.control.value = '';
      campoUbicacion.control.value = '';
      cargarUbicaciones();
      cargarProductos();
    } catch (error) {
      // Un 409/422 del backend es un problema de un campo (el serial ya
      // existe, el QR está repetido). Va al campo, no a un cartel.
      if (error.status === 409 || error.status === 422) {
        const mensaje = error.message || 'Revisa los datos.';
        if (/serial/i.test(mensaje)) campoSerial.marcar(mensaje);
        else if (/qr|c[oó]digo/i.test(mensaje)) campoQR.marcar(mensaje);
        else avisoFormulario(mensaje);
      } else {
        avisoFormulario(error.message);
      }
    } finally {
      btnSubmit.disabled = false;
      btnSubmit.textContent = 'Crear componente';
    }
  }

  return { elemento: raiz };
}