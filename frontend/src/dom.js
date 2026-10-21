/**
 * Construccion de DOM sin `innerHTML`.
 *
 * POR QUE ESTE ARCHIVO EXISTE
 * ---------------------------
 * El `index.html` anterior hacia esto en siete lugares:
 *
 *     li.innerHTML = `<span>${code}</span>`;              // venia de un QR
 *     box.innerHTML += `<div>Tú: ${q}</div>`;             // venia del usuario
 *     box.innerHTML += `<div>IA: ${j.respuesta}</div>`;   // venia del modelo
 *
 * Cada uno de esos `${}` es una inyeccion. El primero es el más grave de los
 * tres y el más fácil de explotar: `code` es lo que dice una etiqueta fisica, o
 * sea que un atacante imprime un QR con `<img src=x onerror="...">`, lo pega en
 * cualquier equipo y espera. Cuando el operador lo escanea, el script corre en
 * la página con la sesión del operador.
 *
 * El segundo y el tercero son XSS reflejado y almacenado respectivamente: la
 * respuesta de la IA suele devolver en prosa parte de lo que se le pregunto,
 * así que un texto con `<script>` va y vuelve entre el usuario y el modelo.
 *
 * LA REGLA
 * --------
 * `textContent` NUNCA interpreta HTML. Escribe el string como texto, con `<`,
 * `>` y `&` literales. Es la única forma de poner en pantalla algo que no
 * escribio este código.
 *
 * Cuando hace falta HTML de verdad —un `<div>` con una clase, un `<svg>`— se
 * construye con `document.createElement` y se le engancha el texto con
 * `textContent`. No hay caso en el que haga falta `innerHTML` acá.
 */

/**
 * Crea un elemento con clase, texto y atributos.
 *
 * @param {string} etiqueta     Ej: 'div', 'button', 'span'
 * @param {object} [opciones]
 * @param {string} [opciones.clase]
 * @param {string} [opciones.id]
 * @param {string} [opciones.texto]     Va por textContent. Seguro con cualquier dato.
 * @param {string} [opciones.estilo]     Estilo en línea (cssText).
 * @param {object} [opciones.atributos] atributos="valor"
 * @param {Array}  [opciones.hijos]     Elementos ya construidos.
 *
 * LO QUE SE DESCARTA EN SILENCIO
 * ------------------------------
 * Cualquier clave que no sea `clase`, `id`, `texto`, `atributos`, `hijos` o
 * `estilo` se ignora. No hay error, no hay aviso: el elemento sale sin eso y el
 * bug aparece más tarde y en otro lado.
 *
 * Ya paso tres veces en este repo, todas con la misma forma:
 *
 *   crear('div', { clase: 'x' }, [hijo])  → el hijo nunca se agrega
 *   crear('p',  { id: 'y' })              → el `id` no existe
 *   crear('div', { role: 'dialog' })      → no es un dialogo para el lector
 *   crear('div', { estilo: 'margin:0' })  → el estilo no se aplica
 *
 * La regla: lo que sea atributo va en `atributos`. `id` y `estilo` son las dos
 * excepciones, porque se usan en todas partes y casi siempre es un error
 * olvidarlos.
 */
export function crear(etiqueta, opciones = {}) {
  const { clase, id, texto, atributos, hijos, estilo } = opciones;
  const el = document.createElement(etiqueta);

  if (clase) el.className = clase;
  if (id) el.id = id;

  // EL ESTILO EN Línea: ESTABA EN EL DOCSTRING Y NO EN EL Código
  // -----------------------------------------------------------
  // El helper documentaba cuatro opciones y aceptaba cuatro: `estilo` no
  // existia, y todas las vistas la usaban (treinta y seis lugares). El
  // resultado era que NINGUNO de esos estilos se aplicaba, y como el efecto
  // es sutil (un `margin:0` de más) nadie lo notaba: no habia ningún error,
  // solo un archivo con la forma correcta y el resultado equivocado.
  if (estilo) el.style.cssText = estilo;

  // `textContent` y no `innerText`: `innerText` depende del layout y es más
  // lento, y además interpreta entidades. No es lo que se quiere.
  if (texto !== undefined && texto !== null) el.textContent = String(texto);

  if (atributos) {
    for (const [nombre, valor] of Object.entries(atributos)) {
      if (valor === false || valor === null || valor === undefined) continue;

      if (nombre === 'dataset') {
        Object.assign(el.dataset, valor);
      } else if (nombre.startsWith('on') && typeof valor === 'function') {
        el.addEventListener(nombre.slice(2).toLowerCase(), valor);
      } else if (valor === true) {
        el.setAttribute(nombre, '');
      } else {
        el.setAttribute(nombre, String(valor));
      }
    }
  }

  if (hijos) {
    for (const hijo of hijos) {
      if (hijo) el.append(hijo);
    }
  }

  return el;
}

/** Borra todos los hijos de un elemento. */
export function vaciar(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

/** Reemplaza el contenido de un elemento por otros, en una sola operación. */
export function reemplazar(el, ...nuevos) {
  vaciar(el);
  el.append(...nuevos.filter(Boolean));
}

/**
 * Un ícono SVG desde una cadena de path.
 *
 * Se construye con `createElementNS` porque SVG no es HTML: `createElement`
 * crea un elemento del namespace equivocado y el navegador no lo dibuja. Es la
 * trampa clasica de SVG en JS.
 */
const NS_SVG = 'http://www.w3.org/2000/svg';

export function icono(paths, { tamano = 20, className = '' } = {}) {
  const svg = document.createElementNS(NS_SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(tamano));
  svg.setAttribute('height', String(tamano));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  if (className) svg.setAttribute('class', className);

  for (const d of [].concat(paths)) {
    const p = document.createElementNS(NS_SVG, 'path');
    p.setAttribute('d', d);
    svg.append(p);
  }

  return svg;
}

/**
 * Etiqueta de un producto, para desplegables y listados.
 *
 * POR QUE NO ES `${p.brand} ${p.model} (${p.sku})`
 * -----------------------------------------------
 * Porque el SKU es NULL en los productos a los que todavía no se les asigno
 * uno, y la interpolacion de `null` en una template string no da una cadena
 * vacía: da la palabra "null". El operador ve "Dell Latitude 5520 (null)" en
 * el desplegable del alta de componentes y no sabe si es un dato roto o una
 * parte del nombre.
 *
 * El SKU va cuando está, y no va cuando no: el nombre del producto ya
 * alcanza para elegirlo.
 */
export function etiquetaProducto(p) {
  if (!p) return '—';
  const nombre = [p.brand, p.model].filter(Boolean).join(' ').trim();
  const base = nombre || 'Sin modelo';
  return p.sku ? `${base} (${p.sku})` : base;
}

/** Las rutas de los íconos que usa la app. */
export const ICONOS = {
  camara: 'M3 7V5a2 2 0 012-2h2M17 3h2a2 2 0 012 2v2M21 17v2a2 2 0 01-2 2h-2M7 21H5a2 2 0 01-2-2v-2M3 12h18',
  teclado: ['M3 6h18v12H3z', 'M7 10h.01M11 10h.01M15 10h.01M17 14H7'],
  ok: 'M20 6L9 17l-5-5',
  alerta: ['M12 9v4M12 17h.01', 'M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z'],
  error: ['M12 8v4M12 16h.01', 'M12 2a10 10 0 100 20 10 10 0 000-20z'],
  buscar: ['M11 19a8 8 0 100-16 8 8 0 000 16z', 'M21 21l-4.35-4.35'],
  reloj: ['M12 2a10 10 0 100 20 10 10 0 000-20z', 'M12 6v6l4 2'],
  ubicacion: ['M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z', 'M12 13a3 3 0 100-6 3 3 0 000 6z'],
  relojito: ['M12 6v6l4 2', 'M12 2a10 10 0 100 20 10 10 0 000-20z'],
  refrescar: ['M23 4v6h-6', 'M1 20v-6h6', 'M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15'],
  wifiOff: ['M1 1l22 22', 'M16.72 11.06A10.94 10.94 0 0019 12.55', 'M5 12.55a10.94 10.94 0 015.17-2.39', 'M10.71 5.05A16 16 0 0122.58 9', 'M1.42 9a15.91 15.91 0 014.7-2.88', 'M8.53 16.11a6 6 0 016.95 0', 'M12 20h.01'],
};

/** Traduce el estado de la base a la insignia con su clase. */
export const ESTADOS = {
  IN_WAREHOUSE: { texto: 'En depósito', clase: 'insignia--deposito' },
  IN_TRANSIT: { texto: 'En tránsito', clase: 'insignia--transito' },
  INSTALLED: { texto: 'Instalado', clase: 'insignia--instalado' },
  IN_REPAIR: { texto: 'En taller', clase: 'insignia--taller' },
  PERMANENTLY_DISPOSED: { texto: 'Dado de baja', clase: 'insignia--baja' },
};

/** Traduce el tipo de movimiento a algo legible. */
export const MOVIMIENTOS = {
  WAREHOUSE_ENTRY: 'Entró al depósito',
  WAREHOUSE_REENTRY: 'Volvio al depósito',
  OUT_TO_CLIENT: 'Salió hacia el cliente',
  INSTALLATION: 'Instalado',
  REPLACEMENT: 'Reemplazo',
  WITHDRAWAL_TO_WORKSHOP: 'Mando a taller',
  DISPOSAL: 'Dado de baja',
};
