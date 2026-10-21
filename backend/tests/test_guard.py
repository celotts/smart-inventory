"""
Tests de la guardia de SQL.

POR QUE ESTOS CASOS Están ESCRITOS A MANO Y NO GENERADOS
-------------------------------------------------------
Cada caso es un ataque o una consulta real que se rompio en producción de
alguien. Un generador de casos produciria variaciones que nunca pasan por el
código; está lista viene de los errores que cometimos al escribir el validador.

Para correrlos:  docker compose exec backend pytest -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.ai import guard

# (sql, debe pasar la validación, porque falla)
CASOS = [
    # --- Consultas legitimas ------------------------------------------
    ("SELECT * FROM devices", True),
    ("SELECT count(*) FROM movement_history", True),
    (
        (
            "SELECT d.serial_number, m.brand FROM devices d "
            "JOIN models m ON m.id = d.model_id"
        ),
        True,
    ),
    ("WITH x AS (SELECT 1) SELECT * FROM x", True),
    ("WITH RECURSIVO x AS (SELECT 1 UNION ALL SELECT 2) SELECT * FROM x", True),
    ("WITH v AS MATERIALIZED (SELECT 1) SELECT * FROM v", True),
    ("WITH v AS NOT MATERIALIZED (SELECT 1) SELECT * FROM v", True),
    ("WITH x AS (SELECT * FROM (SELECT 1) t) SELECT * FROM x", True),
    ("WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, b", True),
    # El alias de la consulta principal no puede confundir al parser de CTE.
    ("WITH x AS (SELECT 1) SELECT count(*) AS total FROM devices", True),
    # --- Escrituras ---------------------------------------------------
    ("DROP TABLE devices", False),
    ("INSERT INTO devices (serial_number) VALUES ('x')", False),
    ("UPDATE devices SET current_status = 'IN_REPAIR'", False),
    ("DELETE FROM movement_history", False),
    ("TRUNCATE devices", False),
    ("ALTER TABLE devices ADD COLUMN x INT", False),
    # --- Multi statement ----------------------------------------------
    ("SELECT 1; DROP TABLE devices", False),
    ("SELECT * FROM devices; SELECT * FROM users", False),
    # --- Evasion por WITH ---------------------------------------------
    # Está es la que paso de verdad: el filtro por prefijo la dejaba pasar.
    (("WITH x AS (INSERT INTO devices VALUES (1) RETURNING *) SELECT * FROM x"), False),
    (
        (
            "WITH a AS (SELECT 1), b AS (DELETE FROM devices RETURNING *) "
            "SELECT * FROM a, b"
        ),
        False,
    ),
    # --- Cambió de sesión / escalada ----------------------------------
    ("SELECT 1; SET ROLE postgres", False),
    ("SELECT pg_sleep(60)", False),
    ("SELECT pg_read_file('/etc/passwd')", False),
    # --- Columnas que no salen del servidor ---------------------------
    ("SELECT email, password FROM users", False),
    ("SELECT old_value FROM audit_logs", False),
    ("SELECT new_value FROM audit_logs", False),
    # --- Falsos positivos que NO deben dispararse --------------------
    # Un valor que contiene una palabra vetada es un DATO, no código.
    ("SELECT * FROM devices WHERE serial_number = 'DROP TABLE'", True),
    ("SELECT * FROM clients WHERE commercial_name = 'Pedro'", True),
    # `create_time` no es `create`: el límite de palabra es lo que evita que
    # una columna legitima se lea como palabra prohibida.
    ("SELECT * FROM devices WHERE created_at > NOW()", True),
]


@pytest.mark.parametrize("sql,debe_pasar", CASOS)
def test_validar_select(sql, debe_pasar):
    if debe_pasar:
        assert guard.validar_select(sql) is not None
    else:
        with pytest.raises(guard.SQLRechazado):
            guard.validar_select(sql)


def test_mensaje_de_rechazo_es_util():
    """
    El motivo importa: 'no permitido' sin decir cual deja al operador
    adivinando si el modelo está roto o si hay un problema de permisos.
    """
    with pytest.raises(guard.SQLRechazado) as excepcion:
        guard.validar_select("SELECT password FROM users")
    assert "password" in str(excepcion.value)


def test_lista_blanca_tiene_las_11_tablas():
    """
    El esquema tiene 11 tablas de negocio. Si se agrega una al DDL y no se
    suma a la lista blanca, la IA no la puede consultar y nadie se entera
    hasta que alguien pregunta por ella.
    """
    assert guard.tablas_permitidas() == {
        "clients",
        "locations",
        "categories",
        "models",
        "devices",
        "movement_history",
        "repairs",
        "disposal_certificates",
        "roles",
        "users",
        "audit_logs",
    }


def test_extraer_sql_de_bloque_codigo():
    assert (
        guard.extraer_sql("Aqui tienes:\n```sql\nSELECT 1;\n```")
        .upper()
        .startswith("SELECT")
    )


def test_extraer_sql_de_texto_suelto():
    assert guard.extraer_sql("SELECT * FROM devices;").upper().startswith("SELECT")


def test_extraer_sql_de_prosa_del_modelo():
    """
    El modelo de 3b, que es el de fallback, mete introducciones asi:
    'Claro, la consulta seria: SELECT count(*) FROM devices'
    """
    texto = "Claro, la consulta seria:\nSELECT count(*) FROM devices"
    assert "count(*)" in guard.extraer_sql(texto)


def test_extraer_sql_rechaza_prosa_sin_consulta():
    with pytest.raises(guard.SQLRechazado):
        guard.extraer_sql("No tengo datos suficientes para responder eso.")


def test_limite_no_se_agrega_dos_veces():
    sql = guard.aplicar_limite("SELECT * FROM devices", 100)
    assert sql.endswith("LIMIT 100")
    assert guard.aplicar_limite(sql, 100) == sql


def test_limite_respeta_el_que_ya_tenia():
    assert (
        guard.aplicar_limite("SELECT * FROM devices LIMIT 5", 100)
        == "SELECT * FROM devices LIMIT 5"
    )
