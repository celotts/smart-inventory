import { test, expect } from '@playwright/test';

/**
 * La cabecera: dónde está cada cosa.
 *
 * POR QUÉ ESTA PRUEBA
 * -------------------
 * La cabecera se rehízo varias veces y cada vez hubo que volver a medir si
 * el selector de módulo quedaba centrado, en su sitio, y sin moverse. Es
 * una prueba de POSICIÓN, y las posiciones no se ven en el código: se ven
 * midiendo el navegador.
 *
 * LO QUE SE COMPRUEBA
 * -------------------
 * 1. El selector de módulo (Inventario | Service Desk) está en la SEGUNDA
 *    fila, en la misma línea que el bloque del usuario ("Rol: Admin").
 * 2. Está CENTRADO en la página: el centro del selector es el centro de la
 *    ventana.
 * 3. NO SE MUEVE al cambiar de módulo (es lo que se pidió: que se mantenga
 *    en una posición fija para evitar el salto).
 */

/**
 * Entra en la app y elige un módulo.
 * @param {import('@playwright/test').Page} page
 */
async function entrar(page) {
  await page.goto('/');
  await page.locator('input[type="email"]').waitFor({ timeout: 20_000 });
  await page.locator('input[type="email"]').fill('admin@smartinventory.local');
  await page.locator('input[type="password"]').fill('admin123');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.locator('#cabecera').waitFor({ timeout: 20_000 });

  // El admin elige módulo en el lanzador.
  const modulo = page.locator('[data-modulo="inventario"]').last();
  if (await modulo.count()) {
    await modulo.click({ force: true });
    await page.waitForTimeout(2000);
  }
}

/**
 * La caja y el centro de un elemento.
 * @param {import('@playwright/test').Page} page
 * @param {string} selector
 */
async function caja(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return {
      x: Math.round(b.x),
      y: Math.round(b.y),
      w: Math.round(b.width),
      h: Math.round(b.height),
      centroX: Math.round(b.x + b.width / 2),
      centroY: Math.round(b.y + b.height / 2),
    };
  }, selector);
}

test.describe('Cabecera', () => {
  test('el selector de módulo está en la segunda fila, junto al rol', async ({ page }) => {
    await entrar(page);

    const selector = await caja(page, '#cabecera-modulo');
    const rol = await caja(page, '#cabecera-usuario-rol');

    expect(selector, 'No se encuentra el selector de modulo').not.toBeNull();
    expect(rol, 'No se encuentra el bloque del rol').not.toBeNull();

    // La MISMA fila: la diferencia vertical es menor que la altura de la
    // fila (no tiene que estar pegado, solo en la misma linea).
    expect(Math.abs(selector.centroY - rol.centroY)).toBeLessThan(20);

    // Y el rol queda a la IZQUIERDA del selector.
    expect(rol.x).toBeLessThan(selector.x);
  });

  test('el selector de módulo está centrado en la página', async ({ page }) => {
    await entrar(page);

    const selector = await caja(page, '#cabecera-modulo');
    const ancho = await page.evaluate(() => window.innerWidth);

    expect(selector).not.toBeNull();

    // El centro del selector es el centro de la ventana (con un margen de
    // 2 px: el padding de la cabecera corre un poco el centro util).
    expect(Math.abs(selector.centroX - Math.round(ancho / 2))).toBeLessThanOrEqual(2);
  });

  test('el selector no se mueve al cambiar de módulo', async ({ page }) => {
    await entrar(page);

    const antes = await caja(page, '#cabecera-modulo');

    // Cambiar al otro módulo.
    await page.locator('#cabecera-modulo [data-modulo="helpdesk"]').click({ force: true });
    await page.waitForTimeout(2000);

    const despues = await caja(page, '#cabecera-modulo');

    expect(antes).not.toBeNull();
    expect(despues).not.toBeNull();

    // Misma fila, mismo ancho y mismo centro: no hay "salto" de ubicación.
    expect(despues.y).toBe(antes.y);
    expect(despues.w).toBe(antes.w);
    expect(despues.centroX).toBe(antes.centroX);
  });
});
