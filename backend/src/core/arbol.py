"""
Armado de árboles a partir de una lista plana con `parent_id`.

POR QUE Está EN `core` Y NO EN CADA Módulo
------------------------------------------
Categorías y ubicaciones tienen la misma forma: una tabla con `parent_id`, un
nombre, y una jerarquía de dos o tres niveles. La lógica de armar el camino
("Depósito Central > Pasillo 3") y de anidar las hijas es Idéntica en los dos
casos. Copiarla es copiar el bug.

COMO SE CALCULA EL CAMINO
-------------------------
En Python, no con un CTE recursivo por fila. Son pocas filas (decenas de
categorías, decenas de ubicaciones) y armarlo en memoria es más simple de
leer y de debuggear.

El límite de profundidad es la red de seguridad contra un CICLO en los datos:
si alguien edita a mano un `parent_id` y deja un anillo, el recorrido no
termina. Con un árbol sano de tres niveles, nunca se llega al límite.
"""

from __future__ import annotations

from typing import Any, Callable

# A partir de está profundidad se corta el recorrido. Un árbol sano de
# categorías o de ubicaciones tiene 2 o 3 niveles: 10 es holgado.
PROFUNDIDAD_MAXIMA = 10


def rutas_por_padres(
    filas: list[dict[str, Any]],
    *,
    nombre: str = "name",
    padre: str = "parent_id",
) -> dict[int, str]:
    """
    Devuelve `{id: "A > B > C"}` para cada fila.

    Las filas que no tienen padre quedan con su propio nombre.
    """
    por_id = {f["id"]: f for f in filas}
    rutas: dict[int, str] = {}

    def construir(fila_id: int, profundidad: int = 0) -> str:
        if fila_id in rutas:
            return rutas[fila_id]
        if profundidad > PROFUNDIDAD_MAXIMA:
            return ""
        fila = por_id.get(fila_id)
        if not fila:
            return ""
        id_padre = fila.get(padre)
        if not id_padre:
            rutas[fila_id] = fila[nombre]
            return rutas[fila_id]
        prefijo = construir(id_padre, profundidad + 1)
        rutas[fila_id] = f"{prefijo} > {fila[nombre]}" if prefijo else fila[nombre]
        return rutas[fila_id]

    for fila in filas:
        construir(fila["id"])

    return rutas


def anidar(
    filas: list[dict[str, Any]],
    *,
    nombre: str = "name",
    padre: str = "parent_id",
    hijos: str = "hijas",
    ordenar_por: Callable[[dict[str, Any]], Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Convierte una lista plana en un árbol, mutando las filas.

    Cada nodo queda con su lista `hijas` (la clave se puede cambiar). Las
    filas que no tienen padre —o cuyo padre no está en la lista— quedan en la
    raíz: un padre que no está en la lista es un dato roto, y esconderlo
    haria que el nodo desapareciera de la pantalla.
    """
    por_id: dict[int, dict[str, Any]] = {f["id"]: {**f, hijos: []} for f in filas}

    raices: list[dict[str, Any]] = []
    for f in filas:
        nodo = por_id[f["id"]]
        id_padre = f.get(padre)
        if id_padre and id_padre in por_id:
            por_id[id_padre][hijos].append(nodo)
        else:
            raices.append(nodo)

    def ordenar(nodos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for nodo in nodos:
            nodo[hijos] = ordenar(nodo[hijos])
        if ordenar_por is None:
            return sorted(nodos, key=lambda n: str(n.get(nombre) or ""))
        return sorted(nodos, key=ordenar_por)

    return ordenar(raices)
