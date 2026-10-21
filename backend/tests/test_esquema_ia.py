"""
El esquema que ve el modelo tiene que coincidir con la base.

POR QUE UN TEST
---------------
El esquema que se le pasa al modelo estaba escrito a mano, en
`schema_context.py`. Una copia se desactualiza: paso tres veces (faltaban
`devices.purchase_date`, `models.purchase_value` y `locations.location_type`,
que la copia declaraba y la base NO tiene).

Con acento, el modelo escribia consultas con columnas que no existen
(`m.category_name`, `r.deleted_at`) y la peticion devolvia un 500 con un
error que no decia "falta una columna en el esquema" sino "no such column".

EL GENERADOR Y EL FALLBACK
--------------------------
Hoy el esquema que se usa se LEE de la base (`src.modules.ai.esquema`), asi
que no puede quedar viejo. El de a mano queda como FALLBACK, para cuando no
hay base (un test sin Docker).

Este test verifica que el FALLBACK este sincronizado: si no, el fallback
serve para cuando no se quiere (precisamente cuando no hay base) y teaches
al modelo columnas que no existen.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.ai.schema_context import ESQUEMA_SQL  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1] / "src"

# Una tabla del DDL declarado.
TABLA_DECLARADA = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\);", re.S)


def _columnas_declaradas() -> dict[str, set[str]]:
    """Las columnas que declara el esquema escrito a mano."""
    declaradas: dict[str, set[str]] = {}
    for tabla, cuerpo in TABLA_DECLARADA.findall(ESQUEMA_SQL):
        columnas = set()
        for linea in cuerpo.split("\n"):
            limpia = linea.split("--")[0].strip()
            if not limpia:
                continue
            coincidencia = re.match(r"(\w+)\s", limpia)
            if coincidencia:
                columnas.add(coincidencia.group(1))
        declaradas[tabla] = columnas
    return declaradas


def _columnas_reales() -> dict[str, set[str]] | None:
    """Las columnas de la base. `None` si no hay base."""
    import os

    url = os.getenv("DATABASE_URL_TEST") or os.getenv("DATABASE_URL")
    if not url:
        return None

    from sqlalchemy import create_engine, text

    try:
        motor = create_engine(url, pool_pre_ping=True)
        with motor.connect() as conexion:
            filas = conexion.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                    """
                )
            ).all()
    except Exception:  # noqa: BLE001 - sin base no hay nada que comparar
        return None

    reales: dict[str, set[str]] = {}
    for tabla, columna in filas:
        reales.setdefault(tabla, set()).add(columna)
    return reales


class TestEsquemaSincronizado:
    def test_el_fallback_no_declara_columnas_que_no_existen(self):
        """
        El esquema de a mano no puede declarar columnas que la base no tiene.

        Una columna declarada y inexistente es PEOR que una columna que
        falta: el modelo la usa, la consulta no arranca, y el error dice
        "no such column" sin decir de donde salio el nombre.
        """
        reales = _columnas_reales()
        if reales is None:
            pytest.skip("No hay base para comparar el esquema.")

        declaradas = _columnas_declaradas()
        problemas = []
        for tabla, columnas in declaradas.items():
            if tabla not in reales:
                continue
            inventadas = columnas - reales[tabla]
            if inventadas:
                problemas.append(
                    f"{tabla}: el esquema declara {sorted(inventadas)}, "
                    "y la tabla no las tiene"
                )

        assert not problemas, (
            "El esquema de respaldo declara columnas que no existen. Un modelo "
            "que las ve escribe consultas que no arrancan.\n  "
            + "\n  ".join(problemas)
        )

    def test_el_esquema_generado_tiene_las_tablas_permitidas(self):
        """
        El esquema que se genera de la base tiene las tablas permitidas.

        Y SOLO esas: si el modelo ve una tabla que el guard no deja
        consultar, escribe contra ella y la consulta se rechaza.
        """
        from src.modules.ai.esquema import _generar_desde_la_base
        from src.modules.ai.guard import tablas_permitidas

        generado = _generar_desde_la_base()
        if generado is None:
            pytest.skip("No hay base para generar el esquema.")

        declaradas = set(re.findall(r"CREATE TABLE (\w+)", generado))
        permitidas = tablas_permitidas()

        # Las tablas que el guard permite y que existen en la base.
        reales = _columnas_reales() or {}
        esperadas = {t for t in permitidas if t in reales}

        assert declaradas == esperadas, (
            "El esquema generado no coincide con las tablas permitidas.\n"
            f"  de mas: {sorted(declaradas - esperadas)}\n"
            f"  de menos: {sorted(esperadas - declaradas)}"
        )

    def test_el_esquema_generado_no_tiene_las_columnas_vetadas(self):
        """Ni la contraseña, ni el valor viejo, ni el nuevo."""
        from src.modules.ai.esquema import _generar_desde_la_base
        from src.modules.ai.guard import _COLUMNAS_PROHIBIDAS

        generado = _generar_desde_la_base()
        if generado is None:
            pytest.skip("No hay base para generar el esquema.")

        for columna in _COLUMNAS_PROHIBIDAS:
            assert not re.search(rf"\b{columna}\b", generado), (
                f"El esquema generado incluye la columna vetada '{columna}': "
                "el modelo no tiene por que verla."
            )
