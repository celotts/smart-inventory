"""
Los identificadores van SIN ACENTOS.

QUE PASO
--------
Un pase de acentos meto tildes en los identificadores: los ALIAS de las
consultas SQL. El alias `AS días_bajo_minimo` no lo encuentra nadie: el
frontend lo leia como `dias_bajo_minimo` y por eso la pantalla de existencia
mostraba "Hace undefined dias esta bajo el minimo".

Y lo que es peor: el error no se ve en la consulta que se ejecuta, sino en
la que la USA. Un `ORDER BY días_bajo_minimo` con el `AS dias_bajo_minimo`
correcto arriba es un error de Postgres en tiempo de ejecucion: el endpoint
devuelve 500 y el tablero entero no carga.

POR QUE UN TEST Y NO "CUIDADO CON LOS ACENTOS"
----------------------------------------------
Porque el error no es de escritura: el SQL es valido para Python y el
archivo compila. Lo que falla es en la base, y solo cuando se ejecuta esa
consulta concreta. Un test que mira el codigo lo detecta antes.

QUE MIRA
--------
Los identificadores que aparecen en posicion de SQL: un alias (`AS x`), un
`ORDER BY x`, una columna de un `SELECT`. En esas posiciones, un acento es
siempre un error: Postgres los acepta (entre comillas dobles) pero el
frontend no los va a adivinar.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RAIZ = Path(__file__).resolve().parents[1] / "src"

# Una palabra con al menos una letra acentuada.
IDENTIFICADOR_CON_ACENTO = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*[áéíóúüñÁÉÍÓÚÜÑ][A-Za-z0-9_]*)\b"
)

# Las palabras clave de SQL. Una linea que tiene una de estas es una linea
# de SQL (o casi).
PALABRAS_SQL = re.compile(
    r"\b(SELECT|FROM|WHERE|JOIN|ORDER\s+BY|GROUP\s+BY|HAVING|AS|AND|OR|ON)\b",
    re.IGNORECASE,
)

# Las palabras que, con acento, son PROSA y no un identificador: estan en
# mayusculas al principio de una frase o son un termino conocido.
NO_ES_IDENTIFICADOR = {
    "línea", "código", "válida", "válido", "válidas", "válidos",
    "mínimo", "mínima", "mínimos", "último", "última", "últimos",
    "técnico", "técnica", "técnicos", "ubicación", "ubicaciones",
    "días", "día", "mes", "número", "números", "página", "páginas",
    "auditoría", "categoría", "categorías", "contraseña", "sesión",
    "cambio", "cambió", "está", "están", "código", "códigos",
    "petición", "peticiones", "operación", "operaciones",
    "reparación", "reparaciones", "reposición", "reposición",
    "histórica", "histórico", "decisión", "definición", "función",
    "jerarquía", "excepción", "límite", "límites", "transición",
    "validación", "selección", "opción", "opciones", "módulo",
    "módulos", "menú", "menú", "pestaña", "pestañas", "versión",
    "traducción", "traducciones", "descripción", "descripciones",
    "verificación", "configuración", "cancelación", "duración",
    "instalación", "activación", "generación", "recepción", "rotación",
    "deprecación", "consulta", "consultas", "búsqueda", "búsquedas",
    "plantilla", "plantillas", "referencia", "referencias",
    "mínima", "máxima", "máximo", "mínimo", "único", "única",
    "únicos", "únicas", "específico", "específica", "múltiple",
    "múltiples", "múltiplo", "automático", "automática", "automático",
    "teléfono", "teléfonos", "ubicación", "físico", "física",
    "sustantivo", "adjetivo", "verbo", "sigilo", "sigilo",
}


def _archivos_python() -> list[Path]:
    return sorted(RAIZ.rglob("*.py"))


def _lineas_de_sql(texto: str) -> list[tuple[int, str]]:
    """
    Las lineas que son (o contienen) SQL.

    Se toman las que tienen una palabra clave de SQL, y se les saca el
    comentario: un `--` dentro de un bloque de SQL es prosa, no un
    identificador.
    """
    lineas = []
    for numero, linea in enumerate(texto.split("\n"), 1):
        if not PALABRAS_SQL.search(linea):
            continue
        # Se saca el comentario de SQL y el de Python.
        limpio = linea.split("--")[0].split("#")[0]
        if limpio.strip():
            lineas.append((numero, limpio))
    return lineas


class TestIdentificadoresSinAcentos:
    def test_ningun_identificador_sql_tiene_acentos(self):
        """
        Ningun alias, columna u orden tiene que llevar tilde.

        Con un acento, Postgres lo acepta (si va entre comillas dobles) pero
        el frontend lo lee sin acento y no lo encuentra: el campo llega
        `undefined` y el 500 aparece en otro endpoint, en la consulta que
        usa ese alias.
        """
        problemas: list[str] = []
        for archivo in _archivos_python():
            texto = archivo.read_text(encoding="utf-8")
            for numero, linea in _lineas_de_sql(texto):
                for coincidencia in IDENTIFICADOR_CON_ACENTO.finditer(linea):
                    token = coincidencia.group(1)
                    if token.lower() in NO_ES_IDENTIFICADOR:
                        continue
                    # Un identificador de SQL va en minusculas o en
                    # mayusculas, pero nunca mezclado: "días_bajo_minimo" es
                    # un identificador, "Días" es prosa.
                    if token[0].isupper() and not token.isupper():
                        continue
                    problemas.append(
                        f"{archivo.relative_to(RAIZ.parent)}:{numero}: "
                        f"identificador '{token}'\n      {linea.strip()[:100]}"
                    )

        assert not problemas, (
            "Estos identificadores SQL llevan acentos. Los identificadores van "
            "sin acentos: el frontend los lee por su nombre, y con acento no "
            "los encuentra (el campo llega `undefined`).\n"
            + "\n  ".join(problemas)
        )

    def test_las_columnas_de_la_base_no_tienen_acentos(self):
        """
        Las columnas de la base tampoco.

        Es el otro lado del mismo problema: si una columna se creo con
        acento, el `SELECT` tiene que pedirla con acento, y el JSON sale
        con la clave acentuada, que el frontend no espera.

        El test se salta si no hay base (en el host sin Docker, por
        ejemplo).
        """
        import os

        url = os.getenv("DATABASE_URL_TEST") or os.getenv("DATABASE_URL")
        if not url:
            pytest.skip("No hay DATABASE_URL: no se puede consultar la base.")

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
                          AND column_name ~ '[áéíóúüñÁÉÍÓÚÜÑ]'
                        """
                    )
                ).all()
        except Exception as exc:  # noqa: BLE001 - sin base no hay nada que verificar
            pytest.skip(f"No se pudo consultar la base: {exc}")

        assert not filas, (
            "Estas columnas de la base tienen acentos en el nombre. Las "
            "columnas son identificadores: van sin acentos.\n  "
            + "\n  ".join(f"{t}.{c}" for t, c in filas)
        )
