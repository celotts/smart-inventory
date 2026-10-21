"""
El middleware que escribe la bitacora de transacciones.

QUE HACE
--------
Cada peticion a la API deja una fila en `transacciones`: quien la hizo, que
pidio, sobre que recurso, que le devolvio y cuanto tardo.

POR QUE UN MIDDLEWARE Y NO UN DECORADOR POR ENDPOINT
-----------------------------------------------------
Un decorador obliga a acordarse de ponerlo. El primero que se olvida es
justo el que no queria auditarse, y el que se olvida no se nota: la
bitacora esta completa y sin el evento.

El middleware no se puede olvidar: pasa por TODAS las peticiones.

COMO SABE QUIEN ES EL USUARIO
------------------------------
Decodifica el token de la cabecera `Authorization` (sin tocar la base: la
validacion real la hace el endpoint). Si el token no es valido o no hay
token, la transaccion se registra SIN usuario: un intento de acceso sin
sesion tambien es un evento que interesa ver.

Y SI LA PETICION SE RECHAZA
--------------------------
Se registra con `resultado = 'denegado'`. Un 403 es una de las cosas que
un cliente con datos de empleados pregunta: "quien intento ver lo que no
podia ver".
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.core import transacciones
from src.core.security import TokenInvalido, decodificar_token

logger = logging.getLogger(__name__)

# Cada cuantas escrituras se limpia la tabla de lo vencido.
#
# La limpieza va aqui y no en un job porque un job es una cosa mas que
# puede no estar corriendo: si el job no corre, la tabla sigue limpia.
CADA_CUANTAS_ESCRITURAS = 500

# El contador de escrituras, para saber cuando limpiar.
_contador = 0


class TransaccionesMiddleware(BaseHTTPMiddleware):
    """
    Escribe una transaccion por peticion.

    NUNCA ROMPE LA PETICION
    -----------------------
    Si la auditoria falla (la tabla esta llena, la base esta lenta), la
    peticion sigue: un error de auditoria se avisa en el log del servidor y
    se sigue con el trabajo del operador.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        global _contador

        if not transacciones._es_operacion(request.url.path):
            return await call_next(request)

        inicio = time.monotonic()
        respuesta = await call_next(request)
        duracion_ms = int((time.monotonic() - inicio) * 1000)

        try:
            self._registrar(request, respuesta, duracion_ms)
        except Exception as exc:  # noqa: BLE001 - la auditoria no puede voltear la peticion
            logger.warning("No se pudo auditar %s: %s", request.url.path, exc)

        # La limpieza de lo vencido, de vez en cuando.
        _contador += 1
        if _contador % CADA_CUANTAS_ESCRITURAS == 0:
            try:
                borradas = transacciones.limpiar_vencidas()
                if borradas:
                    logger.info("Se borraron %d transacciones vencidas", borradas)
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudieron limpiar las transacciones: %s", exc)

        return respuesta

    def _registrar(self, request: Request, respuesta: Response, duracion_ms: int) -> None:
        """Escribe la transaccion de esta peticion."""
        usuario = self._usuario_del_token(request)

        status = respuesta.status_code
        if status == 403:
            resultado = "denegado"
        elif 200 <= status < 400:
            resultado = "ok"
        else:
            resultado = "error"

        transacciones.registrar(
            user_id=usuario.get("user_id"),
            user_email=usuario.get("email"),
            role_name=usuario.get("role"),
            metodo=request.method,
            ruta=request.url.path,
            resultado=resultado,
            status_code=status,
            duracion_ms=duracion_ms,
            ip_address=_ip_de(request),
            user_agent=request.headers.get("user-agent"),
            request_id=_request_id_de(request, respuesta),
            sesion_id=usuario.get("sesion_id"),
        )

    def _usuario_del_token(self, request: Request) -> dict[str, Any]:
        """
        Quien es el de esta peticion, segun el token.

        Se DECODIFICA, no se valida: la validacion real la hace el endpoint
        (que ademas consulta la sesion). Acá solo hace falta saber de quien
        es la peticion para la bitacora, y un token invalido se registra
        como peticion SIN usuario.
        """
        cabecera = request.headers.get("authorization")
        if not cabecera:
            return {}

        try:
            payload = decodificar_token(cabecera, "access")
        except (TokenInvalido, Exception):  # noqa: BLE001
            return {}

        return {
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role"),
            "sesion_id": payload.get("sid"),
        }


def _ip_de(request: Request) -> str | None:
    """
    La IP del cliente, confiando en `X-Forwarded-For` SOLO si el proxy es de
    los confiados.

    Confiar en la cabecera sin mas es confiar en lo que mande el cliente: un
    atacante pone su IP y la auditoria registra una direccion falsa.
    """
    redes = getattr(request.app.state, "redes_confiadas", None)
    if redes:
        for salto in reversed(request.headers.get("x-forwarded-for", "").split(",")):
            candidate = salto.strip()
            if not candidate:
                continue
            try:
                import ipaddress

                direccion = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if any(direccion in red for red in redes):
                return candidate
        return request.client.host if request.client else None

    return request.client.host if request.client else None


def _request_id_de(request: Request, respuesta: Response) -> str | None:
    """El id de peticion: el que sale en la cabecera de la respuesta."""
    valor = request.headers.get("x-request-id") or respuesta.headers.get("x-request-id")
    if not valor:
        return None
    try:
        import uuid

        return str(uuid.UUID(valor))
    except (ValueError, AttributeError):
        return None
