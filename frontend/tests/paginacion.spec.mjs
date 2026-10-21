import { test, expect } from '@playwright/test';

/**
 * La paginación de las tablas.
 *
 * POR QUÉ ESTA PRUEBA
 * -------------------
 * La paginación se copió a mano en el inventario y en la auditoría, y las
 * dos copias hacian cosas distintas: una dejaba cambiar la cantidad por
 * página y la otra no; una mostraba el total y la otra no. Con 5000
 * registros la diferencia no es estética: en una pantalla el operador cree
 * que vio todo cuando vio 50 de 5000.
 *
 * Estas pruebas fijan las TRES cosas que hacen que una tabla sea navegable:
 *
 *  1. Que se vea el TOTAL (sin él no hay forma de saber si la lista está
 *     completa).
 *  2. Que se pueda cambiar la cantidad por página.
 *  3. Que esa cantidad se RECUERDE entre pantallas (es una preferencia de la
 *     persona, como el idioma).
 */

test.describe('Paginación', () => {
  test('el inventario muestra el total y deja cambiar la cantidad por página', async ({ page }) => {
    await page.goto('/');
    await page.locator('input[type="email"]').waitFor({ timeout: 20_000 });
    await page.locator('input[type="email"]').fill('admin@smartinventory.local');
    await page.locator('input[type="password"]').fill('admin123');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.locator('#cabecera').waitFor({ timeout: 20_000 });
    await page.waitForTimeout(1500);

    // El lanzador: elegir el módulo de inventario.
    const modulo = page.locator('.lanzador__opcion').first();
    if (await modulo.count()) {
      await modulo.click({ force: true });
      await page.waitForTimeout(1500);
    }

    await page.locator('[data-vista="inventario"]').first().click({ force: true });
    await page.waitForTimeout(2500);

    const barra = page.locator('.paginacion').first();
    await expect(barra).toBeVisible();

    // El TOTAL: el texto dice cuantas paginas hay y cuantos registros hay.
    const info = await barra.locator('.paginacion__info').textContent();
    expect(info).toMatch(/P[aá]gina \d+ de \d+/);
    expect(info).toMatch(/\d+.*\d+ (registros|componentes|equipos)/);

    // El selector de la cantidad por página.
    const selector = barra.locator('.paginacion__cantidad');
    await expect(selector).toBeVisible();
    const opciones = await selector.locator('option').allTextContents();
    expect(opciones.length).toBeGreaterThanOrEqual(3);
  });

  test('la cantidad por página se recuerda entre pantallas', async ({ page }) => {
    await page.goto('/');
    await page.locator('input[type="email"]').waitFor({ timeout: 20_000 });
    await page.locator('input[type="email"]').fill('admin@smartinventory.local');
    await page.locator('input[type="password"]').fill('admin123');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.locator('#cabecera').waitFor({ timeout: 20_000 });
    await page.waitForTimeout(1500);

    const modulo = page.locator('.lanzador__opcion').first();
    if (await modulo.count()) {
      await modulo.click({ force: true });
      await page.waitForTimeout(1500);
    }

    await page.locator('[data-vista="inventario"]').first().click({ force: true });
    await page.waitForTimeout(2500);

    // Elegir 50 por página.
    const selector = page.locator('.paginacion__cantidad').first();
    await selector.selectOption('50');
    await page.waitForTimeout(2500);

    // Queda guardada.
    const guardada = await page.evaluate(() => localStorage.getItem('si.porPagina'));
    expect(guardada).toBe('50');

    // Y al cambiar de pantalla, la otra pantalla arranca con la misma.
    await page.locator('[data-vista="productos"]').first().click({ force: true });
    await page.waitForTimeout(2500);

    const valor = await page.locator('.paginacion__cantidad').first().inputValue();
    expect(valor).toBe('50');
  });

  test('toda tabla con muchos registros tiene paginación', async ({ page }) => {
    await page.goto('/');
    await page.locator('input[type="email"]').waitFor({ timeout: 20_000 });
    await page.locator('input[type="email"]').fill('admin@smartinventory.local');
    await page.locator('input[type="password"]').fill('admin123');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.locator('#cabecera').waitFor({ timeout: 20_000 });
    await page.waitForTimeout(1500);

    const modulo = page.locator('.lanzador__opcion').first();
    if (await modulo.count()) {
      await modulo.click({ force: true });
      await page.waitForTimeout(1500);
    }

    // Las pantallas que muestran una tabla de registros.
    const vistas = ['inventario', 'productos', 'movimientos', 'usuarios'];

    for (const vista of vistas) {
      const pestana = page.locator(`[data-vista="${vista}"]`).first();
      if (!(await pestana.count()) || !(await pestana.isVisible())) continue;

      await pestana.click({ force: true });
      await page.waitForTimeout(2500);

      // Si la pantalla tiene una tabla, tiene la barra de paginacion.
      const hayTabla = await page.locator('#contenido table').count();
      if (hayTabla === 0) continue;

      await expect(
        page.locator('#contenido .paginacion').first(),
        `La pantalla "${vista}" muestra una tabla sin paginación`,
      ).toBeVisible();
    }
  });
});
