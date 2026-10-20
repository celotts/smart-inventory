/**
 * Vista "Nuevo componente" — Alta de dispositivo en inventario.
 *
 * Solo visible para ADMIN (permiso devices:edit).
 * Accesible desde la barra de navegación (pestaña "Nuevo").
 */

import { api } from '../api.js';
import { crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo } from '../dialogo.js';

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
      onsubmit: async (e) => {
        e.preventDefault();
        await enviarFormulario();
      },
    },
  });

  // Campos
  const campoSerial = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'text',
      placeholder: 'Ej: SN-12345678',
      autocomplete: 'off',
      autocapitalize: 'characters',
      required: true,
      'aria-label': 'Número de serie del fabricante',
    },
  });

  const campoQR = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'text',
      placeholder: 'Opcional: INV-a1b2c3 (se genera si vacío)',
      autocomplete: 'off',
      autocapitalize: 'characters',
      'aria-label': 'Código QR propio (opcional)',
    },
  });

  // --- Producto, desde el maestro -------------------------------------------
  //
  // ANTES este formulario tenia campos sueltos de marca/modelo/categoria y los
  // mandaba como texto. El backend los RECHAZA: `CrearDispositivoRequest` exige
  // `model_id` (un id del catalogo) y tiene `extra="forbid"`, asi que el alta
  // fallaba con 422 siempre. Un equipo no lleva marca y modelo escritos a mano:
  // apunta a un producto del maestro, y de ahi hereda marca, modelo y categoria.
  const campoProducto = crear('select', {
    clase: 'campo__control',
    atributos: { 'aria-label': 'Producto del catálogo' },
  });
  campoProducto.append(
    crear('option', { texto: 'Cargando productos…', atributos: { value: '', disabled: true } }),
  );

  const campoUbicacion = crear('select', {
    clase: 'campo__control',
    atributos: {
      'aria-label': 'Ubicación inicial',
    },
  });
  campoUbicacion.append(crear('option', { texto: 'Cargando ubicaciones…', atributos: { value: '', disabled: true } }));

  const btnSubmit = crear('button', {
    clase: 'boton boton--bloque',
    texto: 'Crear componente',
    atributos: { type: 'submit' },
  });

  form.append(
    crear('h2', { clase: 'tarjeta__titulo', estilo: 'text-align:center; margin-bottom:0.5rem', texto: 'Nuevo componente' }),
    crear('p', { clase: 'tarjeta__subtitulo centrado', texto: 'Completa los datos para dar de alta un equipo en el inventario.' }),
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Producto *' }), campoProducto] }),
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Serial *' }), campoSerial] }),
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'QR propio' }), campoQR] }),
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Ubicación inicial *' }), campoUbicacion] }),
    btnSubmit,
  );

  raiz.append(form);

  // Cargar productos (maestro) y ubicaciones
  async function cargarProductos() {
    try {
      const productos = await api.productos();
      campoProducto.replaceChildren();
      campoProducto.append(crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true, selected: true } }));
      if (!productos.length) {
        campoProducto.replaceChildren(
          crear('option', { texto: 'No hay productos: crea uno en la pestaña "Productos"', atributos: { value: '', disabled: true, selected: true } }),
        );
        return;
      }
      for (const p of productos) {
        campoProducto.append(
          crear('option', { texto: `${p.brand} ${p.model} (${p.sku})`, atributos: { value: p.id } }),
        );
      }
    } catch (error) {
      campoProducto.replaceChildren(crear('option', { texto: 'Error al cargar productos', atributos: { value: '', disabled: true } }));
      console.error(error);
    }
  }

  // Cargar ubicaciones
  async function cargarUbicaciones() {
    try {
      const datos = await api.pedir('/devices/locations');
      campoUbicacion.replaceChildren();
      campoUbicacion.append(crear('option', { texto: 'Seleccionar…', atributos: { value: '', disabled: true, selected: true } }));
      for (const loc of datos.items) {
        campoUbicacion.append(crear('option', { texto: loc.nombre, atributos: { value: loc.id } }));
      }
    } catch (error) {
      campoUbicacion.replaceChildren(crear('option', { texto: 'Error al cargar ubicaciones', atributos: { value: '', disabled: true } }));
      console.error(error);
    }
  }
  cargarUbicaciones();
  cargarProductos();

  // Enviar formulario
  async function enviarFormulario() {
    const serial = campoSerial.value.trim();
    const qr = campoQR.value.trim() || undefined;
    const model_id = campoProducto.value ? parseInt(campoProducto.value, 10) : null;
    const ubicacion_id = campoUbicacion.value ? parseInt(campoUbicacion.value, 10) : null;

    if (!model_id) {
      avisar('Selecciona un producto del catálogo', 'alerta');
      campoProducto.focus({ preventScroll: true });
      return;
    }
    if (!serial) {
      avisar('El serial es obligatorio', 'alerta');
      campoSerial.focus({ preventScroll: true });
      return;
    }
    if (!ubicacion_id) {
      avisar('Selecciona una ubicación inicial', 'alerta');
      campoUbicacion.focus({ preventScroll: true });
      return;
    }

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
      campoProducto.value = '';
      campoUbicacion.value = '';
      cargarUbicaciones();
      cargarProductos();
    } catch (error) {
      avisar(error.message, 'error');
    } finally {
      btnSubmit.disabled = false;
      btnSubmit.textContent = 'Crear componente';
    }
  }

  return { elemento: raiz };
}