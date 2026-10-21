"""
Registro de acciones sensibles en la bitacora.

POR QUE ESTE ARCHIVO ESCRIBIA EN LA TABLA EQUIVOCADA
-----------------------------------------------------
Habian DOS tablas de auditoria en el esquema, y el código escribia en dos
distintas:

  `audit_logs` (plural)  -> la que escribe `users/service.py`. La que crea
                            `init.sql` y la que tiene el trigger que guarda el
                            ROL del actor en el momento de la accion.
  `audit_log`  (singular) -> la que escribia ESTE archivo. La creo la
                            migracion 0004.

Consecuencia: la bitacora de Crear/Desactivar usuario estaba en una, y la de
escanear/transferir en la otra. Ningun reporte las podia mostrar juntas, porque
no hay consulta que las una. Y la tabla más rica —la que puede decir QUE ROL
tenia el actor cuando lo hizo— perdia la mitad de los eventos.

La migracion 0006 copia las filas de la tabla vieja a la buena y la dropea. Aca
se escribe en `audit_logs`, que es la que sobrevive.

EL PORQUE DE LAS COLUMNAS QUE NO SON OBVIAS
-------------------------------------------
`audit_logs` no tiene `resource`, ni `request_id`, ni `metadata` JSONB: tiene
`table_name`, `record_id`, `old_value` y `new_value`. El mapeo es:

  table_name  <- `resource` o el nombre de la tabla de la accion
  new_value   <- el `metadata` serializado

`record_id` queda NULL a proposito. Es un UUID y casi ninguna de las acciones
que se auditan tienen un UUID (los `devices.id` son enteros, los `transfers.id`
son UUID pero no todos), y poner un número en una columna UUID revienta el
INSERT. Un `NULL` honesto es mejor que un id inventado.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.modules.ai.executor import get_ai_engine

# Acciones que van a la bitacora. El ENUM `audit_action` de la base tiene solo
# INSERT/UPDATE/DELETE/LOGIN/LOGOUT, y `action` es NOT NULL, asi que una accion
# de dominio como "ESCANEO" se guarda como UPDATE con el detalle en
# `new_value`. Filtrar por `action` y no por el texto es lo que permite seguir
# confiando en la columna.
_ACCIONES_VALIDAS = {"INSERT", "UPDATE", "DELETE", "LOGIN", "LOGOUT"}


def registrar(
    *,
    user_id: str | None,
    action: str,
    resource: str | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Escribe una fila en `audit_logs`.

    NUNCA propaga la excepcion. Un registro de auditoria que tumba la operacion
    que estaba auditando es peor que no auditar: convierte "el usuario no pudo
    desactivar la cuenta" en "no se que paso". El error se loguea y se sigue.

    `action` se normaliza a un valor del ENUM antes de insertar. Sin eso, una
    accion de dominio que se pase sin mapear revienta el INSERT con
    "invalid input value for enum audit_action", que es un 500 en el camino
    calento de una operacion que en realidad funciono.
    """
    normalizada = _normalizar_accion(action)

    if normalizada is None:
        # Se avisa por log y no se escribe: perder una fila es visible; perder
        # la operación, no.
        import logging

        logging.getLogger(__name__).warning(
            "accion de auditoria invalida %r, no se registro (resource=%s)",
            action,
            resource,
        )
        return

    # El ENUM no acepta una cadena cualquiera. El mapeo de las acciones de
    # dominio cae en UPDATE, que es el contenedor generico, y el detalle real
    # viaja en `new_value` donde se puede buscar igual.
    detalle: dict[str, Any] = dict(metadata or {})
    if request_id:
        detalle["request_id"] = request_id
    if resource:
        detalle["resource"] = resource

    try:
        with get_ai_engine().begin() as conexion:
            conexion.execute(
                text(
                    """
                    INSERT INTO audit_logs (
                        action, table_name, record_id, new_value,
                        user_id, ip_address, created_at
                    )
                    VALUES (
                        CAST(:action AS audit_action), :tabla, NULL, :detalle,
                        :user_id, :ip, NOW()
                    )
                    """
                ),
                {
                    "action": normalizada,
                    "tabla": (resource or "desconocido")[:100],
                    "detalle": json.dumps(detalle, default=str),
                    # `audit_logs.user_id` es UUID y sin FK: un id de usuario es
                    # un dato del request, y un id inválido no debe romper la
                    # escritura de la bitacora. `None` viaja como NULL.
                    "user_id": user_id,
                    "ip": ip_address,
                },
            )
    except SQLAlchemyError:
        # Ver el docstring: la auditoría no puede tumbar la operación.
        import logging

        logging.getLogger(__name__).exception("No se pudo escribir la bitacora")


def _normalizar_accion(action: str) -> str | None:
    """Lleva cualquier accion de dominio a un valor del ENUM, o None."""
    if not action:
        return None
    if action in _ACCIONES_VALIDAS:
        return action

    # Un texto con la accion de dominio en el detalle es lo que hace que la
    # fila siga siendo filtrable: `metadata` lleva `{"accion": "ESCANEO"}`.
    return "UPDATE"
