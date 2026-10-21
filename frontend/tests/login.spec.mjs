import { test, expect } from '@playwright/test';

/**
 * Pruebas del INICIO DE SESIÓN.
 *
 * POR QUÉ EL LOGIN PROPIO
 * -----------------------
 * El login es la puerta de entrada: si falla, no hay inventario que valga y
 * el operador se queda parado en la verja. Por eso tiene su propia suite, y
 * no una línea dentro de otra.
 *
 * Lo que se cubre:
 *
 *  1. Entrar con credenciales buenas.
 *  2. Credenciales malas: el error se ve, no se entra y no se filtra si el
 *     email existe o no (mismo mensaje para los dos casos).
 *  3. Campos vacíos: el formulario avisa sin pegarle a la API.
 *  4. El token NO queda en el DOM ni en el `localStorage` (si queda, un XSS
 *     se lleva la sesión; por eso vive solo en memoria).
 *  5. La sesión se renueva sola (refresh) sin volver a escribir la clave.
 *  6. Cerrar sesión deja la app como estaba: pantalla de login.
 *  7. El rol decide qué se ve después de entrar (la cabecera y las pestañas).
 */

/** Los usuarios de demo. La contrasena se lee del entorno si se cambia. */
const ADMIN = {
  email: process.env.E2E_ADMIN_EMAIL || 'admin@smartinventory.local',
  password: process.env.E2E_ADMIN_PASSWORD || 'admin123',
};

const OPERADOR = {
  email: process.env.E2E_OPERADOR_EMAIL || 'operador@empresa.com',
  password: process.env.E2E_OPERADOR_PASSWORD || 'ClaveSegura2026',
};

/**
 * Espera a que aparezca la pantalla de login.
 * @param {import('@playwright/test').Page} page
 */
async function irAlLogin(page) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
}

/**
 * Escribe las credenciales y las envía.
 *
 * Los selectores son por TIPO de control, no por etiqueta: el botón de la
 * cabecera tiene un `aria-label` que termina en "cambiar la contraseña", y
 * `getByLabel('Contraseña')` lo agarra también (el modo estricto de
 * Playwright lo detecta y falla, que es lo correcto).
 *
 * @param {import('@playwright/test').Page} page
 * @param {{email: string, password: string}} usuario
 */
async function enviarLogin(page, usuario) {
  await page.locator('input[type="email"]').fill(usuario.email);
  await page.locator('input[type="password"]').fill(usuario.password);
  await page.getByRole('button', { name: 'Entrar' }).click();
}

test.describe('Login', () => {
  test('entra con credenciales válidas y muestra la app', async ({ page }) => {
    await irAlLogin(page);

    await enviarLogin(page, ADMIN);

    // La cabecera aparece: es la señal de que la sesión se abrió.
    const cabecera = page.locator('#cabecera');
    await expect(cabecera).toBeVisible({ timeout: 20_000 });

    // Y dice quién entró: el bloque de usuario muestra el ROL (el nombre
    // completo está en "Mi cuenta", no en la cabecera).
    await expect(page.locator('#cabecera-usuario-rol')).toContainText(/Rol:/i);

    // El formulario de login desaparece.
    await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toHaveCount(0);

    // La vista de inicio cargó contenido real, no un spinner eterno.
    await expect(page.locator('#contenido')).not.toBeEmpty();
  });

  test('rechaza una contraseña incorrecta y no entra', async ({ page }) => {
    await irAlLogin(page);

    await enviarLogin(page, { email: ADMIN.email, password: 'clave-inventada-123' });

    // Se ve el error y se sigue en el login.
    //
    // El selector es por TEXTO y no por `[role="alert"]`: los campos del
    // formulario tambien son `role="alert"` (el lector de pantalla los
    // anuncia), asi que el primero de la lista es el del email, que esta
    // oculto. El error general es el unico con este texto.
    const error = page.getByText(/incorrect/i).first();
    await expect(error).toBeVisible();
    await expect(error).toContainText(/incorrect/i);

    // La cabecera (la app) NO aparece.
    await expect(page.locator('#cabecera')).toBeHidden();

    // Y el botón vuelve a estar usable: se puede reintentar.
    await expect(page.getByRole('button', { name: 'Entrar' })).toBeEnabled();
  });

  test('el mensaje es el mismo exista el email o no', async ({ page }) => {
    await irAlLogin(page);

    await enviarLogin(page, { email: ADMIN.email, password: 'clave-inventada-123' });
    const conEmail = (await page.getByText(/incorrect/i).first().textContent())?.trim();

    await enviarLogin(page, { email: 'nadie@estaempresa.com', password: 'clave-inventada-123' });
    const sinEmail = (await page.getByText(/incorrect/i).first().textContent())?.trim();

    // Distinguir "el email no existe" de "la clave está mal" permite
    // enumerar qué usuarios tiene el sistema.
    expect(conEmail).toBe(sinEmail);
  });

  test('no deja entrar con los campos vacíos', async ({ page }) => {
    await irAlLogin(page);

    let llamadas = 0;
    page.on('request', (req) => {
      if (req.url().includes('/api/auth/login')) llamadas += 1;
    });

    await page.getByRole('button', { name: 'Entrar' }).click();

    // Avisa del email vacío.
    await expect(page.getByText(/email/i).first()).toBeVisible();
    await expect(page.locator('#cabecera')).toBeHidden();

    // Y no pegó contra la API: la validación es del formulario.
    await page.waitForTimeout(1000);
    expect(llamadas).toBe(0);
  });

  test('el access token no queda en el DOM ni en ningun storage', async ({ page }) => {
    await irAlLogin(page);
    await enviarLogin(page, ADMIN);
    await expect(page.locator('#cabecera')).toBeVisible({ timeout: 20_000 });

    /**
     * Lee el "type" de un JWT (el payload va en base64).
     * @param {string} token
     * @returns {string|null}
     */
    const tipoDe = (token) => {
      try {
        const partes = String(token).split('.');
        if (partes.length < 2) return null;
        const payload = JSON.parse(
          atob(partes[1].replace(/-/g, '+').replace(/_/g, '/')),
        );
        return payload.type ?? null;
      } catch {
        return null;
      }
    };

    // 1. En el DOM no hay ningun JWT.
    const html = await page.content();
    expect(html).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/);

    // 2. En el `localStorage` no hay ningun token: ese storage sobrevive a
    //    cerrar el navegador, y ahi un token es un token robado.
    const local = await page.evaluate(() => ({ ...localStorage }));
    for (const valor of Object.values(local)) {
      expect(tipoDe(valor)).toBeNull();
    }

    // 3. En el `sessionStorage` solo puede estar el REFRESH token (que es lo
    //    unico que la app guarda, y muere con la pestana). El ACCESS token
    //    vive solo en memoria: si aparece aqui, la sesion sobrevive a cerrar
    //    la pestana.
    const sesion = await page.evaluate(() => ({ ...sessionStorage }));
    const tipos = Object.values(sesion).map(tipoDe);
    expect(tipos.filter((t) => t === 'access')).toHaveLength(0);
    expect(tipos.filter((t) => t === 'refresh').length).toBeLessThanOrEqual(1);
  });

  test('renueva la sesión sin volver a escribir la clave', async ({ page }) => {
    await irAlLogin(page);
    await enviarLogin(page, ADMIN);
    await expect(page.locator('#cabecera')).toBeVisible({ timeout: 20_000 });

    // Se fuerza el refresh: el backend rota el refresh token y devuelve un
    // access nuevo.
    const renovado = await page.evaluate(async () => {
      const respuesta = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          refresh_token: sessionStorage.getItem('si.refresh'),
        }),
      });
      return { status: respuesta.status, ok: respuesta.ok };
    });

    expect(renovado.status).toBe(200);
    expect(renovado.ok).toBe(true);
  });

  test('cerrar sesión devuelve a la pantalla de login', async ({ page }) => {
    await irAlLogin(page);
    await enviarLogin(page, ADMIN);
    await expect(page.locator('#cabecera')).toBeVisible({ timeout: 20_000 });

    // El botón de salir PIDE CONFIRMACIÓN: no cierra la sesión de un clic
    // sin preguntar (un clic de más en un depot no puede desloguear a
    // alguien).
    await page.locator('#btn-salir').click();
    await page.getByRole('button', { name: 'Cerrar sesión' }).click();

    await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.locator('#cabecera')).toBeHidden();

    // Y el refresh token se fue con la sesion.
    const refresh = await page.evaluate(() => sessionStorage.getItem('si.refresh'));
    expect(refresh).toBeNull();
  });

  test('cerrar sesión no muestra "sesión expirada" después', async ({ page }) => {
    await irAlLogin(page);
    await enviarLogin(page, ADMIN);
    await expect(page.locator('#cabecera')).toBeVisible({ timeout: 20_000 });

    // El botón de salir pide confirmación.
    await page.locator('#btn-salir').click();
    await page.getByRole('button', { name: 'Cerrar sesión' }).click();

    await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible({
      timeout: 20_000,
    });

    // El cartel de "Sesión expirada" NO puede aparecer.
    //
    // El bug: la campana seguía preguntando el conteo de avisos cada 30
    // segundos con la sesión ya cerrada. Esa respuesta 401 la tomaba
    // `api.js` como "tu sesión venció" y abría el modal encima del login.
    // El operador leía "sesión expirada" después de haberla cerrado él.
    //
    // Se espera un poco más que el intervalo de la campana (30 s) para que
    // el pollinghubiera ocurrido.
    await page.waitForTimeout(35_000);

    await expect(page.getByText('Sesión expirada')).toHaveCount(0);
    await expect(page.getByText(/Tu sesión ha expirado/)).toHaveCount(0);
    await expect(page.locator('.modal--login')).toHaveCount(0);

    // Y la pantalla de login sigue ahí, sin cartel encima.
    await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
  });

  test('el rol del operador deja el inventario y oculta la auditoría', async ({ page }) => {
    await irAlLogin(page);
    await enviarLogin(page, OPERADOR);

    await expect(page.locator('#cabecera')).toBeVisible({ timeout: 20_000 });

    // El rol se muestra en la cabecera.
    await expect(page.locator('#cabecera-usuario-rol')).toContainText(/operador/i);

    // Las pestañas que el operador NO puede usar siguen ocultas.
    // (La pestaña de auditoría es solo de administración.)
    await expect(page.locator('#pestana-auditoria')).toBeHidden();
  });
});
