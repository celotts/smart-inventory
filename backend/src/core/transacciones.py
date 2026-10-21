"""
La bitacora de transacciones: cada operacion que pasa por la API.

QUE REGISTRA
------------
Cada peticion autenticada a la API, con:
  - QUIEN la hizo (usuario, email y rol en el momento),
  - QUE pidio (metodo, ruta, recurso, recurso afectado),
  - COMO SALIO (resultado, codigo HTTP, duracion),
  - DE DONDE (IP, user agent, id de peticion, id de sesion).

LO QUE NO REGISTRA
------------------
El CUERPO de la peticion. Puede traer una contrasena (la de "cambiar mi
clave", la de "crear usuario") y una contrasena en una bitacora es una
contrasena escrita en un archivo que cualquiera con acceso al servidor lee.

Lo que si se guarda es `datos`, que el codigo de cada operacion arma con lo
que esa operacion decide que es relevante (el serial de un equipo, el
nombre de un producto) y NUNCA con el cuerpo crudo.

DIFERENCIA CON `audit_logs`
---------------------------
`audit_logs` es la bitacora de LOS DATOS: que se creo, que se modifico y
que se borro, con el valor viejo y el nuevo. Esta es la de LAS OPERACIONES:
tambien las lecturas, que no cambian nada y por eso `audit_logs` no las
tiene.

Las dos hacen falta: "cambio el precio" y "el administrador consultedo el
inventario a las 14:32" son preguntas distintas.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# Las operaciones, con lo que significan.
LECTURA = "LECTURA"
ESCRITURA = "ESCRITURA"
AUTENTICACION = "AUTENTICACION"
SISTEMA = "SISTEMA"

# Los metodos que no cambian datos.
_METODOS_DE_ESCRITURA = {"POST", "PUT", "PATCH", "DELETE"}

# Las rutas que NO se registran.
#
#  - el healthcheck: lo consulta el orquestador de Docker cada 10 segundos,
#    y no es una operacion de nadie.
#  - el estado de la IA: la campana lo consulta cada 30 segundos.
#  - el login fallido se REGISTRA (es lo que hay que ver cuando alguien
#    esta probando claves).
RUTAS_IGNORADAS = {
    "/health",
    "/api/health",
    "/api/ai/health",
    "/api/ai/alcances",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
}

# La retencion, en dias.
DIAS_DE_RETENCION = int(os.getenv("AUDITORIA_RETIENE_DIAS", "180"))


def _es_operacion(ruta: str) -> bool:
    """La ruta es una operacion de alguien (y no una comprobacion de vida)."""
    limpia = ruta.split("?")[0].rstrip("/") or "/"
    if limpia in RUTAS_IGNORADAS:
        return False
    return not limpia.startswith("/static")


def _recurso_de(ruta: str) -> tuple[str | None, str | None]:
    """
    El recurso y su identificador, deducidos de la ruta.

    `/api/devices/42` -> ('devices', '42')
    `/api/incidents/abc-123/comentarios` -> ('incidents', 'abc-123')

    La idea es que el operador pueda filtrar "todo lo que se hizo con este
    equipo" sin que cada endpoint tenga que declarar su recurso.
    """
    partes = [p for p in ruta.split("?")[0].split("/") if p]
    if not partes:
        return None, None
    if partes[0] != "api":
        return None, None

    # Saltar 'api' y los prefijos tecnicos.
    indice = 1
    while indice < len(partes) and partes[indice] in {"v1", "v2"}:
        indice += 1
    if indice >= len(partes):
        return None, None

    recurso = partes[indice]
    identificador = partes[indice + 1] if indice + 1 < len(partes) else None

    # Un identificador que parece un UUID o un numero; si no, es parte de la
    # ruta ('devices/locations' -> el recurso es 'devices').
    if identificador and not identificador.isdigit() and not _es_uuid(identificador):
        identificador = None

    return recurso, identificador


def _es_uuid(valor: str) -> bool:
    return len(valor) == 36 and valor.count("-") == 4


def _tipo_de_operacion(metodo: str, ruta: str) -> str:
    """LECTURA, ESCRITURA o AUTENTICACION."""
    if "/auth/" in ruta:
        return AUTENTICACION
    if metodo.upper() in _METODOS_DE_ESCRITURA:
        return ESCRITURA
    return LECTURA


def registrar(
    *,
    user_id: str | None = None,
    user_email: str | None = None,
    role_name: str | None = None,
    metodo: str,
    ruta: str,
    resultado: str,
    status_code: int | None = None,
    duracion_ms: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    sesion_id: str | None = None,
    recurso_id: str | None = None,
    datos: dict[str, Any] | None = None,
) -> None:
    """
    Escribe una transaccion.

    NUNCA LANZA
    -----------
    Una bitacora que hace fallar la operacion que audita es un problema: si
    la tabla esta llena, o la base esta lenta, el operador no puede
    trabajar. Un error de auditoria se avisa en el log del servidor y se
    sigue.
    """
    if not _es_operacion(ruta):
        return

    recurso, recurso_de_ruta = _recurso_de(ruta)
    if recurso_id is None:
        recurso_id = recurso_de_ruta

    try:
        from src.db.session import get_engine

        motor = get_engine()
        with motor.begin() as conexion:
            conexion.execute(
                text(
                    """
                    INSERT INTO transacciones (
                        user_id, user_email, role_name, operacion, metodo, ruta,
                        recurso, recurso_id, resultado, status_code, duracion_ms,
                        ip_address, user_agent, request_id, sesion_id, datos
                    ) VALUES (
                        :user_id, :user_email, :role_name, :operacion, :metodo, :ruta,
                        :recurso, :recurso_id, :resultado, :status_code, :duracion_ms,
                        :ip_address, :user_agent, :request_id, :sesion_id, :datos
                    )
                    ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
                    """
                ),
                {
                    "user_id": user_id,
                    "user_email": user_email,
                    "role_name": role_name,
                    "operacion": _tipo_de_operacion(metodo, ruta),
                    "metodo": metodo.upper(),
                    "ruta": ruta[:500],
                    "recurso": recurso,
                    "recurso_id": recurso_id,
                    "resultado": resultado,
                    "status_code": status_code,
                    "duracion_ms": duracion_ms,
                    "ip_address": (ip_address or None) and str(ip_address)[:45],
                    "user_agent": (user_agent or "")[:500] or None,
                    "request_id": request_id,
                    "sesion_id": sesion_id,
                    "datos": json.dumps(datos, ensure_ascii=False, default=str) if datos else None,
                },
            )
    except SQLAlchemyError as exc:
        # La auditoria no puede voltear la operacion que audita.
        logger.warning("No se pudo registrar la transaccion de %s: %s", ruta, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo registrar la transaccion de %s: %s", ruta, exc)


def limpiar_vencidas(dias: int | None = None) -> int:
    """
    Borra las transacciones que ya vencieron.

    Se llama al escribir (de vez en cuando) y se puede llamar desde un job.
    """
    from src.db.session import get_engine

    dias = DIAS_DE_RETENCION if dias is None else dias
    motor = get_engine()
    with motor.begin() as conexion:
        resultado = conexion.execute(
            text("SELECT limpiar_transacciones(:dias)"),
            {"dias": dias},
        )
        return int(resultado.scalar() or 0)
