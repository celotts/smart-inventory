import { test, expect } from '@playwright/test';

/**
 * Los cuadros de texto son BLANCOS.
 *
 * POR QUÉ ESTA PRUEBA
 * -------------------
 * El fondo de un campo de texto se ve gris cuando:
 *
 *  - el navegador le pone su aspecto nativo (un `<select>` sin
 *    `appearance: none` se pinta gris, con borde de 3D, por mas que el CSS
 *    diga blanco), o
 *  - el color viene de un token que en otro tema es gris (la superficie en
 *    el modo exterior es `#fafafa`), o
 *  - el navegador completa el campo solo y lo pinta con su fondo (el
 *    autofill de Chrome, que no se puede cambiar con `background`).
 *
 * Un campo gris se lee como deshabilitado, y el operador no sabe si ahí se
 * escribe. Por eso la regla es "blanco", y se comprueba en el navegador de
 * verdad: el CSS puede decir una cosa y el navegador hacer otra.
 */

/** Los controles que el navegador pinta con su aspecto NATIVO (gris). */
const CON_APARIENCIA_NATIVA = ['select', 'date', 'time', 'number'];

/**
 * Entra en la app y abre una vista con campos.
 * @param {import('@playwright/test').Page} page
 */
async function entrar(page) {
  await page.goto('/');
  await page.locator('input[type="email"]').waitFor({ timeout: 20_000 });
  await page.locator('input[type="email"]').fill('admin@smartinventory.local');
  await page.locator('input[type="password"]').fill('admin123');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.locator('#cabecera').waitFor({ timeout: 20_000 });

  // El admin elige módulo: se entra al de inventario.
  const modulo = page.locator('[data-modulo="inventario"]').last();
  if (await modulo.count()) {
    await modulo.click({ force: true });
    await page.waitForTimeout(1500);
  }

  // Y de ahí a la vista de inventario, que tiene buscador, select y paginación.
  const pestana = page.locator('[data-vista="inventario"]').first();
  if (await pestana.count()) {
    await pestana.click({ force: true });
    await page.waitForTimeout(2000);
  }
}

test.describe('Cuadros de texto', () => {
  test('el fondo de los campos es blanco', async ({ page }) => {
    await entrar(page);

    const campos = await page.evaluate(() =>
      [...document.querySelectorAll('input, select, textarea')].map((el) => ({
        etiqueta: `${el.tagName}:${el.type || ''}`,
        fondo: getComputedStyle(el).backgroundColor,
        apariencia: getComputedStyle(el).appearance,
      })),
    );

    expect(campos.length).toBeGreaterThan(0);

    for (const campo of campos) {
      // Blanco: rgb(255, 255, 255).
      expect(campo.fondo, `${campo.etiqueta} debería tener fondo blanco`).toBe(
        'rgb(255, 255, 255)',
      );

      // Y sin la apariencia nativa del navegador (que es la que pinta gris).
      const tipo = campo.etiqueta.split(':')[1] || 'text';
      if (CON_APARIENCIA_NATIVA.includes(tipo)) {
        expect(
          campo.apariencia,
          `${campo.etiqueta} no debería usar la apariencia nativa`,
        ).toBe('none');
      }
    }
  });

  test('los select tienen la flecha propia y no la del sistema', async ({ page }) => {
    await entrar(page);

    const selects = await page.evaluate(() =>
      [...document.querySelectorAll('select')].map((el) => ({
        conFlecha: getComputedStyle(el).backgroundImage.includes('svg'),
      })),
    );

    expect(selects.length).toBeGreaterThan(0);
    for (const sel of selects) {
      expect(sel.conFlecha).toBe(true);
    }
  });

  test('el login también tiene los campos blancos', async ({ page }) => {
    await page.goto('/');
    await page.locator('input[type="email"]').waitFor({ timeout: 20_000 });

    const fondos = await page.evaluate(() =>
      [...document.querySelectorAll('input, select, textarea')].map(
        (el) => getComputedStyle(el).backgroundColor,
      ),
    );

    expect(fondos.length).toBeGreaterThan(0);
    for (const fondo of fondos) {
      expect(fondo).toBe('rgb(255, 255, 255)');
    }
  });
});
