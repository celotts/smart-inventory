/**
 * La medicion del FRONT con la base cargada.
 *
 * QUE MIDE
 * --------
 * 1. **Cuanto tarda en pintar cada pantalla.** Es lo que siente el operador:
 *    desde que toca la pestana hasta que ve los datos. Se mide desde el
 *    click hasta que la pantalla tiene contenido.
 *
 * 2. **Cuanto se descarga.** El peso de las respuestas. Un front que pide
 *    1,5 MB para pintar 25 filas se siente lento aunque la API responda en
 *    10 ms.
 *
 * 3. **Si algo se rompe.** Errores de consola, peticiones fallidas, y
 *    pantallas que quedan en "cargando".
 *
 * COMO SE USA
 * -----------
 *     node scripts/medir-front.mjs
 *     node scripts/medir-front.mjs --url http://localhost:5175
 *
 * La carga de la base se genera con `scripts/carga-prueba.py`.
 */

import { chromium } from '@playwright/test';

const URL_BASE = process.argv.includes('--url')
  ? process.argv[process.argv.indexOf('--url') + 1]
  : 'http://localhost:5175';
const EMAIL = 'admin@smartinventory.local';
const PASSWORD = 'admin123';

// Las pantallas que se miden: el nombre de la pestana y el texto que tiene
// que aparecer cuando termino de pintar.
const PANTALLAS = [
  { vista: 'inicio', titulo: 'Inventario' },
  { vista: 'inventario', titulo: 'Buscar' },
  { vista: 'existencia', titulo: 'Existencia' },
  { vista: 'movimientos', titulo: 'Movimientos' },
  { vista: 'productos', titulo: 'Productos' },
  { vista: 'compras', titulo: 'Compras' },
  { vista: 'consultas', titulo: 'Consultar' },
  { vista: 'incidencias', titulo: 'Incidencias' },
  { vista: 'usuarios', titulo: 'Usuarios' },
  { vista: 'escanear', titulo: 'Escanear' },
];

const navegador = await chromium.launch();
const contexto = await navegador.newContext({ viewport: { width: 1400, height: 900 } });
const pagina = await contexto.newPage();

// Todo lo que descarga la pagina.
const peticiones = [];
pagina.on('response', async (respuesta) => {
  const url = respuesta.url();
  if (!url.includes('/api/')) return;
  let tamano = 0;
  try {
    tamano = (await respuesta.body()).length;
  } catch {
    // Las respuestas sin cuerpo (204) fallan al leerlo: no importa.
  }
  peticiones.push({
    ruta: url.replace(URL_BASE, '').replace('/api', ''),
    estado: respuesta.status(),
    kb: Math.round(tamano / 1024 * 10) / 10,
  });
});

const errores = [];
pagina.on('pageerror', (e) => errores.push(`pageerror: ${String(e).slice(0, 200)}`));
pagina.on('console', (m) => {
  if (m.type() === 'error') errores.push(`consola: ${m.text().slice(0, 200)}`);
});
pagina.on('requestfailed', (r) => {
  errores.push(`peticion fallida: ${r.url().replace(URL_BASE, '')} ${r.failure()?.errorText}`);
});

console.log('\n' + '='.repeat(96));
console.log('EL FRONT CON CARGA');
console.log('='.repeat(96));

// --- El ingreso ---
await pagina.goto(URL_BASE, { waitUntil: 'domcontentloaded' });
await pagina.locator('input[type="email"]').waitFor({ timeout: 30_000 });
const inicio = Date.now();
await pagina.locator('input[type="email"]').fill(EMAIL);
await pagina.locator('input[type="password"]').fill(PASSWORD);
await pagina.getByRole('button', { name: 'Entrar' }).click();
await pagina.locator('#cabecera').waitFor({ timeout: 30_000 });
console.log(`\n  Ingreso (login + perfil + pantalla): ${Date.now() - inicio} ms`);

// El lanzador de modulos (el admin elige).
const modulo = pagina.locator('.lanzador__opcion').first();
if (await modulo.count()) {
  const t = Date.now();
  await modulo.click({ force: true });
  await pagina.waitForTimeout(1500);
  console.log(`  Eleccion de modulo:                ${Date.now() - t} ms`);
}

// --- Cada pantalla ---
console.log();
const r = (texto, largo) => String(texto).padStart(largo);
console.log(`${'pantalla'.padEnd(18)} ${r('pintado', 10)} ${r('peticiones', 11)} ${r('descargado', 12)}  estado`);
console.log('-'.repeat(96));

const resultados = [];
for (const pantalla of PANTALLAS) {
  const pestana = pagina.locator(`[data-vista="${pantalla.vista}"]`).first();
  if (!(await pestana.count())) {
    console.log(`${pantalla.vista.padEnd(18)} (no esta en este rol)`);
    continue;
  }
  if (!(await pestana.isVisible())) {
    console.log(`${pantalla.vista.padEnd(18)} (oculta en este rol)`);
    continue;
  }

  peticiones.length = 0;
  const t = Date.now();
  await pestana.click({ force: true });

  // Se espera a que la pantalla deje de estar "cargando".
  try {
    await pagina.waitForFunction(
      () => {
        const cargando = document.querySelector('#contenido .cargando');
        return !cargando;
      },
      { timeout: 30_000 },
    );
  } catch {
    // Se sigue: se reporta como "no termino".
  }
  const pintado = Date.now() - t;

  const fallidas = peticiones.filter((p) => p.estado >= 400);
  const descargado = Math.round(peticiones.reduce((suma, p) => suma + p.kb, 0) * 10) / 10;

  const estado = fallidas.length
    ? `FALLARON ${fallidas.length}: ${fallidas.map((f) => `${f.ruta} (${f.estado})`).join(', ')}`
    : 'ok';

  console.log(
    `${pantalla.vista.padEnd(18)} ${r(`${pintado} ms`, 10)} ${r(peticiones.length, 11)} ${r(`${descargado} KB`, 12)}  ${estado}`,
  );

  resultados.push({ ...pantalla, pintado, peticiones: peticiones.length, descargado, fallidas });
}

// --- Lo que se descargó en total ---
console.log();
console.log('-'.repeat(96));
const porRuta = new Map();
for (const p of peticiones) {
  porRuta.set(p.ruta, (porRuta.get(p.ruta) || 0) + p.kb);
}
const masPesadas = [...porRuta.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
if (masPesadas.length) {
  console.log('  Lo que mas peso en la ultima pantalla:');
  for (const [ruta, kb] of masPesadas) console.log(`    ${ruta.padEnd(44)} ${kb} KB`);
}

console.log();
if (errores.length) {
  console.log(`  ERRORES (${errores.length}):`);
  for (const e of [...new Set(errores)].slice(0, 12)) console.log(`    ${e}`);
} else {
  console.log('  Sin errores de consola ni peticiones fallidas.');
}

await navegador.close();
