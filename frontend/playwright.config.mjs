import { defineConfig, devices } from '@playwright/test';

/**
 * Configuracion de las pruebas de interfaz (Playwright).
 *
 * QUE PRUEBA
 * ----------
 * El login, que es la puerta de entrada de la app: si el login falla, no
 * hay inventario que valga. Y de paso las dos cosas de las que el login
 * depende: que el token NO quede en el DOM ni en el `localStorage`, y que la
 * sesion se pueda renovar sin volver a escribir la clave.
 *
 * DONDE CORRE
 * -----------
 * Contra el front de Docker (`http://localhost:5175`), que es como corre de
 * verdad: la app servida por nginx con las cabeceras de seguridad y el
 * service worker puestos. Probar contra el servidor de desarrollo seria
 * probar otra cosa.
 *
 * `make test-e2e` levanta el stack si hace falta.
 */
export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  // Uno a uno: los usuarios de demo son los mismos y las sesiones se pisan.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.FRONT_URL || 'http://localhost:5175',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
