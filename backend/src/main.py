"""
Punto de entrada de la API de Smart Inventory B2B.

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
perfectamente sin IA — y el orquestador lo volvería a reiniciar, en loop.

El estado de la IA vive en `/api/ai/health`, que reporta sin propagar el fallo.
La separación es deliberada: el inventario no depende del modelo.
"""

import os

import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:secretpassword@db:5432/smart_inventory",
)

_ES_PRODUCCION = os.getenv("APP_ENV", "production").lower() in ("production", "prod")

app = FastAPI(
    title="Smart Inventory B2B API",
    version="0.3.0",
    description="Gestion de inventario de dispositivos para empresas, con IA.",
    # POR QUE /docs SE DESACTIVA EN PRODUCCION
    # --------------------------------------
    # Swagger UI no es un endpoint de negocio: es una pagina que lista TODA la
    # superficie de la API con ejemplos ejecutables, y arrastra el CDN de
    # fastapi.tiangolo.com para el CSS y el JS.
    #
    # Lo que abre de verdad:
    #   - El mapa completo de endpoints a cualquiera que llegue a la API, que
    #     sirve para elegir a que atacar.
    #   - Un tercero del que la pagina depende. Si ese dominio esta
    #     comprometido, inyecta JS en el navegador de quien abra /docs.
    #   - El esquema OpenAPI, que ademas alimenta a Postman y a los generadores
    #     de cliente.
    #
    # En desarrollo queda disponible, que es donde sirve.
    docs_url=None if _ES_PRODUCCION else "/docs",
    redoc_url=None if _ES_PRODUCCION else "/redoc",
    openapi_url=None if _ES_PRODUCCION else "/openapi.json",
)

# El pool es chico a proposito: el contenedor corre una sola instancia de
# uvicorn y el healthcheck abre una conexion cada 10s. Un pool grande solo
# desperdicia conexiones de Postgres, que es el recurso escaso aqui.
engine = create_engine(DATABASE_URL, pool_size=3, max_overflow=2, pool_pre_ping=True)

# --- Routers ---------------------------------------------------------------
# Se importan despues de crear `app` porque los modulos de la API usan
# `from src.main import app`... no, no lo hacen: traen su propio APIRouter y se
# incluyen aca. El import va arriba de todo por legibilidad, pero los
# `include_router` quedan juntos para que se vea la superficie de la API de un
# vistazo.
from src.core.exceptions import register_exception_handlers
from src.core.security import verificar_configuracion_de_secreto
from src.modules.ai.router import router as ai_router
from src.modules.catalog.router import router as catalog_router
from src.modules.audit.router import router as audit_router
from src.modules.devices.router import router as devices_router
from src.modules.movements.router import router as movements_router
from src.modules.users.router import router as auth_router
from src.modules.users.router import usuarios_router
from src.modules.operations.router import router as ops_router
from src.modules.stock.router import router as stock_router
from src.modules.maintenance.router import router as maintenance_router
from src.modules.purchasing.router import router as purchasing_router
from src.modules.reporting.router import router as reporting_router
from src.modules.locations.router import router as locations_router
from src.modules.export.router import router as export_router

# Falla en el ARRANQUE y no en el primer login. Un deploy sin SECRET_KEY tiene
# que caerse en el pipeline, no un martes a la tarde en produccion.
verificar_configuracion_de_secreto()
register_exception_handlers(app)

from src.core.middleware import (
    LoginRateLimitMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    configurar_cors,
)

configurar_cors(app)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
# El orden importa. `add_middleware` los mete al PRINCIPIO de la pila, asi que
# el ultimo agregado es el mas externo: el rate limit ve la request antes que
# el parser de cuerpo y antes de que se abra la sesion. Un limite que se
# verifica despues de validar el JWT no frena un ataque de fuerza bruta, que es
# justo el caso para el que existe.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(LoginRateLimitMiddleware)

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
app.include_router(export_router)


@app.get("/health", tags=["operacion"])
def health() -> dict:
    """
    Estado real del servicio: la API responde Y la base acepta queries.

    SIN token, a proposito: lo consulta el healthcheck del contenedor, que no
    tiene credenciales. Y NO consulta a la IA: si dependiera del proveedor de
    LLM, un Ollama lento haria que el orquestador reiniciara la API de
    inventario —que funciona sin IA— en loop.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        return {"status": "degraded", "database": "unreachable", "detail": str(error)}

    return {"status": "ok", "database": "reachable"}


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
except Exception as e:
    print(f"Warning: No se pudo cargar AuditMiddleware: {e}")
