"""
Como se le pasan las filas al modelo.

ESTO SE Probó CON TRES FORMATOS. EL TERCERO ES EL QUE QUEDA.
-----------------------------------------------------------
La eleccion no fue estetica: los tres se probaron contra llama3.1:8b con la
misma consulta, y el primero y el segundo produjeron el mismo error.

  1. JSON de diccionarios  ->  `{"equipos_activos": 5, "en_reparación": 1}`
     El modelo alineo mal las etiquetas y escribio "no hay equipos en
     reparación" habiendo uno.

  2. Tabla posicional      ->  `COLUMNAS: a | b | c` / `fila 1: 5 | 1 | 1`
     Peor. Sin etiqueta al lado de cada valor, el modelo tiene que contar
     posiciones de memoria y no puede. Volvio a equivocarse.

  3. Vertical etiquetado   ->  `en_reparación: 1` en una línea por valor
     Correcto.

POR QUE EL TERCERO FUNCIONA Y LOS OTROS NO
-----------------------------------------
Un número suelto necesita su nombre pegado. El formato 2 separa el nombre de
su número, y separar nombre y valor es exactamente lo que un modelo de 8
parámetros no sostiene bien: son dos operaciones en lugar de una.

En el 3 cada línea es una sola operación de lectura, y no hay nada que contar
ni que alinear. Sale caro en tokens solo cuando hay MUCHAS filas, y para los
agregados —que son la mayoria de las preguntas— hay pocas columnas.

Límite DE LA Garantía
---------------------
Esto reduce el error de lectura; no lo elimina. Un modelo chico puede
inventar. Por eso el número SIEMPRE viene del campo `datos` de la respuesta y
el texto es solo la glosa. Si discrepan, la fuente de verdad es `datos`.
"""

from typing import Any

MAX_FILAS_TABLA = 60
# Por encima de esto, el formato vertical se vuelve carisimo en tokens: cada
# fila repite los nombres de todas las columnas. A partir de acá se pasa a
# tabla posicional, que para列出 muchas filas es lo correcto.
UMBRAL_FORMATO_VERTICAL = 12


def formatear_tabla(
    columnas: list[str],
    filas: list[dict[str, Any]],
    max_filas: int = MAX_FILAS_TABLA,
) -> str:
    if not filas:
        return "(la consulta no devolvio filas)"

    if len(columnas) <= UMBRAL_FORMATO_VERTICAL and len(filas) == 1:
        return _vertical(columnas, filas[0])

    return _horizontal(columnas, filas[:max_filas], len(filas))


def _vertical(columnas: list[str], fila: dict[str, Any]) -> str:
    """Un `nombre: valor` por línea. La etiqueta pegada a su número."""
    return "\n".join(
        # "-" para los nulos: un 0 y un NULL son cosas distintas y el modelo
        # cuenta un NULL como un equipo que existe pero no está en ningún lado.
        f"- {columna}: {fila.get(columna) if fila.get(columna) is not None else 'sin dato'}"
        for columna in columnas
    )


def _horizontal(columnas: list[str], filas: list[dict[str, Any]], total: int) -> str:
    """Tabla clasica, para cuando hay muchas filas."""
    lineas = ["| " + " | ".join(columnas) + " |"]
    lineas.append("|" + "|".join(["---"] * len(columnas)) + "|")

    for fila in filas:
        celdas = ["" if fila.get(c) is None else str(fila.get(c)) for c in columnas]
        lineas.append("| " + " | ".join(celdas) + " |")

    if total > len(filas):
        lineas.append(f"\n(se muestran {len(filas)} de {total} filas)")

    return "\n".join(lineas)
