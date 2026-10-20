"""Excepciones de dominio y handlers centralizados para FastAPI."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError


class DomainError(Exception):
    """Error de negocio genérico."""

    status_code = 400
    code = "domain_error"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_handler(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.code,
                "detail": str(exc),
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def base_no_disponible(request: Request, exc: SQLAlchemyError):
        """
        La base no esta: eso NO es un error interno.

        POR QUE
        ------
        Sin este handler, un `OperationalError` de psycopg2 (la base caida, el
        contenedor que no arrancó) cae en el handler generico y el operador
        ve "Error interno". Un "error interno" dice "el programa esta roto";
        lo que pasa es que el contenedor de la base no esta arriba, y eso se
        arregla levantandolo.

        El 503 dice ademas que el problema NO es de la app: un 500 le hace
        pensar al operador que hay que llamar al soporte de sistemas.
        """
        return JSONResponse(
            status_code=503,
            content={
                "error": "database_unavailable",
                "detail": (
                    "La base de datos no esta disponible. "
                    "Si el proyecto corre en Docker, chequea que el contenedor "
                    "de la base este arriba (`make estado`)."
                ),
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": "Error interno.",
                "request_id": getattr(request.state, "request_id", None),
            },
        )
