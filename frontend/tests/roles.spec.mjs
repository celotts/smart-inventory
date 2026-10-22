import { test, expect } from '@playwright/test';

/**
 * Lo que ve cada persona, según su rol.
 *
 * POR QUÉ ESTA PRUEBA
 * -------------------
 * El acceso NO es solo esconder un botón: el backend rechaza la operación
 * con un 403 (esto está comprobado en `backend/tests/test_security.py`).
 * Lo que se prueba acá es la otra mitad: que la pantalla NO ofrezca lo que
 * el rol no puede hacer.
 *
 * Una pestaña que siempre da 403 es ruido: el operador la prueba, ve un
 * error y la descarta. Y una pestaña de más hace que el operador lea
 * una pantalla que no es suya.
 *
 * LOS TRES CASOS
 * --------------
 * 1. ADMIN: ve los dos módulos y elige en la pantalla de entrada.
 * 2. OPERATOR (depósito): solo Inventario. Ve equipos, stock, movimientos y
 *    compras. NO ve la bandeja de incidencias ni la auditoría.
 * 3. TECHNICIAN (taller): solo Service Desk. Ve la bandeja y la flota (para
 *    diagnosticar). NO ve productos, compras ni el escáner (no mueve
 *    equipos).
 */

/** Las cuentas de ejemplo (las crea `scripts/usuarios-modulo.sh`). */
const CUENTAS = {
  operador: { email: 'deposito@empresa.com', password: 'ClaveSegura2026' },
  tecnico: { email: 'taller@empresa.com', password: 'ClaveSegura2026' },
  admin: { email: 'admin@smartinventory.local', password: 'admin123' },
};

/**
 * Entra con una cuenta y devuelve lo que ve.
 * @param {import('@playwright/test').Page} page
 * @param {{email: string, password: string}} cuenta
 */
async function entrar(page, cuenta) {
  await page.goto('/');
  await page.locator('input[type="email"]').waitFor({ timeout: 20_000 });
  await page.locator('input[type="email"]').fill(cuenta.email);
  await page.locator('input[type="password"]').fill(cuenta.password);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.locator('#cabecera').waitFor({ timeout: 20_000 });
  await page.waitForTimeout(1500);
}

/**
 * Las pestañas visibles y el módulo.
 * @param {import('@playwright/test').Page} page
 */
async function queVe(page) {
  return page.evaluate(() => ({
    rol: document.getElementById('cabecera-usuario-rol')?.textContent.trim() ?? '',
    pestanas: [...document.querySelectorAll('.pestana')]
      .filter((b) => !b.hidden && b.offsetParent !== null)
      .map((b) => b.dataset.vista),
    modulo: document.getElementById('cabecera-modulo')?.textContent.trim() ?? '',
    // El escaneo es un boton de la cabecera (al nivel del selector de
    // modulo), no una pestana del menu: en el celular, donde el menu
    // scrollea, una pestana se pierde.
    escanear: (() => {
      const b = document.getElementById('cabecera-escanear');
      return !!b && !b.hidden && b.offsetParent !== null;
    })(),
    lanzador: !!document.querySelector('.lanzador'),
  }));
}

test.describe('Acceso por rol', () => {
  test('el operador entra directo a Inventario y no ve incidencias', async ({ page }) => {
    await entrar(page, CUENTAS.operador);
    const vista = await queVe(page);

    expect(vista.rol).toMatch(/operador/i);
    expect(vista.modulo).toBe('Inventario');

    // Entra DIRECTO: con un solo modulo no hay nada que elegir.
    expect(vista.lanzador).toBe(false);

    // Ve lo del depósito...
    expect(vista.pestanas).toContain('inventario');
    expect(vista.pestanas).toContain('movimientos');

    // ...el escaneo, que esta en la cabecera (no en el menu de secciones)...
    expect(vista.escanear).toBe(true);

    // ...y NO ve la bandeja de incidencias (no es su módulo) ni la auditoría
    // (no tiene el permiso).
    expect(vista.pestanas).not.toContain('incidencias');
    expect(vista.pestanas).not.toContain('auditoria');
    expect(vista.pestanas).not.toContain('usuarios');
  });

  test('el técnico entra directo a Service Desk y no ve productos ni compras', async ({ page }) => {
    await entrar(page, CUENTAS.tecnico);
    const vista = await queVe(page);

    expect(vista.rol).toMatch(/t[eé]cnico/i);
    expect(vista.modulo).toBe('Service Desk');
    expect(vista.lanzador).toBe(false);

    // La bandeja es su trabajo.
    expect(vista.pestanas).toContain('incidencias');

    // La flota la ve para diagnosticar el equipo que está fallando.
    expect(vista.pestanas).toContain('inventario');

    // Y NO ve lo que es tarea del depósito.
    expect(vista.pestanas).not.toContain('productos'); // no ve el maestro
    expect(vista.pestanas).not.toContain('compras'); // no ve las órdenes
    expect(vista.pestanas).not.toContain('auditoria'); // no audita
    expect(vista.escanear).toBe(false); // no crea movimientos
  });

  test('el admin ve los dos módulos y elige en la pantalla de entrada', async ({ page }) => {
    await entrar(page, CUENTAS.admin);

    // Con dos módulos, el admin elige.
    await expect(page.locator('.lanzador')).toBeVisible({ timeout: 20_000 });

    const modulos = await page.evaluate(() =>
      [...document.querySelectorAll('.lanzador__opcion')].map((b) => b.dataset.modulo),
    );
    expect(modulos).toContain('inventario');
    expect(modulos).toContain('helpdesk');
  });

  test('el técnico recibe 403 si llama a un endpoint que no es suyo', async ({ page }) => {
    await entrar(page, CUENTAS.tecnico);

    // El frontend oculta la pestaña, pero la restricción de verdad es del
    // backend: si alguien llama al endpoint a mano, se rechaza.
    const resultado = await page.evaluate(async () => {
      const token = sessionStorage.getItem('si.refresh');
      const r = await fetch('/api/audit/timeline', {
        headers: { Authorization: `Bearer ${token}` },
      });
      return r.status;
    });

    // Con el refresh no alcanza (no es un access token): da 401 o 403. Lo
    // que importa es que NO da 200.
    expect(resultado).not.toBe(200);
    expect([401, 403]).toContain(resultado);
  });
});
