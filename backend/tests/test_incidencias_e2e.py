"""
Tests de incidencias contra una base REAL.

POR QUE NECESITAN UNA BASE REAL
-------------------------------
Lo que se prueba aca NO es logica de Python: son los TRIGGERS y las
restricciones de Postgres. El codigo del numero de incidente, el calculo del
SLA, la maquina de estados y la inmutabilidad del historial viven en la base,
y un test que la simule probaria un Postgres de mentira.

QUE NECESITA
------------
Una base Postgres con el esquema de `database/init.sql` aplicado. Si no hay
conexion, los tests se omiten con un motivo claro.

    export DATABASE_URL_TEST=postgresql://postgres:...@localhost:5432/si_test
    python3 -m pytest tests/test_incidencias_e2e.py -v
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_TEST = os.getenv(
    "DATABASE_URL_TEST",
    "postgresql://postgres:secretpassword@localhost:5432/smart_inventory_test",
)


@pytest.fixture(scope="module")
def base():
    """Conecta, verifica que el esquema este, y corta si no hay nada que probar."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = create_engine(DATABASE_TEST, pool_pre_ping=True)
        with engine.connect() as conexion:
            conexion.execute(text("SELECT 1"))
            conexion.execute(text("SELECT 1 FROM incidents LIMIT 1"))
            conexion.execute(text("SELECT 1 FROM incident_sla_policies LIMIT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(
            f"No hay base de test disponible ({DATABASE_TEST}).\n"
            f"  Levanta una y corré: createdb si_test && psql -d si_test -f database/init.sql\n"
            f"  Motivo: {exc}"
        )

    import src.modules.ai.executor as executor

    # Se reemplaza el engine de la app por el de test: es la unica costura que
    # hace falta, porque los services piden `get_ai_engine()`.
    executor._engine = engine
    return engine


@pytest.fixture
def admin(base):
    """Un usuario real (el admin sembrado) y su id."""
    from sqlalchemy import text

    with base.connect() as conexion:
        fila = conexion.execute(
            text("SELECT id FROM users WHERE role_id = (SELECT id FROM roles WHERE name = 'ADMIN') LIMIT 1")
        ).mappings().first()

    if not fila:
        pytest.skip("La base de test no tiene un usuario ADMIN. Corré init.sql.")

    return fila["id"]


@pytest.fixture
def limpieza(base, admin):
    """Borra las incidencias que crea el test."""
    from sqlalchemy import text

    yield

    # TRUNCATE y no DELETE: el trigger de inmutabilidad prohibe borrar los
    # eventos (por diseno: el historial de un caso es una prueba), y el
    # `ON DELETE CASCADE` de la FK tampoco puede dispararse. `TRUNCATE` no
    # dispara los triggers de fila, y con `CASCADE` lleva las tablas
    # dependientes.
    #
    # OJO CON EL `CASCADE`: vacia tambien las tablas que referencian a las
    # vaciadas (`repairs` y `disposal_certificates`, que cuelgan de
    # `incidents`). En la base de TEST eso no importa: es una base para
    # pruebas. En una base de DESARROLLO con datos, este TRUNCATE se lleva
    # por delante la historia de las bajas. Por eso la limpieza va con el
    # nombre de la base en la cabeza, y no es un `DELETE` de los equipos.
    with base.begin() as conexion:
        conexion.execute(text("TRUNCATE incident_events, incidents, incident_code_counters CASCADE"))


class TestAlta:
    def test_el_codigo_se_genera_y_es_por_ano(self, base, admin, limpieza):
        import re
        from datetime import datetime

        from src.modules.incidents import service

        incidencia = service.crear_incidencia(
            {"title": "Equipo que no prende", "severity": "S2", "category": "HARDWARE"},
            user_id=admin,
        )

        # El formato es INC-<año>-<numero de 6 digitos>. NO se comprueba que
        # sea el 000001: el correlativo es por AÑO y puede venir de otra
        # ejecucion anterior de los tests.
        assert re.fullmatch(r"INC-\d{4}-\d{6}", incidencia["code"]), incidencia["code"]
        assert f"INC-{datetime.now().year}-" in incidencia["code"]
        assert incidencia["status"] == "OPEN"
        assert incidencia["version"] == 1

    def test_el_numero_es_correlativo_y_no_se_repite(self, base, admin, limpieza):
        from src.modules.incidents import service

        primera = service.crear_incidencia({"title": "Primera incidencia del lote"}, user_id=admin)
        segunda = service.crear_incidencia({"title": "Segunda incidencia del lote"}, user_id=admin)

        assert primera["code"] != segunda["code"]
        # El numero del final es correlativo.
        n1 = int(primera["code"].split("-")[-1])
        n2 = int(segunda["code"].split("-")[-1])
        assert n2 == n1 + 1

    def test_el_sla_se_calcula_desde_la_politica_de_la_severidad(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia(
            {"title": "Sistema caido para varios clientes", "severity": "S1"},
            user_id=admin,
        )

        # La politica de S1 dice 15 minutos de respuesta.
        minutos = (incidencia["response_due_at"] - incidencia["created_at"]).total_seconds() / 60
        assert 14 <= minutos <= 16

        minutos_resolucion = (
            incidencia["resolution_due_at"] - incidencia["created_at"]
        ).total_seconds() / 60
        assert 239 <= minutos_resolucion <= 241

    def test_el_alta_es_idempotente_con_el_mismo_client_ref(self, base, admin, limpieza):
        from src.modules.incidents import service

        primera = service.crear_incidencia(
            {"title": "Se corto la conexion en el deposito"},
            user_id=admin,
            client_ref="wifi-deposito-001",
        )
        segunda = service.crear_incidencia(
            {"title": "Se corto la conexion en el deposito"},
            user_id=admin,
            client_ref="wifi-deposito-001",
        )

        assert primera["id"] == segunda["id"]
        assert primera["code"] == segunda["code"]


class TestMaquinaDeEstados:
    def test_una_transicion_valida_cambia_el_estado_y_pone_las_marcas(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "Monitor con manchas"}, user_id=admin)
        actualizada = service.cambiar_estado(incidencia["id"], "IN_PROGRESS", user_id=admin)

        assert actualizada["status"] == "IN_PROGRESS"
        assert actualizada["first_response_at"] is not None
        assert actualizada["resolved_at"] is None

    def test_una_transicion_invalida_se_rechaza(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "Teclado sin teclas"}, user_id=admin)

        # OPEN -> CLOSED no esta permitido: hay que resolverla primero.
        with pytest.raises(service.IncidenciaInvalida):
            service.cambiar_estado(incidencia["id"], "CLOSED", user_id=admin)

        # Y la incidencia sigue como estaba.
        assert service.obtener_incidencia(incidencia["id"])["status"] == "OPEN"

    def test_resolver_ponela_fecha_y_cerrar_tambien(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "Impresora sin tinta"}, user_id=admin)
        service.cambiar_estado(incidencia["id"], "IN_PROGRESS", user_id=admin)
        resuelta = service.cambiar_estado(incidencia["id"], "RESOLVED", user_id=admin)

        assert resuelta["status"] == "RESOLVED"
        assert resuelta["resolved_at"] is not None
        assert resuelta["closed_at"] is None

        cerrada = service.cambiar_estado(incidencia["id"], "CLOSED", user_id=admin)
        assert cerrada["closed_at"] is not None

    def test_una_resuelta_puede_reabrirse(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "Falla intermitente"}, user_id=admin)
        service.cambiar_estado(incidencia["id"], "IN_PROGRESS", user_id=admin)
        service.cambiar_estado(incidencia["id"], "RESOLVED", user_id=admin)

        reabierta = service.cambiar_estado(incidencia["id"], "IN_PROGRESS", user_id=admin)
        assert reabierta["status"] == "IN_PROGRESS"
        # Se reabre: pierde la marca de resuelta.
        assert reabierta["resolved_at"] is None


class TestHistorial:
    def test_cada_cambio_deja_un_evento_con_su_actor(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "El escaner no lee"}, user_id=admin)
        service.cambiar_estado(incidencia["id"], "IN_PROGRESS", user_id=admin, nota="Tomo el caso")

        detalle = service.obtener_incidencia(incidencia["id"])
        tipos = [e["event_type"] for e in detalle["eventos"]]

        # El historial va del MAS NUEVO al mas viejo: lo que paso ultimo es
        # lo que el operador mira primero, y con el caso abierto hacia abajo
        # habia que scrollear hasta el final para ver el ultimo cambio.
        assert set(tipos) == {"CREATED", "STATUS_CHANGED"}
        assert tipos[0] == "STATUS_CHANGED"
        # El actor sale del CONTEXTO de la transaccion, no de lo que manda el
        # cliente: un historial de soporte es una prueba.
        for evento in detalle["eventos"]:
            assert evento["actor_id"] == admin

    def test_un_comentario_es_un_evento(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "Duda del cliente"}, user_id=admin)
        service.comentar(incidencia["id"], "El cliente dice que ayer funcionaba", user_id=admin)

        detalle = service.obtener_incidencia(incidencia["id"])
        # El comentario es lo ULTIMO que paso: va primero en la lista.
        assert detalle["eventos"][0]["event_type"] == "COMMENT"
        assert "ayer funcionaba" in detalle["eventos"][0]["note"]

    def test_el_historial_es_inmutable(self, base, admin, limpieza):
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "No se puede editar el historial"}, user_id=admin)

        with pytest.raises(DBAPIError):
            with base.begin() as conexion:
                conexion.execute(
                    text("UPDATE incident_events SET note = 'trucado' WHERE incident_id = :id"),
                    {"id": str(incidencia["id"])},
                )


class TestEdicion:
    def test_el_bloqueo_optimista_detecta_el_conflicto(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "Conflicto de edicion"}, user_id=admin)

        # Dos operadores leen la misma version.
        version = incidencia["version"]
        service.actualizar_incidencia(
            incidencia["id"], {"title": "Titulo del primer operador", "version": version}, user_id=admin
        )

        # El segundo guarda con la version vieja: no pisa el trabajo del otro.
        with pytest.raises(service.ConflictoDeVersion):
            service.actualizar_incidencia(
                incidencia["id"],
                {"title": "Titulo del segundo operador", "version": version},
                user_id=admin,
            )

        # El titulo es el del primero.
        assert service.obtener_incidencia(incidencia["id"])["title"] == "Titulo del primer operador"

    def test_la_version_sube_en_cada_guardado(self, base, admin, limpieza):
        from src.modules.incidents import service

        incidencia = service.crear_incidencia({"title": "Cuenta de versiones"}, user_id=admin)
        actualizada = service.actualizar_incidencia(
            incidencia["id"],
            {"title": "Cuenta de versiones 2", "version": incidencia["version"]},
            user_id=admin,
        )
        assert actualizada["version"] == incidencia["version"] + 1


class TestConsultas:
    def test_el_listado_filtra_por_estado(self, base, admin, limpieza):
        from src.modules.incidents import service

        abierta = service.crear_incidencia({"title": "Abierta de prueba"}, user_id=admin)
        otra = service.crear_incidencia({"title": "Otra abierta de prueba"}, user_id=admin)
        service.cambiar_estado(otra["id"], "IN_PROGRESS", user_id=admin)

        resultado = service.listar_incidencias(estado="OPEN")
        codigos = {i["code"] for i in resultado["items"]}
        assert abierta["code"] in codigos
        assert otra["code"] not in codigos

    def test_el_listado_encuentra_por_texto(self, base, admin, limpieza):
        from src.modules.incidents import service

        creada = service.crear_incidencia(
            {"title": "Monitor parpadea en la pantalla", "description": "Solo con la luz de la sala"},
            user_id=admin,
        )

        # Busqueda de texto completo: dos palabras del titulo, en otro orden.
        resultado = service.listar_incidencias(busqueda="pantalla parpadea")
        assert creada["code"] in {i["code"] for i in resultado["items"]}

    def test_las_metricas_cuentan_lo_que_importa(self, base, admin, limpieza):
        from src.modules.incidents import service

        service.crear_incidencia({"title": "Metrica abierta de prueba"}, user_id=admin)
        resultado = service.metricas()

        assert resultado["resumen"]["abiertas"] >= 1
        assert "por_severidad" in resultado
