# Smart Inventory B2B

Inventario de dispositivos para empresas, con IA integrada para análisis y
respuesta de preguntas en lenguaje natural.

```
make up-dev          # levanta todo (con IA apuntando a tu Ollama local)
make ai-pull         # descarga el modelo: paso único, tarda minutos
make ai-test         # prueba los 4 endpoints
```

- Front: http://localhost:5173
- API: http://localhost:5001 · Docs: http://localhost:5001/docs

---

## Qué hay adentro

| Parte | Qué es |
|---|---|
| `database/init.sql` | Esquema completo: 11 tablas, índices, CHECKs, triggers |
| `database/migrations/` | Migraciones para bases que ya existen |
| `backend/src/core/llm/` | Proveedores de IA (Ollama, OpenAI) tras una interfaz común |
| `backend/src/modules/ai/` | Preguntas en lenguaje natural, análisis y reportes |
| `frontend/` | Front en Vite |

---

## La IA

Cuatro endpoints, todos bajo `/api/ai`:

| Endpoint | Qué hace |
|---|---|
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

11 tablas. Lo que no es obvio y por qué está así:

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

```
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

```
backend/src/
├── main.py                  # app + /health (no consulta a la IA, a propósito)
├── core/
│   ├── config.py            # TODA la config; ningún os.getenv fuera de acá
│   └── llm/
│       ├── base.py          # la interfaz que conoce el resto del sistema
│       ├── ollama.py        # fallback automático a un modelo más chico
│       ├── openai.py        # compatible con gateways OpenAI-compatible
│       └── factory.py       # el único lugar que decide qué proveedor se usa
└── modules/ai/
    ├── router.py            # /api/ai/*
    ├── sql_agent.py         # pregunta → SQL → datos → explicación
    ├── analyzer.py          # clasificación y riesgo por equipo
    ├── report.py            # reportes (SQL fijo, glosa del modelo)
    ├── guard.py             # validación de SQL
    ├── executor.py          # transacción READ ONLY
    ├── table_format.py      # cómo se le pasan los datos al modelo
    └── schema_context.py    # el esquema que ve el modelo (y solo eso)
```

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