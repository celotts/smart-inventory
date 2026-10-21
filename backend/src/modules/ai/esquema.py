"""
El esquema que ve el modelo, GENERADO desde la base real.

POR QUE NO ES UN TEXTO ESCRITO A MANO
--------------------------------------
Porque se desactualiza. Tres veces en este repo: el esquema declaraba
`models.list_price` y no `models.purchase_value`, no tenia
`devices.purchase_date`, y el modelo escribia consultas con columnas que
NO EXISTEN (`m.category_name`, `r.deleted_at`). Cada una era un 500 en el
endpoint de la IA, con un error que no dice "falta una columna en el
esquema" sino "no such column".

Un esquema escrito a mano es una copia: se copia un dia y al otro ya no
copia. Leido de la base, no hay nada que sincronizar.

LO QUE EL MODELO VE
-------------------
Lo mismo de antes, con tres filtros:

  1. SOLO las tablas permitidas (las del `guard`): el modelo no puede
     escribir SQL contra una tabla que no puede consultar.
  2. SIN las columnas vetadas (`password`, `old_value`, `new_value` y las de
     contacto): son las que el `guard` no deja leer, asi que no tiene por
     que verlas ni para inventarlas.
  3. SIN las columnas de auditoria del actor (`created_by_user_id` y
     familia): no aportan a una pregunta de inventario y si aparecen, el
     modelo las joins por gusto.

EL CACHE
--------
Leer `information_schema` en cada pregunta es un viaje de mas a la base por
un prompt. El esquema cambia cuando cambia una migracion (poco), asi que se
cachea unos minutos.

EL FALLBACK
-----------
Si la base no esta disponible (un test sin Docker, por ejemplo), se usa el
esquema escrito a mano de `schema_context.py`. Es una copia, y por eso hay
un test que verifica que la copia este sincronizada con la base.
"""

from __future__ import annotations

import time
from typing import Any

from src.db.session import get_engine
from src.modules.ai.guard import (
    _COLUMNAS_PROHIBIDAS,
    tablas_permitidas,
)

# Cuanto se cachea el esquema, en segundos.
TTL_SEGUNDOS = 300

# Las columnas de auditoria del actor: no aportan a una pregunta de
# inventario, y si el modelo las ve, las une por gusto.
COLUMNAS_DE_AUDITORIA = {
    "created_by_user_id",
    "updated_by_user_id",
    "deleted_by_user_id",
    "created_by_role_id",
    "updated_by_role_id",
    "deleted_by_role_id",
}

# El cache: (momento_de_lectura, esquema_generado).
_cache: tuple[float, str] | None = None


def _columnas_permitidas(nombre: str) -> bool:
    """
    ¿Esta columna se le puede mostrar al modelo?

    Las vetadas por el `guard` no: si el modelo no puede leerlas, no tiene
    por que verlas, y verlas lo tienta a escribirlas.
    """
    if nombre in _COLUMNAS_PROHIBIDAS:
        return False
    if nombre in COLUMNAS_DE_AUDITORIA:
        return False
    return True


def _tipo_de(columna: dict[str, Any]) -> str:
    """El tipo, en el formato que el modelo entiende."""
    tipo = columna.get("data_type") or "TEXT"
    if tipo == "character varying":
        largo = columna.get("character_maximum_length")
        return f"VARCHAR({largo})" if largo else "VARCHAR"
    if tipo == "numeric":
        precision = columna.get("numeric_precision")
        escala = columna.get("numeric_scale")
        if precision and escala is not None:
            return f"NUMERIC({precision},{escala})"
        return "NUMERIC"
    if tipo == "timestamp with time zone":
        return "TIMESTAMPTZ"
    if tipo == "USER-DEFINED":
        # Los ENUM: el nombre del tipo, que el modelo ya conoce.
        return (columna.get("udt_name") or "TEXT").upper()
    if tipo == "ARRAY":
        return f"{str(columna.get('udt_name') or 'TEXT').upper()}[]"
    return tipo.upper()


def _generar_desde_la_base() -> str | None:
    """
    El esquema leido de la base. `None` si no se pudo.

    El formato es DDL real (`CREATE TABLE ...`), que es lo que el modelo
    parsea mejor: tiene el Postgres en sus datos de entrenamiento.

    NUNCA LANZA
    -----------
    Si la base no esta (un test en el host, la API arrancando antes que
    Postgres), se devuelve `None` y el que llama usa el esquema de a mano.
    """
    permitidas = tablas_permitidas()
    if not permitidas:
        return None

    try:
        motor = get_engine()
        with motor.connect() as conexion:
            from sqlalchemy import text

            filas = conexion.execute(
                text(
                    """
                    SELECT c.table_name,
                           c.column_name,
                           c.data_type,
                           c.udt_name,
                           c.character_maximum_length,
                           c.numeric_precision,
                           c.numeric_scale,
                           c.is_nullable,
                           c.column_default
                    FROM information_schema.columns c
                    JOIN information_schema.tables t
                      ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                    WHERE c.table_schema = 'public'
                      AND c.table_name = ANY(:tablas)
                      AND t.table_type = 'BASE TABLE'
                    ORDER BY c.table_name, c.ordinal_position
                    """
                ),
                {"tablas": list(permitidas)},
            ).mappings().all()
    except Exception:  # noqa: BLE001 - sin base, el esquema de a mano
        return None

    if not filas:
        return None

    por_tabla: dict[str, list[dict[str, Any]]] = {}
    for fila in filas:
        if not _columnas_permitidas(fila["column_name"]):
            continue
        por_tabla.setdefault(fila["table_name"], []).append(dict(fila))

    if not por_tabla:
        return None

    partes = []
    for tabla in sorted(por_tabla):
        columnas = []
        for columna in por_tabla[tabla]:
            tipo = _tipo_de(columna)
            nombre = columna["column_name"]
            if columna["is_nullable"] == "NO":
                columnas.append(f"    {nombre} {tipo} NOT NULL")
            else:
                columnas.append(f"    {nombre} {tipo}")
        partes.append(
            f"CREATE TABLE {tabla} (\n" + ",\n".join(columnas) + "\n);"
        )

    return "\n\n".join(partes)


def esquema_del_sistema() -> str:
    """
    El esquema que va en el prompt.

    De la base si se puede; el de a mano si no. Nunca se lanza: un esquema
    que no se pudo leer no puede hacer fallar la consulta entera.
    """
    global _cache

    ahora = time.monotonic()
    if _cache is not None:
        momento, esquema = _cache
        if ahora - momento < TTL_SEGUNDOS:
            return esquema

    esquema = None
    try:
        esquema = _generar_desde_la_base()
    except Exception:  # noqa: BLE001 - sin base se usa el esquema de a mano
        esquema = None

    if esquema is None:
        from src.modules.ai.schema_context import ESQUEMA_SQL

        esquema = ESQUEMA_SQL

    _cache = (ahora, esquema)
    return esquema


def limpiar_cache() -> None:
    """Olvida el esquema cacheado (los tests lo necesitan)."""
    global _cache
    _cache = None
