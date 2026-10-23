# Gestor de Activos

Control de activos para empresas: inventario de dispositivos con escáner de
códigos, incidencias con SLA y mantenimiento, con IA integrada para análisis y
respuesta de preguntas en lenguaje natural.

```bash
make up-dev          # levanta todo (con IA apuntando a tu Ollama local)
make ai-pull         # descarga el modelo: paso único, tarda minutos
make ai-test         # prueba los 4 endpoints
```

- Front: <http://localhost:5175>  (app de escaneo, instalable)
- API: <http://localhost:5002> · Docs: <http://localhost:5002/docs>

> **La cámara necesita contexto seguro.** El navegador solo da acceso a la
> cámara en HTTPS o en `localhost`. Con la app en `http://localhost:5175`
> funciona; para abrirla desde un celular en la red local hay que servir el
> front por HTTPS, o usar el ingreso manual, que está siempre disponible.

---

## Escaneo de códigos

La app del front lee **QR y códigos de barras** con la API nativa del
navegador (`BarcodeDetector`), sin librerías externas. Funciona en Chrome, Edge
y Safari 17+; donde no está, hay ingreso manual del código.

| Endpoint | Qué hace |
| --- | --- |
| `GET /api/devices/resolve?codigo=` | Resuelve un QR o serial a un componente. Nunca da 404: devuelve `encontrado` y un motivo accionable |
| `POST /api/movements/escanear` | Registra un escaneo. **Idempotente** por `client_scan_id` |
| `POST /api/movements/escanear-lote` | Conteo cíclico de depósito, elemento por elemento |
| `GET /api/movements/dispositivo/{id}` | "Dónde estuvo": recorrido con hora, sede y quién lo registró |

### Idempotencia

El cliente genera un `client_scan_id` (UUID) **antes** de enviar y lo reutiliza
en los reintentos. Si la misma petición llega dos veces, la segunda no inserta
nada y devuelve el movimiento original con `replay: true`.

No se usa un índice único sobre `(device_id, fecha)`: dos escaneos **reales**
del mismo componente en el mismo segundo son legítimos —uno entra al depósito y
al segundo sale hacia un cliente— y rechazarlos sería peor que la duplicación
que se quiere evitar.

### Geo-validación

Si el escaneo trae coordenadas, el servidor **recalcula** la distancia contra
`locations.latitude/longitude` con haversine y la compara con
`tolerance_radius_meters`. Las coordenadas del cliente no se confían.

`is_valid_location` queda **NULL cuando no hay GPS**, no `FALSE`: "no estabas
ahí" y "no se puede saber" son cosas distintas. Un `FALSE` sin coordenadas
convierte cada escaneo de un móvil sin GPS en una inspección, y el operador
deja de mirar la columna.

### Rotación y máquina de estados

`devices.current_status` solo puede pasar por transiciones válidas (depósito →
tránsito → instalado → taller), y la regla vive en **un trigger de Postgres**,
no en Python: escriben el estado cuatro caminos distintos —el escáner,
`/api/ops`, una migración, un `psql`— y la regla tiene que estar en los cuatro.

- `PERMANENTLY_DISPOSED` es **absorbente**: nada sale de una baja.
- Entrar a `IN_REPAIR` exige una reparación abierta; salir, que esté cerrada.
- `installation_date` la mantiene el trigger: "instalado desde" es lo que hace
  legible el desgaste.

---

## Qué hay adentro

| Parte | Qué es |
| --- | --- |
| `database/init.sql` | Esquema completo: tablas, índices, CHECKs, triggers |
| `database/migrations/` | Migraciones para bases que ya existen |
| `backend/src/core/llm/` | Proveedores de IA (Ollama, OpenAI) tras una interfaz común |
| `backend/src/modules/ai/` | Preguntas en lenguaje natural, análisis y reportes |
| `backend/src/modules/devices/` | Alta, resolución de código y ficha de componente |
| `backend/src/modules/movements/` | Escaneo, idempotencia y geo-validación |
| `frontend/` | PWA de escaneo: módulos ES nativos, sin build y sin CDN |

---

## La IA

Cuatro endpoints, todos bajo `/api/ai`:

| Endpoint | Qué hace |
| --- | --- |
| `POST /api/ai/preguntar` | Pregunta en lenguaje natural → SQL → datos → explicación |
| `GET /api/ai/analisis/dispositivo/{id}` | Clasificación y puntaje de riesgo de un equipo |
| `POST /api/ai/reporte` | Reporte operativo en prosa, con cifras de Postgres |
| `GET /api/ai/health` | Estado del proveedor de IA |

### Cambiar de proveedor es una variable

`LLM_PROVIDER=ollama` o `openai`. **Ningún archivo de código cambia.** El
resto del sistema solo conoce la clase base de `core/llm/base.py`.

```bash
# hoy, en tu máquina
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434

# mañana, en producción con GPU
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama-gpu.interno:11434

# o un servicio gestionado
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

`OPENAI_BASE_URL` también acepta gateways compatibles (Azure OpenAI, vLLM,
LiteLLM): es HTTP contra el protocolo de OpenAI, no un cliente atado a un
proveedor.

> **Detalle de Docker:** dentro de un contenedor, `localhost` es el propio
> contenedor. Por eso en desarrollo la URL es `host.docker.internal`, que es el
> host desde adentro. Es la única línea que cambia entre desarrollo y
> producción.

### Qué pasa con Ollama en producción

Es la decisión que el código no puede tomar por vos. Lo que sí hace el código es
no atarse:

- **Un Ollama centralizado con GPU**, y todas las réplicas de la API pegándole.
  El modelo se carga una sola vez en memoria. Es lo correcto.
- **Un Ollama por réplica** multiplica la RAM por la cantidad de réplicas y
  recalcula el modelo en cada una. Solo tiene sentido en desarrollo.
- **Proveedor gestionado**, si los datos no pueden salir de la red interna.

`make up-ollama` levanta un Ollama dentro de Docker para producción o CI. Está
separado del compose base a propósito: en el compose base haría que cada `make
up` descargara 5 GB de imágenes para no usarlas.

Si la IA se cae, **el inventario sigue funcionando**. `/health` no consulta al
modelo a propósito: si dependiera de él, un Ollama lento haría que el
orquestador reiniciara la API en loop.

---

## La base de datos

16 tablas. Lo que no es obvio y por qué está así:

- **`users` es UUID, y `movement_history.user_id` también.** Antes era un `INT`
  suelto sin foreign key: se podía guardar `999` o `-1` y nadie lo notaba.
- **Las tablas de historial son `ON DELETE RESTRICT`, no `CASCADE`.** Con
  `CASCADE`, un `DELETE` accidental de un dispositivo se lleva todo su
  historial. La baja legítima se hace con `deleted_at`.
- **`devices.is_warehouse` es `CHECK`-eado contra `current_status`.** Son la
  misma información en dos columnas; el CHECK impide que divergan en silencio.
- **Todas las FK están indexadas.** Postgres no lo hace solo, y los JOIN que
  genera la IA entran por ahí.
- **`users.password` no sale del servidor** por preguntas en lenguaje natural.
  La restricción está en código (`guard.py`), no en el prompt.

### Migraciones

```bash
make migrar-estado      # ¿qué falta? no cambia nada
make migrar             # aplica, preguntando
make migrar MIGRAR_SI=1  # aplica sin preguntar (CI)
```

- **Instalación nueva** → `make up`. No hacen falta migraciones: `init.sql` se
  corre en el primer arranque del volumen.
- **Base existente** → `make migrar`.

`init.sql` y una base migrada terminan con el mismo esquema, así que mirar las
tabelas no dice si una base necesita migraciones. Para eso está la tabla
`schema_migrations`.

Las migraciones son atómicas: cada archivo va entero o no va. Y **no borran
datos para pasar**. Si `movement_history` tiene filas con `user_id` entero, la
migración se detiene e imprime el `SELECT` para ver cuáles. Perder trazabilidad
para que un script diga "ok" es peor que el error.

---

## Comandos

```bash
make                # lista de todos los targets
make up             # build + up, espera a que todo esté healthy
make up-dev         # con recarga de código y logs en DEBUG
make up-qa          # sin --reload: QA prueba el artefacto, no tu working tree
make up-ollama      # stack con Ollama dentro de Docker
make reload         # reinicia backend y frontend sin rebuild
make migrar-estado / migrar
make ai-health / ai-pull / ai-list / ai-test
make shell / db-shell
make stats / disk / ps / logs
make down            # conserva la base
make clean          # BORRA la base
make prune
```

`up` y `up-force` usan `--wait`: devuelven el control cuando los servicios están
**sirviendo**, no cuando los contenedores existen. Sin eso, abrís el navegador
al segundo 2 y no hay API detrás.

---

## Seguridad de las consultas que genera el modelo

Un LLM escribe SQL, así que el SQL es la parte **no confiable** del sistema.
Decirle al modelo "no hagas DROP TABLE" es una instrucción, no un control de
acceso.

Capas, en `backend/src/modules/ai/guard.py` y `executor.py`:

1. Validación textual (solo SELECT, un statement, sin palabras vetadas).
2. `SET TRANSACTION READ ONLY` — **lo impone Postgres, no Python**. Si la
   validación tiene un hueco, el motor lo cierra.
3. `statement_timeout` — un `SELECT * FROM movement_history` sin filtro no
   cuelga la API.
4. `LIMIT` forzado.
5. Columnas vetadas (`password`, `old_value`, `new_value`).
6. El prompt solo ve el **esquema**. Las filas crudas nunca salen del servidor.

```bash
make ai-test          # incluye la suite de seguridad
```

---

## Estructura

### Ramas

| Rama | Qué es |
| --- | --- |
| `main` | Lo que está en producción. |
| `develop` | La integración: todo lo terminado se mergea acá. |
| `feature/<nombre>` | Una cosa que se está haciendo. |

Nadie commitea directo en `main` ni en `develop`, y `main` solo avanza por
merge de `develop`. El detalle está en [`docs/flujo-trabajo.md`](docs/flujo-trabajo.md).

### El código

```text
backend/src/
├── main.py                  # app + /health (no consulta a la IA, a propósito)
├── core/
│   ├── config.py            # TODA la config; ningún os.getenv fuera de acá
│   ├── geo.py               # haversine y validación de radio del escaneo
│   ├── middleware.py        # headers, rate limit, CORS, política de permisos
│   └── llm/
│       ├── base.py          # la interfaz que conoce el resto del sistema
│       ├── ollama.py        # fallback automático a un modelo más chico
│       ├── openai.py        # compatible con gateways OpenAI-compatible
│       └── factory.py       # el único lugar que decide qué proveedor se usa
└── modules/
    ├── ai/
    │   ├── router.py            # /api/ai/*
    │   ├── sql_agent.py         # pregunta → SQL → datos → explicación
    │   ├── analyzer.py          # clasificación y riesgo por equipo
    │   ├── report.py            # reportes (SQL fijo, glosa del modelo)
    │   ├── guard.py             # validación de SQL
    │   ├── executor.py          # transacción READ ONLY
    │   ├── table_format.py      # cómo se le pasan los datos al modelo
    │   └── schema_context.py    # el esquema que ve el modelo (y solo eso)
    ├── devices/              # alta, resolución de código, ficha
    ├── movements/            # escaneo idempotente + geo-validación
    ├── operations/           # transferencias, mantenimiento, alertas
    └── users/                # auth, sesiones, roles, auditoría
```

```text
frontend/
├── index.html               # esqueleto; ningún script inline (la CSP no cede)
├── nginx.conf               # CSP estricta, cámara habilitada, /api en proxy
├── public/
│   ├── manifest.webmanifest # PWA instalable
│   └── sw.js                # service worker: cachea el esqueleto, NUNCA la API
└── src/
    ├── main.js              # sesión, navegación, arranque
    ├── api.js               # cliente HTTP; token en memoria, refresh en sesión
    ├── dom.js               # creación de DOM sin innerHTML
    ├── escaner.js           # BarcodeDetector nativo + ingreso manual
    ├── avisos.js            # avisos flotantes
    └── vistas/              # login, escanear, inventario, consultas
```

---

## Seguridad del front

La versión anterior de esta app era un `index.html` con Tailwind y
`html5-qrcode` desde CDN, y tenía **XSS explotable**. Lo que se corrigió:

**1. Nada de `innerHTML` con datos.** El contenido de un QR es texto de
atacante: cualquiera puede imprimir una etiqueta con
`<img src=x onerror=...>` y pegarla en cualquier equipo. La versión vieja hacía
`li.innerHTML = ...${code}...` y el script corría con la sesión del operador —
que además tenía el JWT en un `<input>` del DOM, visible y en el historial del
navegador.

Ahora todo el DOM se arma con `createElement` + `textContent`
(`src/dom.js`). `textContent` nunca interpreta HTML, y no hay una sola rama que
use `innerHTML`. La salida del modelo se trata igual que la del usuario: es el
XSS indirecto más subestimado que hay, porque la respuesta de la IA suele
devolver en prosa parte de lo que se le preguntó.

**2. Cero dependencias externas.** Sin CDN, sin `package.json`, sin build.
`cdn.tailwindcss.com` no es solo un tercero del que la app depende: genera el
CSS con JavaScript, lo que obliga a `unsafe-inline` y `unsafe-eval` en la CSP, y
la única protección contra XSS queda desactivada para poder usar un estilo. Con
CSS propio y módulos ES nativos, la CSP puede ser estricta.

**3. El token no está en el DOM.** Vive en memoria. El refresh va a
`sessionStorage`, no a `localStorage`: en un dispositivo compartido en un
depósito, la diferencia es entre "cerrar la app" y "el turno siguiente entra con
mi sesión".

Verificado en navegador: el payload `<img src=x onerror=...>` queda como nodo de
**texto** (`nodeType 3`), cero elementos `<img>` creados, script sin ejecutar.

### La CSP y los permisos

`script-src 'none'`: si una inyección de HTML se colara, no puede ejecutarse. Lo
único que se cede es `style-src 'unsafe-inline'`, acotado a estilos —que no
ejecutan JavaScript—.

La `Permissions-Policy` habilita `camera=(self)` y `geolocation=(self)` porque
el escaneo los necesita, y **nega** micrófono, pago y USB. La versión anterior
mandaba `camera=() geolocation=()`: la política se aplica a todo el origen, así
que `getUserMedia()` fallaba y el escáner directamente no existía.

---

## Problemas frecuentes

**"La IA no responde"** → `make ai-health`. Dice si la config está mal, si el
modelo no está descargado, o si no entra.

**`Cannot find module` en el frontend** → hay un `node_modules` del host
adentro. Está en `.dockerignore` justamente por eso.

**El healthcheck del frontend falla** → el front sirve en 5173, no en 3000.

**"column does not exist" en una migración** → mirá `database/migrations/`. Si
la base ya tenía datos, hay chequeos previos que avisan qué filas hay que
corregir antes.