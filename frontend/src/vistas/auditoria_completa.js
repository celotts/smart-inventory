/**
 * Vista Auditoría Completa - Trazabilidad profesional con INSERT/UPDATE/DELETE
 */
import { api } from '../api.js';
import { crearPaginacion } from '../paginacion.js';
import { crear, icono, ICONOS } from '../dom.js';
import { formatearFecha } from './inventario.js';

const ACCIONES = {
  INSERT: { texto: 'CREADO', clase: 'insignia--ok' },
  UPDATE: { texto: 'ACTUALIZADO', clase: 'insignia--transito' },
  DELETE: { texto: 'ELIMINADO', clase: 'insignia--peligro' },
  LOGIN: { texto: 'LOGIN', clase: 'insignia--deposito' },
  LOGOUT: { texto: 'LOGOUT', clase: '' },
};

const TABLAS = {
  devices: 'Equipos',
  users: 'Usuarios',
  roles: 'Roles',
  models: 'Productos',
  clients: 'Clientes',
  vendors: 'Proveedores',
  purchase_orders: 'Órdenes de Compra',
  locations: 'Ubicaciones',
  categories: 'Categorias',
};

export function vistaAuditoriaCompleta({ permisos = [] } = {}) {
  const raiz = crear('div');

  // Header
  const tarjeta = crear('div', { clase: 'tarjeta' });
  tarjeta.append(
    crear('div', {
      hijos: [
        crear('h2', { clase: 'tarjeta__titulo', texto: 'Auditoría - Trazabilidad Completa' }),
        crear('p', {
          clase: 'tarjeta__subtitulo',
          estilo: 'margin:0',
          texto: 'Registro de todas las operaciones CRUD con valores anteriores y nuevos.',
        }),
      ],
    })
  );

  // Filtros
  const filtros = crear('div', { clase: 'rejilla-filtros', estilo: 'margin-top:1rem' });
  
  const selTabla = crear('select', { clase: 'campo__control' });
  selTabla.append(
    crear('option', { texto: 'Todas las tablas', atributos: { value: '' } }),
    ...Object.entries(TABLAS).map(([k, v]) => crear('option', { texto: v, atributos: { value: k } }))
  );

  const selAccion = crear('select', { clase: 'campo__control' });
  selAccion.append(
    crear('option', { texto: 'Todas las acciones', atributos: { value: '' } }),
    crear('option', { texto: 'CREADO', atributos: { value: 'INSERT' } }),
    crear('option', { texto: 'ACTUALIZADO', atributos: { value: 'UPDATE' } }),
    crear('option', { texto: 'ELIMINADO/BORRADO LÓGICO', atributos: { value: 'DELETE' } })
  );

  filtros.append(
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Tabla' }), selTabla] }),
    crear('label', { clase: 'campo', hijos: [crear('span', { clase: 'campo__etiqueta', texto: 'Accion' }), selAccion] }),
  );

  tarjeta.append(filtros);
  raiz.append(tarjeta);

  const lista = crear('div', { clase: 'tarjeta', estilo: 'margin-top:1rem' });
  lista.append(crear('h3', { texto: 'Historial de Auditoría' }));
  const contenido = crear('div');
  lista.append(contenido);
  raiz.append(lista);
  // La barra de paginacion va DESPUES de la lista, en la tarjeta.
  const zonaPaginacion = crear('div', { estilo: 'margin-top:0.75rem' });
  raiz.append(zonaPaginacion);

  // La PAGINACION
  //
  // Esta vista es la que mas filas tiene (cada escritura queda registrada),
  // asi que sin paginacion el operador scrollea 4000 eventos para encontrar
  // uno.
  let pagina = 0;
  let porPagina = 50;

  const paginacion = crearPaginacion({
    total: 0,
    limite: porPagina,
    pagina,
    etiqueta: 'eventos',
    alCambiarPagina: (nueva) => {
      pagina = nueva;
      cargar();
    },
    alCambiarLimite: (nuevo) => {
      porPagina = nuevo;
      pagina = 0;
      cargar();
    },
  });

  async function cargar() {
    contenido.replaceChildren(crear('div', { clase: 'cargando', texto: 'Cargando...' }));
    try {
      // Por `api`, y no con un `fetch` a mano: el token vive en MEMORIA y no
      // en el `localStorage`, asi que el `fetch` de antes mandaba la
      // peticion sin token y la pantalla no cargaba nunca.
      const data = await api.auditoriaTrazabilidad({
        table_name: selTabla.value || undefined,
        limit: porPagina,
        offset: pagina * porPagina,
      });

      paginacion.actualizar({ total: data.total ?? 0, limite: porPagina, pagina });

      if (!data.items || !data.items.length) {
        contenido.replaceChildren(crear('p', { clase: 'tenue', texto: 'Sin registros' }));
        return;
      }
      
      const ul = crear('ul', { clase: 'lista' });
      for (const item of data.items) {
        const li = crear('li', { clase: 'lista__item' });
        const body = crear('div', { clase: 'lista__cuerpo' });
        
        const acc = ACCIONES[item.action] || { texto: item.action, clase: '' };
        const titulo = crear('div', { clase: 'flex-entre' });
        titulo.append(
          crear('div', { clase: 'lista__titulo', texto: `${acc.texto} - ${TABLAS[item.table_name] || item.table_name}` }),
          crear('span', { clase: `insignia ${acc.clase}`, texto: acc.texto })
        );
        body.append(titulo);
        
        const meta = crear('div', { clase: 'lista__meta' });
        meta.append(
          crear('span', { texto: item.user_name || item.user_email || 'Sistema' }),
          crear('span', { texto: ' · ' }),
          crear('span', { clase: 'tabular', texto: formatearFecha(item.created_at) })
        );
        if (item.ip_address) {
          meta.append(crear('span', { texto: ' · IP: ' }), crear('span', { clase: 'mono', texto: item.ip_address }));
        }
        body.append(meta);
        
        if (item.record_id) {
          body.append(crear('div', { clase: 'lista__meta mono pequeno', texto: `ID: ${item.record_id}` }));
        }
        
        // Mostrar cambios
        if (item.action === 'UPDATE' && item.old_value && item.new_value) {
          const detalles = crear('details', { estilo: 'margin-top:0.5rem' });
          detalles.append(crear('summary', { texto: 'Ver cambios' }));
          const pre = crear('pre', { clase: 'pequeno', estilo: 'overflow:auto;max-height:300px' });
          pre.textContent = JSON.stringify({ antes: JSON.parse(item.old_value), despues: JSON.parse(item.new_value) }, null, 2);
          detalles.append(pre);
          body.append(detalles);
        } else if (item.action === 'INSERT' && item.new_value) {
          const detalles = crear('details', { estilo: 'margin-top:0.5rem' });
          detalles.append(crear('summary', { texto: 'Ver datos creados' }));
          const pre = crear('pre', { clase: 'pequeno', estilo: 'overflow:auto;max-height:300px' });
          pre.textContent = JSON.stringify(JSON.parse(item.new_value), null, 2);
          detalles.append(pre);
          body.append(detalles);
        } else if (item.action === 'DELETE' && item.old_value) {
          const detalles = crear('details', { estilo: 'margin-top:0.5rem' });
          detalles.append(crear('summary', { texto: 'Ver datos eliminados' }));
          const pre = crear('pre', { clase: 'pequeno', estilo: 'overflow:auto;max-height:300px' });
          pre.textContent = JSON.stringify(JSON.parse(item.old_value), null, 2);
          detalles.append(pre);
          body.append(detalles);
        }
        
        li.append(body);
        ul.append(li);
      }
      contenido.replaceChildren(ul);
    } catch(e) {
      contenido.replaceChildren(crear('div', { clase: 'resultado resultado--error', texto: e.message }));
    }
  }
  
  zonaPaginacion.append(paginacion.elemento);

  selTabla.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });
  selAccion.addEventListener('change', () => {
    pagina = 0;
    cargar();
  });
  cargar();
  
  return { elemento: raiz };
}
