/**
 * Service worker.
 *
 * QUE HACE Y QUE NO
 * -----------------
 * Cachea el ESQUELETO de la app para que abra sin conexion, y nada mas.
 *
 * Lo que NO hace, y es deliberado:
 *
 *  - No cachea respuestas de la API. Un inventario cacheado es un inventario que
 *    muestra datos de ayer como si fueran de hoy. Un operador que ve "este
 *    equipo esta en deposito" y escanea en esa pantalla genera un movimiento con
 *    el estado equivocado. Es peor no tener datos que tenerlos viejos.
 *
 *  - No intercepta ni modifica peticiones. Es un cache de archivos, no un proxy.
 *
 * ESTRATEGIA
 * ----------
 * `index.html`  → SIEMPRE de la red, con el HTML cacheado de respaldo.
 *                 (así se ve de inmediato un deploy nuevo)
 * `/src/*`       → cache primero, red de fondo. (son archivos versionados)
 * `/icons/*`     → cache primero.
 * `/api/*`       → nunca cacheado.
 * otros          → red, y cache de respaldo si falla.
 */

// El sello se incrementa cuando cambia la estrategia de cache. Al cambiarlo,
// el `activate` de mas abajo borra los caches de la version anterior: sin eso,
// el navegador guarda megabytes de versiones que ya no existen y sirve la
// primera que encuentra.
const VERSION = 'si-v3';
const CACHE_ESTATICO = `${VERSION}-estatico`;
const CACHE_ESTABLE = `${VERSION}-estable`;

/** El esqueleto minimo para que la app abra sin red. */
const ESQUELETO = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/src/styles.css',
  // El vigilante de arranque va primero: sin el, una app que no arranca se
  // queda en "Cargando…" para siempre, y offline tampoco.
  '/src/arranque.js',
  '/src/main.js',
  '/src/api.js',
  '/src/dom.js',
  '/src/escaner.js',
  '/src/avisos.js',
  '/src/composables/usePasswordStrength.js',
  '/src/vistas/login.js',
  '/src/vistas/escanear.js',
  '/src/vistas/inventario.js',
  '/src/vistas/consultas.js',
  '/icons/icon.svg',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(
    caches.open(CACHE_ESTABLE).then(async (cache) => {
      // `addAll` es atomico: si UNO falla, no se instala ninguno. Con una app
      // cacheada a medias, el usuario ve una pantalla en blanco que no puede
      // explicar. Uno por uno con tolerancia es mejor aca.
      await Promise.all(
        ESQUELETO.map((ruta) =>
          cache.add(ruta).catch(() => {
            // Un recurso que no se pudo cachear no impide instalar el service
            // worker. Se pierde esa capacidad, no la app entera.
          }),
        ),
      );
    }),
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    (async () => {
      const nombres = await caches.keys();
      await Promise.all(
        nombres
          .filter((nombre) => !nombre.startsWith(VERSION))
          .map((nombre) => caches.delete(nombre)),
      );
      // `clients.claim()` para que un service worker viejo que estaba controlando
      // la pagina no la deje sin control hasta la proxima navegacion.
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (evento) => {
  const { request } = evento;

  // Solo GET: una peticion que no lo es no se puede cachear, y esta app hace
  // POST hasta para leer (los endpoints de IA y de escaneo).
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // La API JAMAS se cachea ni se intercepta. Sale temprano y sin tocar.
  if (url.pathname.startsWith('/api/')) return;

  // El sello de build tampoco: es lo que le dice a la app que hay version
  // nueva. Si el SW lo sirviera del cache, compararia consigo mismo y la
  // app no se recargaria nunca.
  if (url.pathname === '/build.txt') return;

  // Solo mismo origen: una peticion a un tercero no se mete en nuestro cache.
  if (url.origin !== self.location.origin) return;

  // Navegaciones: red primero, cache de respaldo. Asi un deploy nuevo se ve
  // apenas se recarga, y sin conexion la app abre igual.
  if (request.mode === 'navigate') {
    evento.respondWith(
      (async () => {
        try {
          const respuesta = await fetch(request);
          const cache = await caches.open(CACHE_ESTABLE);
          cache.put('/index.html', respuesta.clone());
          return respuesta;
        } catch {
          const cacheado = await caches.open(CACHE_ESTABLE);
          return (
            (await cache.match('/index.html')) ??
            (await cache.match('/')) ??
            new Response(
              '<!doctype html><meta charset="utf-8"><p>Sin conexion y sin version guardada de la app.</p>',
              { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } },
            )
          );
        }
      })(),
    );
    return;
  }

  // Assets estaticos: RED PRIMERO, cache de respaldo.
  //
  // POR QUE NO ES "CACHE PRIMERO"
  // --------------------------
  // `/src/main.js` no tiene hash en el nombre. "Cache primero" con un nombre
  // fijo significa que un navegador que ya visito la app NUNCA vuelve a pedir
  // ese archivo: sigue ejecutando el JS viejo para siempre, aunque el servidor
  // mande el corregido. Pasa por el service worker Y por el `immutable` de un
  // año en la cabecera de nginx, y el sintoma es "arregle el bug y el navegador
  // sigue viendo el bug".
  //
  // Red primero con respaldo en cache da lo mismo offline (el esqueleto esta
  // precacheado en `install`) y hace que un fix llegue en la PRIMERA recarga, que
  // es lo que un operador espera.
  evento.respondWith(
    (async () => {
      try {
        const respuesta = await fetch(request);
        if (respuesta.ok) {
          const copia = respuesta.clone();
          caches.open(CACHE_ESTATICO).then((cache) => cache.put(request, copia));
        }
        return respuesta;
      } catch {
        const cacheado = await caches.match(request);
        return cacheado ?? new Response('', { status: 504 });
      }
    })(),
  );
});

/**
 * El frontend le pide al SW limpiar caches viejos.
 *
 * Sin esto, cada deploy acumula la version anterior y el navegador termina
 * guardando megabytes de versiones que ya no existen.
 */
self.addEventListener('message', (evento) => {
  if (evento.data === 'limpiar-cache') {
    evento.waitUntil(
      caches.keys().then((nombres) =>
        Promise.all(
          nombres.filter((n) => !n.startsWith(VERSION)).map((n) => caches.delete(n)),
        ),
      ),
    );
  }
});
