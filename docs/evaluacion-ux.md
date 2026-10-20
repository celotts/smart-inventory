# Evaluación de UX/UI, accesibilidad y seguridad del front

Auditoría del front de `smart-inventory` contra las buenas prácticas de
diseño de interfaces, accesibilidad (WCAG 2.2 AA) y seguridad de aplicaciones
web. Fecha: 2026-10-20. Rama: `feature/smart-local`.

**Veredicto de una línea**: el diseño visual y la arquitectura de seguridad
del front ya estaban **muy por encima de la media** —sistema de tokens con
contrastes documentados, tipografía fluida, modo oscuro, foco visible,
`prefers-reduced-motion`, objetivos táctiles de 44 px, PWA, CSP estricta sin
CDN, cero `innerHTML`—. Lo que faltaba no era el diseño: eran las
**descripciones** (tooltips de verdad, no `title` nativo), la **validación de
formularios** (los errores iban en un cartel que se borra a los 4 segundos) y
**un agujero de seguridad que nadie estaba mirando** (nginx pierde las
cabeceras de seguridad en los `location` con `add_header`).

---

## 1. Lo que ya estaba bien (y no se tocó)

| Área | Estado |
|---|---|
| Sistema de diseño | Tokens de espaciado (base 4), escala tipográfica fluida, radio, sombras, paleta con **contrastes anotados** (AAA en texto secundario, AA en el resto) |
| Modo oscuro | Por tokens, con `data-theme`, sin duplicar reglas |
| Objetivos táctiles | 44 px mínimo (`--toque`),Buttons e inputs |
| Tipografía en inputs | 16 px: por debajo, iOS hace zoom al enfocar y descuadra la página |
| Foco | `:focus-visible` con anillo, y foco atrapado en los modales |
| Movimiento | `@media (prefers-reduced-motion: reduce)` respetado |
| Lighthouse / PWA | Manifest, service worker, instalable, sin CDN |
| XSS | **Cero** `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `eval`, `new Function`, `document.write` en todo el front |
| Tokens | Access token **solo en memoria**; refresh token en `sessionStorage`, nunca en `localStorage` |
| CSP | `script-src 'self'` sin `unsafe-inline` ni `unsafe-eval` |
| Semántica | `lang`, `viewport-fit=cover`, `theme-color`, `referrer`, skip link, landmarks, `aria-live` en los avisos |

---

## 2. Lo que se corrigió en esta ronda

### 2.1 Un agujero de seguridad real: nginx perdía las cabeceras

**El hallazgo más importante de la auditoría.**

En nginx, `add_header` **no se hereda**: si un `location` define *cualquier*
`add_header`, pierde **todos** los del nivel superior. El `nginx.conf` de
este proyecto definía la CSP y las cabeceras de seguridad en el bloque
`server`, y después definía `add_header Cache-Control` en cinco `location`.

Consecuencia: la página principal (`/` y `/index.html`) se servía **sin**:

- `X-Frame-Options: DENY` → la app se podía embeber en un iframe (clickjacking).
- `Permissions-Policy` → la cámara y la geolocalización quedaban permitidas
  por defecto del navegador, y no por política de la app.
- `Referrer-Policy` → la URL de la app podía viajar a terceros.
- `Service-Worker-Allowed`.

Verificado antes y después:

```
ANTES  /            → CSP, X-Content-Type-Options          (faltaban 4 cabeceras)
ANTES  /index.html  → CSP, X-Content-Type-Options          (faltaban 4 cabeceras)
ANTES  /sw.js       → Service-Worker-Allowed               (faltaban 6)
ANTES  /manifest…   → (nada)

AHORA  todas        → CSP + nosniff + DENY + Referrer + Permissions + COOP + CORP + SW
```

**El arreglo**: las cabeceras viven en `frontend/security-headers.conf` y se
`include` en cada bloque que define `add_header`. Con un `include` no hay
forma de olvidarse uno, y agregar un `location` nuevo obliga a pensarlo.

Además se:
- **Sacaron `fonts.googleapis.com` y `fonts.gstatic.com` de la CSP**: la app
  no carga ninguna fuente externa (todo el CSS es propio). Una política de
  seguridad con permisos que nadie usa es una puerta abierta.
- **Agregaron `Cross-Origin-Opener-Policy: same-origin` y
  `Cross-Origin-Resource-Policy: same-origin`**: la app queda en su propio
  grupo de navegación (nadie la puede embeber) y sus recursos no se
  comparten.
- **Anuló `window.opener`** en la ventana de impresión: sin eso, esa ventana
  podía navegar la pestaña de la app.

### 2.2 Un error de arranque invisible

Un `ReferenceError` en un módulo (un nombre mal escrito) **aborta el arranque
de la app entera**: no se pinta ninguna pantalla y el operador ve "Cargando…"
para siempre. El único rastro estaba en la consola del navegador, que el
operador no abre y que se pierde al reiniciar el celular.

**El arreglo**: `arranque.js` (que se carga antes que `main.js`) captura el
error y **lo muestra en la pantalla**: "Detalle del error: element is not
defined". El soporte ya no necesita el celular del operador para diagnosticar.

### 2.3 Formularios: el error iba en un cartel que se borra

Un formulario que avisa el error en un cartel flotante de 4 segundos deja al
operador con la duda de **qué** campo está mal. Peor si escribió 12 campos:
tiene que recorrerlos todos adivinando.

**Ahora** (`frontend/src/formulario.js`):

1. El error va **debajo del campo**, junto a lo que está mal.
2. El control queda marcado con `aria-invalid="true"` y **borde rojo**.
3. Hay un **resumen arriba** del formulario: "Faltan corregir 3 campos", con
   enlaces que llevan el foco al campo.
4. El **foco salta al primer problema**.
5. El error se **anuncia** (`role="alert"`) y se enlaza con
   `aria-describedby`, así que el lector de pantalla lo lee al enfocar.
6. El error **desaparece solo** cuando el operador corrige el campo.

Aplicado a: login, alta de equipo, alta y edición de producto. El resto de
los formularios (compras, movimientos) tienen ya la infraestructura
(`errorDe()`) para migrarlos sin reescribirlos.

Además: el **medidor de fortaleza de contraseña** se sacó del login. En el
*ingreso* la clave ya existe, su fortaleza no es decisión del operador, y
mostrarla le dice "tu clave es débil" cuando lo único que está haciendo es
entrar. El medidor tiene sentido al **crear** una clave.

### 2.4 Tooltips de verdad

El `title` nativo de HTML no sirve para esto:

- No aparece en un celular (iOS y Android lo ignoran al tocar).
- Tarda más de un segundo y no se puede controlar.
- No se muestra al navegar con el teclado, que es como operan los lectores de
  pantalla.
- Aparece fuera de la página, con su propio estilo, que no sigue el tema.

**Ahora** (`frontend/src/tooltip.js`): un tooltip propio que se ve al pasar
el mouse **y** al enfocar con el teclado, funciona con el toque, se posiciona
dentro de la ventana (nunca cortado) y **no se superpone** con el tooltip
nativo.

La regla de oro: **un tooltip nunca es la única forma de saber qué hace un
control**. El nombre accesible va en `aria-label` o en el texto visible; el
tooltip es la *descripción*, y por eso va en `aria-describedby` **permanente**
(contra un texto oculto con `sr-only`), no en el tooltip visual: un lector de
pantalla tiene que poder leer la explicación sin que nadie pase el mouse.

Aplicado a: los 8 KPIs del tablero (qué mide cada número), las insignias de
estado, y los dos botones de solo icono de la cabecera.

### 2.5 El escáner: la cámara se apagaba y el resultado se borraba

**Lo que pasaba**: al leer un código, el lector se detenía (por diseño: "para
no leer diez veces la misma etiqueta"), la cámara se apagaba y el operador
tenía que apretar "Encender cámara" para cada etiqueta. El resultado se
mostraba en un panel que **se borraba solo a los 8 segundos**.

En un depósito eso es un problema de piso: un conteo de 40 equipos son 40
toques de más, y el operador termina escaneando con el ingreso manual, que es
más lento. Y al final del conteo no quedaba **ningún comprobante** de lo que
se había escaneado: el último escaneo tapaba al anterior, y a los 8 segundos
no quedaba nada.

**Ahora**:

- **Escaneo continuo**: la cámara sigue encendida y el operador sigue
  escaneando. El lector (`escaner.js`) tiene un modo `continuo` con un
  filtro de **3 segundos por código**: la etiqueta sigue en el encuadre y el
  detector la vuelve a leer ~30 veces por segundo, y sin ese filtro un solo
  barrido registraría el mismo equipo treinta veces. El filtro es *por
  valor*: una etiqueta nueva se registra al instante.
- **Cola de escaneos**: el operador puede pasar el celu sobre tres cajas en un
  segundo. Antes, los dos últimos se perdían en silencio y el conteo quedaba
  corto sin que nadie supiera por qué. Ahora se registran en orden.
- **Recibo de la sesión**: cada escaneo queda con su hora, su código, su
  estado y su mensaje, y **se acumulan** (el más nuevo arriba). Es el
  comprobante del turno, con un botón "Limpiar".
- **Los errores no cortan el escaneo**: una etiqueta desconocida ya no abre un
  modal (que tapaba la pantalla e impedía seguir escaneando); va al recibo en
  rojo y un aviso flotante lo anuncia.
- El "Registrando…" se muestra **en la tarjeta de la cámara**, no en la zona
  de resultado: reemplazarlo borraría el recibo.

El ingreso manual pasó también por la cola, para que no se pise con un
escaneo de cámara.

### 2.6 Otros detalles de profesionalismo

- **El fondo no se mueve con un modal abierto**: el `overflow: hidden` en el
  `body` bloquea el scroll de atrás, compensando el ancho de la barra para
  que la página no salte unos píxeles.
- **Foco visible en todos los controles**, no solo en los que el navegador
  decide (`:focus-visible` con anillo propio).
- **Las tarjetas del tablero son enfocables** (`tabindex="0"`): llevan una
  descripción, y sin esto el teclado no puede llegar a ellas.

---

## 3. Lo que queda pendiente

| # | Qué | Por qué importa | Esfuerzo |
|---|---|---|---|
| 1 | **Pantalla de usuarios y roles** | Hoy la API permite crear usuarios y roles, pero el front no tiene pantalla. Un admin **no puede revocar el acceso** de alguien desde la app: tiene que pedirlo por mail. Es el hueco de seguridad mas grande que queda | Alto |
| 2 | **Navegación en celular** | 9 pestañas que envuelven en dos filas en un teléfono. Una barra inferior con las 4 principales + "más" es lo que espera un usuario de app | Medio |
| 3 | **Linterna de accesibilidad en CI** (eslint-plugin-jsx-a11y o similar) | Sin dependencias, no: un chequeo propio de las reglas basicas | Medio |
| 4 | **Migrar compras y movimientos al helper de formularios** | La infraestructura esta; los tooltips ya estan, falta la validacion en linea | Bajo |
| 5 | **Imprimir el recibo de la sesion** | El recibo se exporta a CSV, pero no se imprime en un formato de etiqueta | Bajo |

### 2.7 Tooltips en los botones y en los campos

**Los botones**: todos los de la vista de productos (30 de 30) tienen tooltip
con el contexto de la accion ("Descontinuar: deja de venderse el producto sin
borrarlo, el historial se conserva"), y tambien los de la cabecera, el
inventario, los movimientos, las compras y el escaner.

**Los campos de texto**: el tooltip va en la **etiqueta**, no en el control.
Un tooltip en un `<input>` desaparece en cuanto el operador hace clic (esta
escribiendo, y lo que necesita es leer la ayuda sin perder el foco), y en un
celular el tooltip nativo no aparece nunca. La etiqueta es lo que el operador
mira ANTES de hacer clic.

Y lo que de verdad importa —como se usa el campo— quedo **siempre visible**,
debajo, y enlazado con `aria-describedby`.

### 2.8 Aviso de cambios sin guardar

Cerrar un modal de edicion con diez campos escritos los perdia en silencio.
Ahora `abrirDialogo` acepta `avanzarAlCerrar: true` y pregunta antes, con un
modal propio (no un `confirm()` del navegador, que rompe el tema y el foco).

**Un bug que encontre probandolo**: `cerrar` se pasa directo como manejador de
un boton, y `addEventListener` le pasa el **evento** como primer argumento. Un
evento es truthy, asi que el `if (forzar)` normal lo tomaba como un "si,
descarta todo" y **el aviso nunca aparecia**. Ahora solo un `true` explicito
descarta.

### 2.9 El borde de los campos: 1.59:1 (WCAG pide 3:1)

El test automatico de contraste (abajo) fallo en el primer intento: el borde
de los campos de texto tenia **1.59:1** contra la superficie, en los dos
temas. WCAG 1.4.11 pide 3:1 para el borde de un control: es lo unico que dice
"escribi aca".

El arreglo no fue oscurecer todos los bordes (el de una tarjeta puede ser
sutil), sino un token aparte, `--color-border-control`, solo para los campos.

### 2.10 Test automatico de contraste

`backend/tests/test_contraste.py` lee el CSS real, calcula el contraste de
cada par de tokens que la app usa y falla si alguno baja del minimo de
WCAG 2.2 AA. Cubre los tres temas (claro, oscuro y modo exterior).

Los contrastes estaban anotados a mano en los tokens, que es una buena
practica... hasta que el proximo que cambie un color deja el comentario
mintiendo. Un test no se rompe solo.

### 2.11 El recibo de escaneos se exporta

El recibo de la sesion se descarga en CSV: hora, codigo, serial, equipo,
ubicacion, estado y resultado. En un conteo de deposito, el papel es el
comprobante que pide el responsable.

### 2.12 `aria-live` en la lista que cambia

La lista del inventario se actualiza con cada tecla del buscador, y un lector
de pantalla no se enteraba. Ahora la zona de resultados es `aria-live="polite"`
(polite, no assertive: el cambio no interrumpe lo que el operador esta
haciendo) y hay un contador de resultados visibles.

---

## 4. Sobre los campos de fecha (pregunta del usuario)

**Sí: un campo de fecha debe ser un datepicker, y ya lo es.**

La app usa el **datepicker nativo** del navegador (`<input type="date">`):

- En celular abre el calendario del sistema operativo, con el teclado
  numérico y la rueda nativa.
- En escritorio abre el calendario del navegador.
- Es **accesible** (teclado, lector de pantalla) y **gratis**: cero
  dependencias, que es la regla del proyecto.

Dónde está aplicado:

| Campo | Vista | Tipo |
|---|---|---|
| Fecha de instalación | Ficha del componente | `type="date"` |
| Entrega esperada | Compras | `type="date"` |
| Desde / hasta | Auditoría | `type="date"` |
| Atributos de tipo `date` (caducidad, etc.) | Productos | `type="date"` |

**Lo que NO conviene**: una librería de datepicker propia. Serían ~40 KB de
JavaScript de terceros para reemplazar algo que el sistema operativo ya hace
mejor, y el proyecto tiene la regla de no tener cadena de suministro que
auditar.

**Lo que sí se puede mejorar** (pendiente): el `input type="date"` muestra
`AAAA-MM-DD` (formato ISO), que un operador español lee como
"2026-03-31" y no como "31/03/2026". El formato nativo lo decide el
navegador y no se puede cambiar sin romper la validación; la alternativa es
un `<input type="text">` con un parser propio, que es **peor**: se pierde el
calendario, la validación y la accesibilidad.

---

## 5. Cómo se verificó

- **Navegador real** (Chromium headless): login, dashboard, alta de equipo
  con validación, tooltip con mouse y con teclado, escaneo de código, descarga
  de CSV. Sin errores de consola y sin peticiones fallidas.
- **Cabeceras**: verificadas ruta por ruta con `curl -I` sobre el contenedor
  real (no sobre el archivo de configuración).
- **Tests del backend**: 184 pasan (los de e2e contra la base de test real).
- **Sintaxis de todos los módulos del front** verificada con `node --check`.
