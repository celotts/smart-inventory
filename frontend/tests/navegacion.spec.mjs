import { test, expect } from '@playwright/test';

/**
 * La navegación: el botón "atrás" del celular y las pestañas con nombre.
 *
 * POR QUÉ ESTA PRUEBA
 * -------------------
 * 1. EL BOTÓN ATRÁS. Cada vista deja su marca en el historial del
 *    navegador. Sin escuchar el `popstate`, el botón atrás del celular
 *    cambia la URL y NO cambia la pantalla: el operador queda mirando la
 *    misma vista con una URL que dice otra cosa, y la app parece no andar.
 *    En el escritorio casi nadie lo nota; en el celular es el gesto que
 *    todos usan.
 *
 * 2. EL NOMBRE DE LAS PESTAÑAS. En el celular la pastilla es solo un
 *    icono: sin `title`, no hay forma de saber qué dice.
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

  const modulo = page.locator('.lanzador__opcion[data-modulo="inventario"]');
  if (await modulo.count()) await modulo.click({ force: true });
  await page.waitForTimeout(1200);
}

test.describe('Navegación', () => {
  test('el botón atrás del celular vuelve a la vista anterior', async ({ page }) => {
    await entrar(page);

    // Dos vistas, una después de otra.
    await page.locator('#pestana-inventario').click({ force: true });
    await page.waitForTimeout(1000);
    expect(await page.evaluate(() => location.hash)).toBe('#inventario');

    await page.locator('#pestana-existencia').click({ force: true });
    await page.waitForTimeout(1000);
    expect(await page.evaluate(() => location.hash)).toBe('#existencia');

    // El botón atrás: cambia la URL Y la pantalla.
    await page.goBack();
    await page.waitForTimeout(1000);
    expect(await page.evaluate(() => location.hash)).toBe('#inventario');
    expect(
      await page.evaluate(() => document.querySelector('#pestana-inventario')?.getAttribute('aria-current')),
    ).toBe('page');
  });

  test('cada pestaña del menú tiene su nombre (en el celular son solo iconos)', async ({ page }) => {
    await entrar(page);

    const pestanas = await page.evaluate(() =>
      [...document.querySelectorAll('.pestana')].map((b) => ({
        vista: b.dataset.vista,
        title: b.getAttribute('title'),
        texto: b.textContent.trim(),
      })),
    );

    expect(pestanas.length).toBeGreaterThan(0);
    for (const pestana of pestanas) {
      expect(pestana.title, `La pestaña ${pestana.vista} no tiene title`).toBeTruthy();
      expect(pestana.title).toBe(pestana.texto);
    }
  });

  test('el menú de secciones no pierde ninguna pestaña en el celular', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await entrar(page);

    const medidas = await page.evaluate(() => {
      const ancho = window.innerWidth;
      const nav = document.querySelector('.cabecera__nav');
      const pestanas = [...document.querySelectorAll('.pestana')].filter(
        (b) => !b.hidden && b.offsetParent !== null,
      );
      return {
        dentro: pestanas.filter((b) => {
          const r = b.getBoundingClientRect();
          return r.left >= -0.5 && r.right <= ancho + 0.5;
        }).length,
        total: pestanas.length,
        scrollHorizontal: document.documentElement.scrollWidth > ancho + 1,
        navOverflow: nav.scrollWidth > nav.clientWidth + 2,
      };
    });

    // Todas las pestañas se ven, sin scrollear y sin salirse.
    expect(medidas.dentro).toBe(medidas.total);
    expect(medidas.total).toBeGreaterThan(4);
    expect(medidas.scrollHorizontal).toBe(false);
  });
});
