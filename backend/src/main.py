"""
Punto de entrada de la API de Gestor de Activos.

POR QUE `uvicorn` Y NO `python src/main.py`
------------------------------------------
`CMD ["python", "src/main.py"]` ejecutaba el archivo como un script suelto:
sin reload, sin graceful shutdown y sin forma de que uvicorn reporte cuando el
socket ya esta escuchando. Uvicorn tiene las dos cosas, y el healthcheck
necesita de la segunda para no mentir.

POR QUE /health CONSULTA A LA BASE Y NO AL SOCKET
-------------------------------------------------
Responder "estoy vivo" en cuanto el puerto abre es el mismo bug que
`--wait` sin `--wait-timeout`: durante el arranque de Postgres las conexiones
se rechazan, pero en cuanto el postmaster levanta el socket, /health da 200
antes de que exista una sola tabla. Se verifica con un SELECT 1 real.

POR QUE /health NO CONSULTA A LA IA
------------------------------------
Este endpoint decide si el CONTENEDOR esta sano. Si dependiera del proveedor de
LLM, un Ollama lento o caido reiniciaria la API de inventario — que funciona
perfectamente sin IA — y el orquestador lo volveria a reiniciar, en loop.

El estado de la IA vive en `/api/ai/health`, que reporta sin propagar el fallo.
La separacion es deliberada: el inventario no depende del modelo.
"""

import asyncio
import contextlib
import os
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Response, status
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:secretpassword@db:5432/smart_inventory",
)

_ES_PRODUCCION = os.getenv("APP_ENV", "production").lower() in ("production", "prod")

# El pool es chico a propósito: el contenedor corre una sola instancia de
# uvicorn y el healthcheck abre una conexión cada 10s. Un pool grande solo
# desperdicia conexiones de Postgres, que es el recurso escaso aquí.
engine = create_engine(DATABASE_URL, pool_size=3, max_overflow=2, pool_pre_ping=True)

# Cuanto espera la API a que la base este lista antes de rendirse.
DB_ESPERA_SEGUNDOS = int(os.getenv("DB_ESPERA_SEGUNDOS", "60"))


def esperar_base() -> None:
    """
    Espera a que la base responda, o se rinde.

    POR QUE LA API MUERE SI NO HAY BASE
    ------------------------------------
    Un backend vivo contra una base caida es PEOR que un backend caido: acepta
    escrituras, las responde con 500 a la mitad, y el operador cree que
    guardo un movimiento que en realidad no existe. Ademas el healthcheck
    marcaria el contenedor como sano y el front levantaria contra el.

    Asi que al arrancar, la API verifica la base con un SELECT 1 de verdad. Si
    no responde en `DB_ESPERA_SEGUNDOS`, el proceso MUERE: el contenedor queda
    en "restarting" y Docker lo vuelve a levantar, y en el siguiente intento la
    base puede estar arriba. Es un reintento con backoff, sin cron y sin que
    nadie tenga que intervenir.

    El mensaje va al log, que es donde lo mira el operador cuando el stack no
    levanta.
    """
    limite = time.monotonic() + DB_ESPERA_SEGUNDOS
    intento = 0

    while True:
        intento += 1
        try:
            with engine.connect() as conexion:
                conexion.execute(text("SELECT 1"))
            if intento > 1:
                print(f"[arranque] Base conectada al intento {intento}.", flush=True)
            return
        except SQLAlchemyError as error:
            if time.monotonic() >= limite:
                print(
                    f"[arranque] La base no respondio en {DB_ESPERA_SEGUNDOS}s "
                    f"({intento} intentos). Ultimo error: {error}. "
                    "El contenedor se reinicia para reintentar.",
                    flush=True,
                )
                # `sys.exit` y no `raise`: el mensaje es para el log del
                # contenedor, y el proceso tiene que terminar con código != 0
                # para que Docker lo reinicie.
                sys.exit(1)
            time.sleep(2)


async def barrer_sla_en_segundo_plano(intervalo: int = 60) -> None:
    """
    Corre el barrido de SLA cada minuto, en segundo plano.

    QUE HACE
    --------
    Avisa de los casos que vencen en breve y escala los que ya pasaron su
    plazo de respuesta sin que nadie los haya tomado. El motor del SLA está
    en el servicio de incidencias; esto es solo el reloj.

    POR QUE `asyncio.to_thread`
    --------------------------
    La consulta a la base es BLOQUEANTE. Corrida en el event loop, frena
    TODAS las peticiones de la API mientras el barrido habla con Postgres: en
    un depósito, con el escaner abierto, eso se nota. En un hilo aparte, la
    API sigue respondiendo.

    POR QUE UN HILO Y NO UN CRON
    ---------------------------
    Un cron externo necesitaria una imagen más, un despliegue más y un lugar
    más donde se rompe. Además, con el cron, un contenedor reiniciado durante
    un deploy pierde el aviso del medio. Con esto, el barrido sigue vivo con
    el contenedor.
    """
    from src.modules.incidents import service as incidents_service

    while True:
        try:
            await asyncio.sleep(intervalo)
            await asyncio.to_thread(incidents_service.barrer_sla)
        except asyncio.CancelledError:
            # El cierre del contenedor: se sale sin ruido.
            raise
        except Exception as error:  # noqa: BLE001 - el barrido no puede tumbar la API
            print(f"[sla] El barrido fallo: {error}", flush=True)


async def barrer_alertas_en_segundo_plano(intervalo: int = 300) -> None:
    """
    Corre el barrido de alertas de stock bajo cada cinco minutos.

    POR QUE CADA CINCO MINUTOS Y NO CADA UNO
    ---------------------------------------
    El barrido de SLA corre cada minuto porque un plazo de 15 minutos se
    avisa con poco margen. Una alerta de stock bajo dura días: el producto
    no se repone de un minuto a otro. Correrlo cada cinco minutos alcanza y
    no le pega a la base cada minuto.

    Y POR QUE UN BARRIDO Y NO UN EVENTO AL MOVER
    --------------------------------------------
    El stock baja en un movimiento, pero también sube, y el estado depende
    de MUCHO más que de un movimiento (una compra que aún no llega, una
    devolucion). Un barrido que mira el estado real no se olvida de ningún
    caso, y se puede correr a mano cuando algo parece raro.
    """
    from src.modules.stock import alertas as stock_alertas

    while True:
        try:
            await asyncio.sleep(intervalo)
            await asyncio.to_thread(stock_alertas.barrer_alertas)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - el barrido no puede tumbar la API
            print(f"[stock] El barrido de alertas fallo: {error}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    El ciclo de vida de la API.

    La espera de la base va Acá y no en un `@app.on_event("startup")`: el
    evento de startup es la API vieja de FastAPI, y el `lifespan` es la que
    todos los frameworks de ASGI usan. Además, desde el lifespan se puede
    levantar un error y DILE al proceso que muera, que es lo que se quiere.

    El barrido de SLA arranca acá y se corta al apagar: una tarea de fondo que
    sobrevive al cierre del contenedor es una tarea que sigue consultando una
    base que ya no existe.
    """
    esperar_base()

    tarea_sla = asyncio.create_task(barrer_sla_en_segundo_plano())
    tarea_alertas = asyncio.create_task(barrer_alertas_en_segundo_plano())
    try:
        yield
    finally:
        for tarea in (tarea_sla, tarea_alertas):
            tarea.cancel()
        for tarea in (tarea_sla, tarea_alertas):
            with contextlib.suppress(asyncio.CancelledError):
                await tarea

app = FastAPI(
    title="Gestor de Activos — API de activos e incidencias",
    version="0.5.0",
    description="Gestion de activos, inventario e incidencias para empresas, con IA.",
    lifespan=lifespan,
    # POR QUE /docs SE DESACTIVA EN Producción
    # --------------------------------------
    # Swagger UI no es un endpoint de negocio: es una página que lista TODA la
    # superficie de la API con ejemplos ejecutables, y arrastra el CDN de
    # fastapi.tiangolo.com para el CSS y el JS.
    #
    # Lo que abre de verdad:
    #   - El mapa completo de endpoints a cualquiera que llegue a la API, que
    #     sirve para elegir a que atacar.
    #   - Un tercero del que la página depende. Si ese dominio está
    #     comprometido, inyecta JS en el navegador de quien abra /docs.
    #   - El esquema OpenAPI, que además alimenta a Postman y a los generadores
    #     de cliente.
    #
    # En desarrollo queda disponible, que es donde sirve.
    docs_url=None if _ES_PRODUCCION else "/docs",
    redoc_url=None if _ES_PRODUCCION else "/redoc",
    openapi_url=None if _ES_PRODUCCION else "/openapi.json",
)

# --- Routers ---------------------------------------------------------------
# Se importan después de crear `app` porque los módulos de la API usan
# `from src.main import app`... no, no lo hacen: traen su propio APIRouter y se
# incluyen acá. El import va arriba de todo por legibilidad, pero los
# `include_router` quedan juntos para que se vea la superficie de la API de un
# vistazo.
from src.core.exceptions import register_exception_handlers
from src.core.security import verificar_configuracion_de_secreto
from src.core.localization.router import router as localization_router
from src.modules.ai.router import router as ai_router
from src.modules.audit.router import router as audit_router
from src.modules.catalog.router import router as catalog_router
from src.modules.devices.router import router as devices_router
from src.modules.export.router import router as export_router
from src.modules.incidents.router import router as incidents_router
from src.modules.locations.router import router as locations_router
from src.modules.maintenance.router import router as maintenance_router
from src.modules.movements.router import router as movements_router
from src.modules.operations.router import router as ops_router
from src.modules.purchasing.router import router as purchasing_router
from src.modules.reporting.router import router as reporting_router
from src.modules.stock.router import router as stock_router
from src.modules.users.router import router as auth_router
from src.modules.users.router import usuarios_router

# Falla en el ARRANQUE y no en el primer login. Un deploy sin SECRET_KEY tiene
# que caerse en el pipeline, no un martes a la tarde en producción.
verificar_configuracion_de_secreto()
register_exception_handlers(app)

from src.core.middleware import (
    LoginRateLimitMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    configurar_cors,
)
from src.core.waf import register_waf

configurar_cors(app)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)

# WAF: primera barrera de defensa en profundidad (antes del rate limit)
# En producción bloquea; en desarrollo solo loggea (log_only=True)
_es_produccion = os.getenv("APP_ENV", "production").lower() in ("production", "prod")
register_waf(app, log_only=not _es_produccion)

# El orden importa. `add_middleware` los mete al PRINCIPIO de la pila, así que
# el último agregado es el más externo: el rate limit ve la request antes que
# el parser de cuerpo y antes de que se abra la sesión. Un límite que se
# verifica después de validar el JWT no frena un ataque de fuerza bruta, que es
# justo el caso para el que existe.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(LoginRateLimitMiddleware)

app.include_router(localization_router)
app.include_router(ai_router)
app.include_router(auth_router)
app.include_router(usuarios_router)
app.include_router(ops_router)
app.include_router(catalog_router)
app.include_router(audit_router)
app.include_router(devices_router)
app.include_router(movements_router)
app.include_router(stock_router)
app.include_router(maintenance_router)
app.include_router(purchasing_router)
app.include_router(reporting_router)
app.include_router(locations_router)
app.include_router(incidents_router)
app.include_router(export_router)


@app.get("/health", tags=["operacion"])
def health() -> Response:
    """
    Estado real del servicio: la API responde Y la base acepta queries.

    SIN token, a propósito: lo consulta el healthcheck del contenedor, que no
    tiene credenciales. Y NO consulta a la IA: si dependiera del proveedor de
    LLM, un Ollama lento haria que el orquestador reiniciara la API de
    inventario —que funciona sin IA— en loop.

    EL 503 ES LA PARTE IMPORTANTE
    ------------------------------
    Con la base caida, está respuesta es 503 y NO 200. El healthcheck del
    contenedor mira el código: un 503 deja el contenedor "unhealthy", y el
    frontend, que espera `service_healthy`, no se levanta contra una API que
    no puede guardar nada.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        return Response(
            content=(
                '{"status": "degraded", "database": "unreachable", '
                f'"detail": "{error}"}}'
            ).encode(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    return Response(
        content=b'{"status": "ok", "database": "reachable"}',
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@app.get("/", tags=["operacion"])
def root() -> dict:
    return {
        "service": "smart-inventory-api",
        "version": app.version,
        "docs": "/docs",
        "ia": {
            "preguntar": "POST /api/ai/preguntar",
            "analizar_dispositivo": "GET /api/ai/analisis/dispositivo/{id}",
            "reporte": "POST /api/ai/reporte",
            "estado": "GET /api/ai/health",
        },
        "auth": {
            "login": "POST /api/auth/login",
            "refresh": "POST /api/auth/refresh",
            "logout": "POST /api/auth/logout",
            "yo": "GET /api/auth/yo",
        },
        "usuarios": {
            "crear": "POST /api/users",
            "listar": "GET /api/users",
            "actualizar": "PATCH /api/users/{id}",
            "desactivar": "POST /api/users/{id}/desactivar",
        },
    }


if __name__ == "__main__":
    # 0.0.0.0 y no localhost: dentro del contenedor el bind por defecto es
    # solo loopback y el puerto publicado del host nunca llega.
    uvicorn.run("src.main:app", host="0.0.0.0", port=5000, reload=False)

# Middleware de auditoría para trazabilidad completa
try:
    from src.core.audit_middleware import AuditMiddleware
    app.add_middleware(AuditMiddleware)
except ImportError as e:
    print(f"Warning: No se pudo cargar AuditMiddleware: {e}")

# LA BITACORA DE TRANSACCIONES
#
# Se engancha al final (y por eso es el PRIMERO en la pila: `add_middleware`
# mete al principio): asi mide la duracion de TODO, incluyendo lo que tardan
# los otros middleware y el endpoint.
#
# Si la tabla `transacciones` todavia no existe (una base migrada a medias),
# el middleware no se engancha y la app sigue funcionando: la bitacora es
# importante, pero no es lo que hace que la app ande.
try:
    from src.core.transacciones_middleware import TransaccionesMiddleware
    app.add_middleware(TransaccionesMiddleware)
except ImportError as e:
    print(f"Warning: No se pudo cargar TransaccionesMiddleware: {e}")
