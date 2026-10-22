import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

/**
 * La sintaxis de TODOS los modulos del front.
 *
 * POR QUE ESTE TEST
 * -----------------
 * El front no tiene bundler: son modulos ES que el navegador carga uno por
 * uno. Un error de sintaxis en UNO de ellos (un identificador declarado dos
 * veces, una coma que falta) ABORTA el arranque de la app entera: no se
 * pinta ninguna pantalla y el operador ve "Cargando..." para siempre.
 *
 * Y el error no se ve hasta que se abre la pagina: la consola del navegador
 * es el unico lugar donde aparece, y el operador no la abre.
 *
 * `node --check` hace exactamente lo que el navegador hace con el archivo
 * (parsearlo) y tarda milisegundos por archivo. Es la red de seguridad mas
 * barata que hay: si este test pasa, la app arranca.
 */
const RAIZ = new URL('..', import.meta.url).pathname;

/**
 * Todos los `.js` del front, menos los que no se sirven.
 * @returns {string[]}
 */
function modulosDelFront() {
  const encontrados = [];

  const recorrer = (directorio) => {
    for (const nombre of readdirSync(directorio)) {
      const ruta = join(directorio, nombre);
      if (statSync(ruta).isDirectory()) {
        if (nombre === 'node_modules' || nombre === 'test-results') continue;
        recorrer(ruta);
      } else if (nombre.endsWith('.js')) {
        encontrados.push(ruta);
      }
    }
  };

  recorrer(join(RAIZ, 'src'));
  return encontrados;
}

test('todos los módulos del front tienen sintaxis válida', () => {
  const modulos = modulosDelFront();
  expect(modulos.length).toBeGreaterThan(20);

  const conError = [];
  for (const modulo of modulos) {
    try {
      execFileSync(process.execPath, ['--check', modulo], { stdio: 'pipe' });
    } catch (error) {
      conError.push(
        `${relative(RAIZ, modulo)}:\n${String(error.stderr ?? error.message).slice(0, 400)}`,
      );
    }
  }

  expect(
    conError,
    `Estos módulos del front no parsean. Un error de sintaxis en uno de ellos `
    + `aborta el arranque de la app ENTERA:\n\n${conError.join('\n\n')}`,
  ).toEqual([]);
});

test('ningún módulo declara dos veces el mismo identificador', async ({ page }) => {
  // `node --check` NO detecta esto: "Identifier 'x' has already been
  // declared" es un error de AMBITO, no de sintaxis. Se detecta al
  // EVALUAR el modulo, y el navegador lo evalua al cargarlo.
  //
  // Por eso este test IMPORTA los modulos en la pagina: si uno tiene el
  // error, el `import` falla. Y al fallar uno, el modulo que lo importa
  // tambien falla, y la app no arranca.
  // La pagina se carga en un archivo que NO es la app: si se carga el
  // `index.html`, el `main.js` ya evaluo todos los modulos y el `import` de
  // abajo devuelve la version en cache (que no se vuelve a evaluar), y el
  // error no aparece nunca.
  //
  // Con el `build.txt` la pagina esta en blanco, y cada `import` se
  // descarga y se evalua de verdad.
  await page.goto('/build.txt', { waitUntil: 'domcontentloaded' });

  // Los modulos se sirven con el prefijo de version (`/v/<hash>/src/...`),
  // que es lo que usa el `index.html`. Sin el, el import da 404 y el test
  // falla por una razon que no tiene que ver con lo que mide.
  const prefijo = await page.evaluate(async () => {
    const hash = (await (await fetch('/build.txt')).text()).trim();
    return `/v/${hash}/src`;
  });

  const modulos = modulosDelFront();
  const rutas = modulos.map((ruta) => `${prefijo}/${relative(join(RAIZ, 'src'), ruta)}`);

  const fallos = await page.evaluate(async (lista) => {
    const errores = [];
    for (const ruta of lista) {
      try {
        await import(ruta);
      } catch (error) {
        const texto = String(error);
        // SOLO los errores de CARGA del modulo: los de sintaxis y los de
        // ambito ("Identifier 'x' has already been declared"), y los
        // imports rotos ("does not provide an export named 'x'").
        //
        // Los de RUNTIME (un `TypeError` porque el modulo busca un elemento
        // del DOM que en esta pagina en blanco no existe) no son lo que se
        // mide: la pagina de prueba no es la app.
        const esDeCarga =
          texto.includes('SyntaxError') ||
          texto.includes('has already been declared') ||
          texto.includes('does not provide an export') ||
          texto.includes('Failed to resolve module specifier');
        if (esDeCarga) {
          errores.push(`${ruta.split('/src/')[1]}: ${texto.slice(0, 200)}`);
        }
      }
    }
    return errores;
  }, rutas);

  expect(
    fallos,
    `Estos modulos no se pudieron cargar. Un error de ambito (un `
    + `identificador declarado dos veces) aborta el arranque de la app `
    + `entera:\n\n${fallos.join('\n')}`,
  ).toEqual([]);
});
