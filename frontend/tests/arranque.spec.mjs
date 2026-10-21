import { test, expect } from '@playwright/test';

/**
 * Prueba de HUMO: la app arranca y no tira errores.
 *
 * POR QUÉ EXISTE
 * --------------
 * La app no tiene bundler: son módulos ES que el navegador carga uno por uno.
 * Un `SyntaxError` en UNO de ellos (un nombre duplicado, un import que no
 * existe) aborta el arranque ENTERO: no se pinta ninguna pantalla y el
 * operador ve "Cargando…" para siempre.
 *
 * El vigilante de arranque (`arranque.js`) existe justo para eso, y avisa
 * después de 12 segundos. Esta prueba es la versión rápida: en 10 segundos
 * tiene que haber una pantalla, no un esqueleto.
 *
 * Y el error de consola se mira: un `console.error` o una excepción sin
 * manejar son la señal de que algo se rompió aunque la pantalla se vea bien.
 */
test('la app arranca y no tira errores de consola', async ({ page }) => {
  const problemas = [];

  page.on('pageerror', (e) => problemas.push(`pageerror: ${String(e).slice(0, 200)}`));
  page.on('console', (m) => {
    if (m.type() === 'error') problemas.push(`console.error: ${m.text().slice(0, 200)}`);
  });

  await page.goto('/');

  // La pantalla de login (o la app, si habia sesion) tiene que aparecer.
  await expect(page.locator('#contenido')).not.toContainText('Cargando…', {
    timeout: 20_000,
  });

  // Y no puede haber quedado el cartel de "la app no pudo arrancar".
  await expect(page.getByText('La app no pudo arrancar')).toHaveCount(0);

  expect(problemas).toEqual([]);
});
