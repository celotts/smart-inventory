"""
Ubicaciones: bodegas, pasillos, estantes, bins y sitios de cliente.

QUE ES UNA UBICACION ACA
------------------------
Un lugar fisico donde esta (o estuvo) un equipo. "Deposito Central" es una
bodega; "Pasillo 3" cuelga de una bodega; "Estante A" cuelga de un pasillo.
En el sitio del cliente, la ubicacion tiene `client_id`.

LA JERARQUIA
------------
`locations.parent_id` cuelga una ubicacion de otra. El filtro por
"Deposito Central" trae sus pasillos y estantes: el operador pregunta por la
BODEGA, no por cada estante.

El filtro se resuelve con `_ids_descendientes`, que usa un CTE recursivo. La
alternativa (guardar el path completo en cada fila) obliga a reescribir todas
las hijas cuando una bodega se renombra, y el renombre de una bodega es una
operacion de la que nadie se acuerda hasta que es tarde.

EL TIPO
-------
`location_type` distingue WAREHOUSE (bodega), AISLE (pasillo), SHELF
(estante), BIN (bin) y CLIENT (sitio del cliente). No es decoracion: un
filtro por "todas las bodegas" no debe traer los equipos instalados en el
cliente, porque esos no se mueven con una carretilla del deposito.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from src.db.session import get_db_session

# Los tipos que la API acepta. El CHECK de Postgres es la ultima linea de
# defensa; esta lista es la que la API valida y la que el frontend usa para
# los `<select>`.
TIPOS = ("WAREHOUSE", "AISLE", "SHELF", "BIN", "CLIENT")

# Etiquetas legibles, para la interfaz.
ETIQUETAS_TIPO = {
    "WAREHOUSE": "Bodega",
    "AISLE": "Pasillo",
    "SHELF": "Estante",
    "BIN": "Bin",
    "CLIENT": "Sitio del cliente",
}


class UbicacionInvalida(ValueError):
    """Regla de negocio de ubicaciones (padre inexistente, ciclo, etc.)."""


def _ruta_de_padres(filas: list[dict[str, Any]]) -> dict[int, str]:
    """
    Calcula la ruta ("Deposito Central > Pasillo 3") de cada ubicacion.

    Se hace en Python y no con un CTE recursivo por fila: son pocas
    ubicaciones, y armarlo en memoria es mas simple de leer y de debuggear que
    una consulta recursiva por cada fila.
    """
    por_id = {f["id"]: f for f in filas}
    rutas: dict[int, str] = {}

    def construir(ubicacion_id: int, profundidad: int = 0) -> str:
        if ubicacion_id in rutas:
            return rutas[ubicacion_id]
        # El limite de 10 es una red de seguridad contra un ciclo en los datos
        # (alguien edita a mano el parent_id y queda un anillo). Con un arbol
        # sano de 3 niveles, nunca se llega.
        if profundidad > 10:
            return ""
        fila = por_id.get(ubicacion_id)
        if not fila:
            return ""
        padre = fila.get("parent_id")
        if not padre:
            rutas[ubicacion_id] = fila["store_name"]
            return rutas[ubicacion_id]
        prefijo = construir(padre, profundidad + 1)
        rutas[ubicacion_id] = f"{prefijo} > {fila['store_name']}" if prefijo else fila["store_name"]
        return rutas[ubicacion_id]

    for fila in filas:
        construir(fila["id"])

    return rutas


def listar_ubicaciones(solo_activas: bool = True) -> list[dict[str, Any]]:
    """Todas las ubicaciones, con su ruta y su tipo."""
    db = get_db_session()
    try:
        condiciones = ["1 = 1"]
        if solo_activas:
            condiciones.append("l.is_active = TRUE")

        filas = db.execute(
            text(
                f"""
                SELECT l.id, l.store_name, l.address, l.city, l.client_id,
                       l.latitude, l.longitude, l.tolerance_radius_meters,
                       l.is_active, l.parent_id, l.location_type,
                       c.commercial_name AS cliente
                FROM locations l
                LEFT JOIN clients c ON c.id = l.client_id
                WHERE {" AND ".join(condiciones)}
                ORDER BY l.store_name
                """
            )
        ).mappings().all()

        datos = [dict(f) for f in filas]
        rutas = _ruta_de_padres(datos)
        for d in datos:
            d["ruta"] = rutas.get(d["id"], d["store_name"])
            d["tipo_etiqueta"] = ETIQUETAS_TIPO.get(d["location_type"], d["location_type"])
        return datos
    finally:
        db.close()


def arbol_ubicaciones() -> list[dict[str, Any]]:
    """
    Las ubicaciones en arbol: cada una con sus hijas.

    La lista plana de arriba es la que usan los selects (con la ruta en texto);
    esta es la que usa la pantalla de ubicaciones, donde el operador quiere
    ver la estructura.
    """
    todas = listar_ubicaciones()
    por_id: dict[int, dict[str, Any]] = {u["id"]: {**u, "hijas": []} for u in todas}

    raices: list[dict[str, Any]] = []
    for u in por_id.values():
        padre = u.get("parent_id")
        if padre and padre in por_id:
            por_id[padre]["hijas"].append(u)
        else:
            raices.append(u)

    def ordenar(nodos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for nodo in nodos:
            nodo["hijas"] = ordenar(nodo["hijas"])
        return sorted(nodos, key=lambda n: n["store_name"])

    return ordenar(raices)


def ids_descendientes(ubicacion_id: int) -> list[int]:
    """
    La ubicacion y todas sus hijas.

    Es lo que hace que el filtro por "Deposito Central" traiga tambien lo que
    esta en "Deposito Central > Pasillo 3 > Estante A".
    """
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                WITH RECURSIVE descendientes AS (
                    SELECT id FROM locations WHERE id = :ubicacion_id
                    UNION ALL
                    SELECT l.id
                    FROM locations l
                    JOIN descendientes d ON l.parent_id = d.id
                )
                SELECT id FROM descendientes
                """
            ),
            {"ubicacion_id": ubicacion_id},
        ).scalars().all()
        return [int(f) for f in filas]
    finally:
        db.close()


def ids_descendientes_categoria(categoria_id: int) -> list[int]:
    """La categoria y todas sus subcategorias."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                WITH RECURSIVE descendientes AS (
                    SELECT id FROM categories WHERE id = :categoria_id
                    UNION ALL
                    SELECT c.id
                    FROM categories c
                    JOIN descendientes d ON c.parent_id = d.id
                )
                SELECT id FROM descendientes
                """
            ),
            {"categoria_id": categoria_id},
        ).scalars().all()
        return [int(f) for f in filas]
    finally:
        db.close()


def crear_ubicacion(datos: dict[str, Any]) -> dict[str, Any]:
    """Alta de una ubicacion."""
    nombre = (datos.get("store_name") or "").strip()
    if not nombre:
        raise UbicacionInvalida("El nombre de la ubicacion es obligatorio.")

    tipo = (datos.get("location_type") or "WAREHOUSE").upper()
    if tipo not in TIPOS:
        raise UbicacionInvalida(
            f"Tipo de ubicacion desconocido: {tipo!r}. Validos: {', '.join(TIPOS)}."
        )

    parent_id = datos.get("parent_id")
    if parent_id:
        _verificar_padre(parent_id)

    db = get_db_session()
    try:
        ubicacion_id = db.execute(
            text(
                """
                INSERT INTO locations (
                    client_id, store_name, address, city, latitude, longitude,
                    tolerance_radius_meters, is_active, parent_id, location_type
                )
                VALUES (
                    :client_id, :store_name, :address, :city, :latitude, :longitude,
                    :tolerance, :is_active, :parent_id, :location_type
                )
                RETURNING id
                """
            ),
            {
                "client_id": datos.get("client_id"),
                "store_name": nombre,
                "address": (datos.get("address") or "").strip() or nombre,
                "city": datos.get("city"),
                "latitude": datos.get("latitude"),
                "longitude": datos.get("longitude"),
                "tolerance": datos.get("tolerance_radius_meters") or 50,
                "is_active": datos.get("is_active", True),
                "parent_id": parent_id,
                "location_type": tipo,
            },
        ).scalar_one()
        db.commit()
    finally:
        db.close()

    return obtener_ubicacion(ubicacion_id)


def actualizar_ubicacion(ubicacion_id: int, datos: dict[str, Any]) -> dict[str, Any]:
    """
    Edicion de una ubicacion.

    El `parent_id` NO se puede poner a si misma, y un hijo no puede colgar de
    su propio descendiente: eso dejaria un ciclo, y el filtro por
    descendientes se colgaria solo.
    """
    parent_id = datos.get("parent_id")
    if parent_id:
        if int(parent_id) == int(ubicacion_id):
            raise UbicacionInvalida("Una ubicacion no puede ser su propia ubicacion padre.")
        descendientes = ids_descendientes(ubicacion_id)
        if int(parent_id) in descendientes:
            raise UbicacionInvalida(
                "Una ubicacion no puede colgar de una de sus propias sububicaciones."
            )
        _verificar_padre(parent_id)

    tipo = datos.get("location_type")
    if tipo:
        tipo = tipo.upper()
        if tipo not in TIPOS:
            raise UbicacionInvalida(
                f"Tipo de ubicacion desconocido: {tipo!r}. Validos: {', '.join(TIPOS)}."
            )

    db = get_db_session()
    try:
        db.execute(
            text(
                """
                UPDATE locations SET
                    client_id = COALESCE(:client_id, client_id),
                    store_name = COALESCE(:store_name, store_name),
                    address = COALESCE(:address, address),
                    city = COALESCE(:city, city),
                    tolerance_radius_meters = COALESCE(:tolerance, tolerance_radius_meters),
                    is_active = COALESCE(:is_active, is_active),
                    parent_id = :parent_id,
                    location_type = COALESCE(:location_type, location_type)
                WHERE id = :id AND deleted_at IS NULL
                """
            ),
            {
                "id": ubicacion_id,
                "client_id": datos.get("client_id"),
                "store_name": (datos.get("store_name") or "").strip() or None,
                "address": datos.get("address"),
                "city": datos.get("city"),
                "tolerance": datos.get("tolerance_radius_meters"),
                "is_active": datos.get("is_active"),
                "parent_id": parent_id,
                "location_type": tipo,
            },
        )
        db.commit()
    finally:
        db.close()

    return obtener_ubicacion(ubicacion_id)


def obtener_ubicacion(ubicacion_id: int) -> dict[str, Any]:
    """Una ubicacion, con su ruta."""
    for u in listar_ubicaciones(solo_activas=False):
        if u["id"] == int(ubicacion_id):
            return u
    raise UbicacionInvalida("No existe esa ubicacion.")


def _verificar_padre(parent_id: int) -> None:
    """El padre tiene que existir. Un padre que no existe deja una huérfana."""
    db = get_db_session()
    try:
        existe = db.execute(
            text("SELECT 1 FROM locations WHERE id = :id AND deleted_at IS NULL"),
            {"id": parent_id},
        ).first()
        if not existe:
            raise UbicacionInvalida("La ubicacion padre no existe.")
    finally:
        db.close()
