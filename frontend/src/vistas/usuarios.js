/**
 * Vista "Usuarios" — quien puede entrar a la app.
 *
 * QUE HACE
 * --------
 * La API de usuarios ya existia (`/api/users`), pero NO habia pantalla: un
 * administrador no podia crear un usuario, ni resetearle la contraseña, ni
 * desactivar a alguien que se fue de la empresa. Todo eso era por mail o por
 * una terminal.
 *
 * EL ROL, DENTRO DE LA PANTALLA
 * ------------------------------
 * El rol NO es un detalle del formulario: define que puede hacer la persona
 * que entra. Por eso el selector de rol muestra lo que cada rol puede hacer
 * (el backend decide, pero el que elige tiene que saber lo que elige).
 *
 * LA CONTRASENA NUNCA SE MUESTRA
 * -------------------------------
 * Al crear o al resetear, la clave se escribe en un campo de password con el
 * medidor de fortaleza, y no se vuelve a mostrar: el backend guarda el hash
 * (bcrypt), asi que ni siquiera el servidor la tiene. El admin la define y
 * se la comunica al usuario por el canal que corresponda.
 *
 * SOLO ADMIN
 * -----------
 * La pantalla entera pide `users:read`, que solo tiene ADMIN. Un tecnico que
 * puede ver el inventario no puede ver la lista de usuarios: es informacion
 * que no necesita para trabajar, y el que la tiene puede cambiar el rol de
 * cualquiera.
 */

import { api } from '../api.js';
import { crearPaginacion } from '../paginacion.js';
import { crear, icono, ICONOS } from '../dom.js';
import { avisar } from '../avisos.js';
import { abrirDialogo, confirmar } from '../dialogo.js';
import { campoTexto, campoSelect, resumenDeErrores } from '../formulario.js';
import { conTooltip } from '../tooltip.js';
import { PasswordStrengthMeter } from '../composables/usePasswordStrength.js';

/**
 * Que puede hacer cada rol.
 *
 * La fuente de verdad son los permisos del BACKEND (`core/permissions.py`).
 * Aca va la version para el humano: el que crea un usuario tiene que
 * entender lo que esta creando, y un "Rol: OPERATOR" no dice nada.
 */
const ROLES = [
  {
    valor: 'ADMIN',
    texto: 'Administrador',
    ayuda: 'Control total: usuarios, roles, catálogo, precios y auditoría.',
  },
  {
    valor: 'TECHNICIAN',
    texto: 'Técnico',
    ayuda: 'Ve el inventario, trabaja el taller y resuelve incidencias.',
  },
  {
    valor: 'OPERATOR',
    texto: 'Operador',
    ayuda: 'Escanea, registra movimientos y reporta incidencias.',
  },
];

export function vistaUsuarios({ permisos = [] } = {}) {
  const raiz = crear('div');

  const puedeEditar = permisos.includes('users:edit');
  const puedeCrear = permisos.includes('users:create');

  // --- Cabecera -------------------------------------------------------------
  const tarjetaCabecera = crear('section', { clase: 'tarjeta' });

  const encabezado = crear('div', { clase: 'flex-entre' });
  encabezado.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Usuarios' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Quien puede entrar a la app y que puede hacer.',
        }),
      ],
    }),
  );

  if (puedeCrear) {
    const btnNuevo = crear('button', {
      clase: 'boton',
      texto: 'Nuevo usuario',
      atributos: { type: 'button', onclick: () => abrirAlta() },
    });
    conTooltip(btnNuevo, 'Crea un usuario con su email, su rol y su contraseña inicial.');
    encabezado.append(btnNuevo);
  }

  // --- Filtros --------------------------------------------------------------
  const inputBuscar = crear('input', {
    clase: 'campo__control',
    atributos: {
      type: 'search',
      placeholder: 'Buscar por nombre o email',
      autocomplete: 'off',
      spellcheck: 'false',
    },
  });

  const selectRol = crear('select', { clase: 'campo__control' });
  selectRol.append(crear('option', { texto: 'Todos los roles', atributos: { value: '' } }));
  for (const rol of ROLES) {
    selectRol.append(crear('option', { texto: rol.texto, atributos: { value: rol.valor } }));
  }

  const chkInactivos = crear('input', {
    clase: 'campo__control',
    atributos: { type: 'checkbox' },
  });
  chkInactivos.style.width = 'auto';

  const etiquetaBusqueda = crear('label', { clase: 'campo' });
  const textoBusqueda = crear('span', { clase: 'campo__etiqueta', texto: 'Buscar' });
  etiquetaBusqueda.append(textoBusqueda, inputBuscar);
  conTooltip(textoBusqueda, 'Busca por nombre o por email.');

  const etiquetaRol = crear('label', { clase: 'campo' });
  etiquetaRol.append(
    crear('span', { clase: 'campo__etiqueta', texto: 'Rol' }),
    selectRol,
  );

  const etiquetaInactivos = crear('label', { clase: 'campo' });
  const textoInactivos = crear('span', { clase: 'campo__etiqueta', texto: 'Ver desactivados' });
  etiquetaInactivos.append(textoInactivos, chkInactivos);
  conTooltip(textoInactivos, 'Un usuario desactivado no puede entrar, pero su historial se conserva.');

  const rejillaFiltros = crear('div', { clase: 'rejilla-filtros' });
  rejillaFiltros.append(etiquetaBusqueda, etiquetaRol, etiquetaInactivos);

  tarjetaCabecera.append(encabezado, rejillaFiltros);

  // --- Resultados -----------------------------------------------------------
  const zonaLista = crear('div');

  // LA PAGINACION
  //
  // La lista venia con un tope de 100 usuarios: con mas de 100, el
  // administrador veia 100 y no podia llegar a los demas.
  let pagina = 0;
  let limite = 50;
  let total = 0;

  const paginacion = crearPaginacion({
    total: 0,
    limite,
    pagina,
    etiqueta: 'usuarios',
    alCambiarPagina: (nueva) => {
      pagina = Math.max(0, nueva);
      cargar();
    },
    alCambiarLimite: (nuevo) => {
      limite = nuevo;
      pagina = 0;
      cargar();
    },
  });

  raiz.append(tarjetaCabecera, zonaLista, paginacion.elemento);

  let peticionEnCurso = 0;

  async function cargar() {
    const numero = ++peticionEnCurso;
    zonaLista.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando usuarios…' }));

    try {
      const datos = await api.listarUsuarios({
        busqueda: inputBuscar.value.trim() || undefined,
        rol: selectRol.value || undefined,
        solo_activos: !chkInactivos.checked || undefined,
        limite,
        offset: pagina * limite,
      });

      if (numero !== peticionEnCurso) return;
      const usuarios = datos.usuarios ?? [];
      total = datos.total ?? usuarios.length;
      pintarLista(usuarios);
      paginacion.actualizar({ total, limite, pagina });
    } catch (error) {
      if (numero !== peticionEnCurso) return;
      zonaLista.replaceChildren(
        crear('div', { clase: 'resultado resultado--error', texto: error.message }),
      );
    }
  }

  function pintarLista(usuarios) {
    if (!usuarios.length) {
      const vacio = crear('div', { clase: 'tarjeta centrado tenue' });
      vacio.append(
        icono(ICONOS.buscar, { tamano: 28 }),
        crear('p', { texto: 'No hay usuarios con esos filtros.' }),
      );
      zonaLista.replaceChildren(vacio);
      return;
    }

    const lista = crear('ul', { clase: 'lista' });

    for (const u of usuarios) {
      const item = crear('li', { clase: 'lista__item' });

      const cuerpo = crear('div', { clase: 'lista__cuerpo' });
      cuerpo.append(
        crear('div', { clase: 'lista__titulo', texto: u.full_name || u.email }),
        crear('div', { clase: 'lista__meta mono', texto: u.email }),
        crear('div', {
          clase: 'lista__meta',
          texto: [u.role_name, u.phone].filter(Boolean).join(' · '),
        }),
      );

      // La insignia del rol: dice QUE PUEDE HACER, no solo como se llama.
      const insignia = crear('span', {
        clase: `insignia ${u.is_active ? 'insignia--deposito' : 'insignia--baja'}`,
        texto: u.is_active ? (u.role_name ?? '—') : 'Desactivado',
      });
      const rol = ROLES.find((r) => r.valor === u.role_name);
      conTooltip(insignia, rol ? rol.ayuda : `Rol: ${u.role_name ?? '—'}`);

      item.append(cuerpo, insignia);

      if (puedeEditar) {
        const btnClave = crear('button', {
          clase: 'boton boton--suave boton--chico',
          texto: 'Clave',
          atributos: { type: 'button', onclick: () => abrirReset(u) },
        });
        conTooltip(btnClave, `Cambiar la contraseña de ${u.full_name || u.email}.`);

        const btnEstado = crear('button', {
          clase: `boton boton--chico ${u.is_active ? 'boton--peligro' : ''}`,
          texto: u.is_active ? 'Desactivar' : 'Reactivar',
          atributos: { type: 'button', onclick: () => alternarActivo(u) },
        });
        conTooltip(
          btnEstado,
          u.is_active
            ? 'No podra entrar. Su historial de movimientos y de auditoria se conserva.'
            : 'Vuelve a poder entrar a la app.',
        );

        item.append(btnClave, btnEstado);
      }

      lista.append(item);
    }

    zonaLista.replaceChildren(lista);
  }

  // --- Alta de usuario ------------------------------------------------------
  async function abrirAlta() {
    const campoEmail = campoTexto({
      etiqueta: 'Email *',
      placeholder: 'operador@empresa.com',
      tipo: 'email',
      descripcion: 'Es el nombre de usuario: con esto inicia sesion.',
    });

    const campoNombre = campoTexto({
      etiqueta: 'Nombre y apellido *',
      placeholder: 'Ana Gomez',
      descripcion: 'Como aparece en la app y en la auditoria.',
    });

    const campoTelefono = campoTexto({
      etiqueta: 'Telefono *',
      placeholder: '+54 11 0000-0000',
      tipo: 'tel',
      descripcion: 'El contacto del usuario. Es obligatorio para poder avisarle.',
    });

    const campoRol = campoSelect({
      etiqueta: 'Rol *',
      opciones: ROLES.map((r) => ({ valor: r.valor, texto: r.texto })),
      vacio: 'Seleccionar…',
      descripcion: 'Define QUE PUEDE HACER la persona que entra.',
    });
    campoRol.control.value = 'OPERATOR';

    const campoClave = campoTexto({
      etiqueta: 'Contrasena *',
      placeholder: 'Minimo 8 caracteres',
      tipo: 'password',
      descripcion: 'La contrasena inicial. Comunicasela al usuario por un canal seguro.',
    });

    // El medidor de fortaleza: el ADMIN tiene que elegir una clave que no sea
    // "12345678" y que no se rompa a la primera.
    const medidor = PasswordStrengthMeter({ mostrarRequisitos: true });
    campoClave.control.addEventListener('input', () => medidor.actualizar(campoClave.control.value));

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

    form.append(
      zonaResumen,
      campoEmail.campo,
      campoNombre.campo,
      campoTelefono.campo,
      campoRol.campo,
      campoClave.campo,
      medidor.elemento,
    );

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Crear usuario',
      atributos: { type: 'submit' },
    });
    form.append(btnGuardar);

    let cerrarModal = () => {};

    async function guardar() {
      const email = campoEmail.control.value.trim();
      const nombre = campoNombre.control.value.trim();
      const telefono = campoTelefono.control.value.trim();
      const clave = campoClave.control.value;
      const rol = campoRol.control.value;

      [campoEmail, campoNombre, campoTelefono, campoRol, campoClave].forEach((c) => c.limpiar());
      zonaResumen.replaceChildren();

      if (!email) campoEmail.marcar('El email es obligatorio: es el usuario.');
      if (!nombre) campoNombre.marcar('El nombre es obligatorio.');
      if (!telefono) campoTelefono.marcar('El telefono es obligatorio.');
      if (!rol) campoRol.marcar('Elegi el rol.');
      if (clave.length < 8) campoClave.marcar('La contrasena necesita al menos 8 caracteres.');

      const resumen = resumenDeErrores([campoEmail, campoNombre, campoTelefono, campoRol, campoClave]);
      if (resumen) {
        zonaResumen.replaceChildren(resumen);
        return;
      }

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Creando…';
      try {
        const nuevo = await api.crearUsuario({
          email,
          password: clave,
          full_name: nombre,
          phone: telefono,
          role_name: rol,
        });
        avisar(`Usuario ${nuevo.email} creado.`, 'exito', 3000);
        cerrarModal(true);
        cargar();
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Crear usuario';
      }
    }

    // El boton de CERRAR. Un modal sin salida visible solo se cierra con la
    // tecla Escape, y en un celular no hay Escape: el operador queda
    // atrapado en el formulario.
    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    cerrarModal = abrirDialogo({
      titulo: 'Nuevo usuario',
      nota: 'La contrasena se guarda hasheada: no se puede volver a leer. Comunicasela al usuario.',
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

  // --- Reset de contrasena --------------------------------------------------
  async function abrirReset(usuario) {
    const campoClave = campoTexto({
      etiqueta: 'Contrasena nueva *',
      placeholder: 'Minimo 8 caracteres',
      tipo: 'password',
      descripcion: 'La nueva contrasena del usuario. Se cierran sus sesiones abiertas.',
    });

    const medidor = PasswordStrengthMeter({ mostrarRequisitos: true });
    campoClave.control.addEventListener('input', () => medidor.actualizar(campoClave.control.value));

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

    form.append(zonaResumen, campoClave.campo, medidor.elemento);

    const btnGuardar = crear('button', {
      clase: 'boton boton--bloque',
      texto: 'Cambiar contrasena',
      atributos: { type: 'submit' },
    });
    form.append(btnGuardar);

    let cerrarModal = () => {};

    async function guardar() {
      const clave = campoClave.control.value;
      campoClave.limpiar();
      zonaResumen.replaceChildren();

      if (clave.length < 8) {
        campoClave.marcar('La contrasena necesita al menos 8 caracteres.');
        zonaResumen.replaceChildren(resumenDeErrores([campoClave]) || crear('div'));
        return;
      }

      btnGuardar.disabled = true;
      btnGuardar.textContent = 'Cambiando…';
      try {
        await api.resetearPassword(usuario.id, clave);
        avisar(`Contrasena de ${usuario.email} actualizada.`, 'exito', 3000);
        cerrarModal(true);
      } catch (error) {
        avisar(error.message, 'error');
        btnGuardar.disabled = false;
        btnGuardar.textContent = 'Cambiar contrasena';
      }
    }

    // El boton de CERRAR. Un modal sin salida visible solo se cierra con la
    // tecla Escape, y en un celular no hay Escape: el operador queda
    // atrapado en el formulario.
    const btnCancelar = crear('button', {
      clase: 'boton boton--suave',
      texto: 'Cancelar',
      atributos: { type: 'button' },
    });

    cerrarModal = abrirDialogo({
      titulo: `Contrasena de ${usuario.email}`,
      nota: 'Se cierran las sesiones abiertas de ese usuario.',
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

  // --- Desactivar / reactivar ------------------------------------------------
  async function alternarActivo(usuario) {
    const ok = await confirmar({
      titulo: usuario.is_active ? 'Desactivar usuario' : 'Reactivar usuario',
      mensaje: usuario.is_active
        ? `${usuario.full_name || usuario.email} no va a poder entrar a la app. ` +
          'Su historial de movimientos y de auditoria se conserva.'
        : `${usuario.full_name || usuario.email} vuelve a poder entrar a la app.`,
      textoConfirmar: usuario.is_active ? 'Desactivar' : 'Reactivar',
      peligro: usuario.is_active,
    });
    if (!ok) return;

    try {
      if (usuario.is_active) {
        await api.desactivarUsuario(usuario.id);
      } else {
        await api.restaurarUsuario(usuario.id);
      }
      avisar(usuario.is_active ? 'Usuario desactivado.' : 'Usuario reactivado.', 'exito', 2500);
      cargar();
    } catch (error) {
      avisar(error.message, 'error');
    }
  }

  inputBuscar.addEventListener('input', cargar);
  selectRol.addEventListener('change', cargar);
  chkInactivos.addEventListener('change', cargar);

  cargar();
  return { elemento: raiz };
}
