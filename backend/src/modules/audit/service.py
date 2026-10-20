"""
Service de auditoria: arma la linea de tiempo de lo que paso en el sistema.

POR QUE UNIR DOS TABLAS Y NO MOSTRAR UNA SOLA
----------------------------------------------
El sistema guarda las operaciones en dos lugares, y cada uno tiene la mitad de
la respuesta:

  `movement_history`  -> TODO lo que paso con un equipo fisico: quien lo movio,
                         de que sede a que sede, cuando, si estaba fuera del
                         radio de la sede. Es la trazabilidad del INVENTARIO, y
                         su `user_id` es NOT NULL: un movimiento siempre tiene
                         responsable.

  `audit_logs`        -> Lo que paso con las CUENTAS: altas, ediciones, bajas
                         de usuario, y los login. Lo escribe un trigger de la
                         base para `users` y `roles`, y `core.audit.registrar()`
                         para el resto del sistema.

Quien audita no busca "movement_history" ni "audit_logs": busca que paso con el
equipo, o quien entro al sistema. Si la pantalla lo obligara a elegir la tabla,
le estaria pasando el trabajo de conocer el esquema.

LO QUE ESTA UNION NO ES
-----------------------
No es un JOIN: es una UNION de dos consultas a la misma forma. Las dos tienen
que devolver `fecha, categoria, accion, actor, detalle`, y el detalle es un
TEXTO ya armado. Se arma en SQL y no en el cliente porque el cliente que une
dos listas heterogeneas termina con dos funciones de formato y un orden que solo
funciona con el caso de prueba.

FILTROS: SE APLICAN A LAS DOS RAMAS
-----------------------------------
`usuario_id`, `desde` y `hasta` van en las dos partes de la UNION. Filtrar solo
una seria peor que no filtrar: el resultado pareceria completo y tendria, por
ejemplo, todos los logins del usuario y ningun movimiento suyo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from src.modules.ai.executor import get_ai_engine

# Los tipos de movimiento, en el texto que usa la pantalla. Repetido de
# `movements/router.py` a proposito: este modulo no deberia depender del router
# (es una capa de lectura), y la traduccion es una tabla de tres lineas.
TEXTO_MOVIMIENTO: dict[str, str] = {
    "WAREHOUSE_ENTRY": "Entrada a deposito",
    "WAREHOUSE_REENTRY": "Devolucion a deposito",
    "OUT_TO_CLIENT": "Salida a cliente",
    "INSTALLATION": "Instalacion",
    "REPLACEMENT": "Reemplazo",
    "WITHDRAWAL_TO_WORKSHOP": "Retiro a taller",
    "DISPOSAL": "Baja definitiva",
}

# Las acciones de `audit_logs`, que son un ENUM cerrado.
TEXTO_ACCION: dict[str, str] = {
    "INSERT": "Alta",
    "UPDATE": "Modificacion",
    "DELETE": "Baja",
    "LOGIN": "Inicio de sesion",
    "LOGOUT": "Cierre de sesion",
}


def _conexion():
    return get_ai_engine().connect()


def linea_de_tiempo(
    *,
    limite: int = 100,
    offset: int = 0,
    usuario_id: str | None = None,
    categoria: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    busqueda: str | None = None,
) -> dict[str, Any]:
    """
    Los eventos del sistema, del mas reciente al mas antiguo.

    DOS CONSULTAS Y NO UNA
    ----------------------
    La linea de tiempo son dos SELECT unidos con `UNION ALL` en un subselect, y
    el total es un `COUNT` sobre ese subselect. Escribirlos como una sola
    consulta con `UNION ALL` no se puede filtrar por el alias de la columna
    calculada sin envolver todo, y el `LIMIT` del `UNION` aplica al resultado,
    que es lo que se quiere.
    """
    condiciones_mov: list[str] = ["TRUE"]
    condiciones_audit: list[str] = ["TRUE"]
    parametros: dict[str, Any] = {"limite": limite, "offset": offset}

    if usuario_id:
        condiciones_mov.append("mh.user_id = :usuario_id")
        condiciones_audit.append("al.user_id = CAST(:usuario_id AS uuid)")
        parametros["usuario_id"] = usuario_id

    if desde:
        condiciones_mov.append("mh.movement_date >= :desde")
        condiciones_audit.append("al.created_at >= :desde")
        parametros["desde"] = desde

    if hasta:
        condiciones_mov.append("mh.movement_date <= :hasta")
        condiciones_audit.append("al.created_at <= :hasta")
        parametros["hasta"] = hasta

    if busqueda:
        # El `ILIKE` con el comodin escapado. Sin escapar, un `%` devuelve todo,
        # que en una pantalla de auditoria se ve como "no hay filtro" y hace
        # creer que el rango de fechas no funciona.
        patron = f"%{_escapar_like(busqueda)}%"
        condiciones_mov.append(
            "(d.serial_number ILIKE :busqueda OR mh.observations ILIKE :busqueda "
            "OR u.full_name ILIKE :busqueda)"
        )
        condiciones_audit.append(
            "(al.table_name ILIKE :busqueda OR al.new_value ILIKE :busqueda "
            "OR COALESCE(au.full_name, '') ILIKE :busqueda)"
        )
        parametros["busqueda"] = patron

    # `categoria` separa inventario de cuentas. Es lo que usa el filtro de la
    # pantalla, y se aplica con un `WHERE` externo para no duplicar la logica en
    # las dos ramas.
    filtro_categoria = ""
    if categoria == "inventario":
        filtro_categoria = "WHERE categoria = 'inventario'"
    elif categoria == "sistema":
        filtro_categoria = "WHERE categoria = 'sistema'"

    rama_mov = f"""
        SELECT
            mh.movement_date                         AS fecha,
            'inventario'                             AS categoria,
            'MOVIMIENTO'                             AS tipo,
            mh.movement_type::text                   AS subtipo,
            COALESCE(u.full_name, u.email, 'Usuario eliminado') AS actor,
            u.email                                  AS actor_email,
            d.id                                     AS device_id,
            d.serial_number                          AS serial_number,
            m.brand                                  AS marca,
            m.model                                  AS modelo,
            lo.store_name                            AS origen,
            ld.store_name                            AS destino,
            mh.observations                          AS observaciones,
            mh.is_valid_location                     AS geo_valida,
            mh.id::text                              AS referencia
        FROM movement_history mh
        JOIN devices d    ON d.id = mh.device_id
        JOIN models m     ON m.id = d.model_id
        LEFT JOIN users u ON u.id = mh.user_id
        LEFT JOIN locations lo ON lo.id = mh.origin_location_id
        LEFT JOIN locations ld ON ld.id = mh.destination_location_id
        WHERE {' AND '.join(condiciones_mov)}
    """

    rama_audit = f"""
        SELECT
            al.created_at                            AS fecha,
            'sistema'                                AS categoria,
            al.action::text                          AS tipo,
            al.table_name                            AS subtipo,
            COALESCE(au.full_name, au.email, al.user_id::text, 'Sistema') AS actor,
            au.email                                 AS actor_email,
            NULL::integer                            AS device_id,
            NULL::varchar                            AS serial_number,
            NULL::varchar                            AS marca,
            NULL::varchar                            AS modelo,
            NULL::varchar                            AS origen,
            NULL::varchar                            AS destino,
            al.new_value                             AS observaciones,
            NULL::boolean                            AS geo_valida,
            al.id::text                              AS referencia
        FROM audit_logs al
        LEFT JOIN users au ON au.id = al.user_id
        WHERE {' AND '.join(condiciones_audit)}
    """

    consulta = f"""
        WITH eventos AS (
            {rama_mov}
            UNION ALL
            {rama_audit}
        )
        SELECT * FROM eventos
        {filtro_categoria}
        ORDER BY fecha DESC NULLS LAST
        LIMIT :limite OFFSET :offset
    """

    consulta_total = f"""
        WITH eventos AS (
            {rama_mov}
            UNION ALL
            {rama_audit}
        )
        SELECT COUNT(*) FROM eventos
        {filtro_categoria}
    """

    with _conexion() as conexion:
        filas = conexion.execute(text(consulta), parametros).mappings().all()
        total = conexion.execute(text(consulta_total), parametros).scalar_one()

    return {
        "items": [_a_evento(dict(f)) for f in filas],
        "total": int(total or 0),
        "limit": limite,
        "offset": offset,
    }


def _a_evento(fila: dict[str, Any]) -> dict[str, Any]:
    """Traduce una fila cruda al evento que consume la pantalla."""
    es_inventario = fila["categoria"] == "inventario"

    if es_inventario:
        accion = TEXTO_MOVIMIENTO.get(fila["subtipo"], fila["subtipo"])
        objeto = fila.get("serial_number") or "—"
    else:
        accion = TEXTO_ACCION.get(fila["tipo"], fila["tipo"])
        objeto = fila.get("subtipo") or "—"

    return {
        "fecha": fila["fecha"],
        "categoria": fila["categoria"],
        "accion": accion,
        "tipo": fila["tipo"],
        "subtipo": fila["subtipo"],
        "actor": fila.get("actor"),
        "actor_email": fila.get("actor_email"),
        "device_id": fila.get("device_id"),
        "serial_number": fila.get("serial_number"),
        "marca": fila.get("marca"),
        "modelo": fila.get("modelo"),
        "origen": fila.get("origen"),
        "destino": fila.get("destino"),
        "observaciones": fila.get("observaciones"),
        "geo_valida": fila.get("geo_valida"),
        "objeto": objeto,
        "referencia": fila.get("referencia"),
    }


def resumen() -> dict[str, Any]:
    """
    Las cifras del encabezado de auditoria.

    Cuatro numeros que responden "que tan activo esta el sistema" antes de
    mirar la lista: cuanto se movio hoy, en la semana, y quienes son los tres
    usuarios que mas operaron. Sin esto, la pantalla abre con una lista larga y
    el auditor tiene que contar a mano.
    """
    with _conexion() as conexion:
        hoy = conexion.execute(
            text(
                """
                SELECT COUNT(*)
                FROM movement_history
                WHERE movement_date >= NOW() - INTERVAL '24 hours'
                """
            )
        ).scalar_one()

        semana = conexion.execute(
            text(
                """
                SELECT COUNT(*)
                FROM movement_history
                WHERE movement_date >= NOW() - INTERVAL '7 days'
                """
            )
        ).scalar_one()

        total_mov = conexion.execute(
            text("SELECT COUNT(*) FROM movement_history")
        ).scalar_one()

        logins_semana = conexion.execute(
            text(
                """
                SELECT COUNT(*)
                FROM audit_logs
                WHERE action = 'LOGIN' AND created_at >= NOW() - INTERVAL '7 days'
                """
            )
        ).scalar_one()

        # Los tres que mas movieron en la semana. `LEFT JOIN` porque un usuario
        # dado de baja (soft delete) sigue siendo el autor de sus movimientos, y
        # esconder el nombre convertiria el ranking en "y tres anonimos".
        top = conexion.execute(
            text(
                """
                SELECT
                    COALESCE(u.full_name, u.email, 'Usuario eliminado') AS nombre,
                    COUNT(*) AS movimientos
                FROM movement_history mh
                LEFT JOIN users u ON u.id = mh.user_id
                WHERE mh.movement_date >= NOW() - INTERVAL '7 days'
                GROUP BY u.full_name, u.email
                ORDER BY movimientos DESC
                LIMIT 3
                """
            )
        ).mappings().all()

    return {
        "movimientos_hoy": int(hoy or 0),
        "movimientos_semana": int(semana or 0),
        "movimientos_totales": int(total_mov or 0),
        "logins_semana": int(logins_semana or 0),
        "top_usuarios": [dict(f) for f in top],
    }


def _escapar_like(texto: str) -> str:
    r"""Escapa los comodines de LIKE para que se busquen como texto literal."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
