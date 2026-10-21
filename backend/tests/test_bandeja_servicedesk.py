"""
Tests del flujo del Service Desk: bandeja, toma y cierre con destino.

QUE SE PRUEBA
-------------
El flujo COMPLETO de un caso, que es la parte que hace que este modulo sea
el control del stock danado y no un ticket de software:

  1. El caso reportado cae en la BANDEJA (nadie lo respondio).
  2. El tecnico lo TOMA: queda asignado, arranca el reloj de respuesta y el
     que lo reporto recibe el aviso.
  3. Al RESOLVER, el que lo reporto recibe el aviso del cierre.
  4. El destino del EQUIPO se aplica de verdad en el inventario:
     - TALLER: el equipo pasa a `IN_REPAIR` CON una orden de trabajo abierta
       (la base lo exige: un equipo en el taller sin orden no existe).
     - DANO_TOTAL: el equipo sale del stock, con su certificado de baja y la
       orden marcada como no reparable.
  5. Un destino IMPOSIBLE no resuelve el caso: se rechaza antes, para no
     dejar el caso cerrado y el equipo donde estaba.

POR QUE NECESITAN UNA BASE REAL
-------------------------------
Lo que se prueba aca son los TRIGGERS: la maquina de estados, la
inmutabilidad del historial, el trigger que exige la orden de trabajo para
poner un equipo en `IN_REPAIR` y el que exige el certificado para dar de
baja. Un test que los simule probaria un Postgres de mentira.

    export DATABASE_URL_TEST=postgresql://postgres:...@localhost:5432/si_test
    python3 -m pytest tests/test_bandeja_servicedesk.py -v
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
    """Conecta, verifica el esquema y corta si no hay nada que probar."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = create_engine(DATABASE_TEST, pool_pre_ping=True)
        with engine.connect() as conexion:
            conexion.execute(text("SELECT 1"))
            conexion.execute(text("SELECT 1 FROM incidents LIMIT 1"))
            conexion.execute(text("SELECT 1 FROM incident_sla_policies LIMIT 1"))
            conexion.execute(text("SELECT 1 FROM devices LIMIT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(
            f"No hay base de test disponible ({DATABASE_TEST}).\n"
            "  Levanta una y corré: createdb si_test && psql -d si_test -f database/init.sql\n"
            f"  Motivo: {exc}"
        )

    import src.modules.ai.executor as executor

    executor._engine = engine
    return engine


@pytest.fixture
def usuarios(base):
    """Dos usuarios reales: uno reporta (operador) y otro resuelve (tecnico).

    Si la base de test no los tiene, se crean: los tests no pueden depender
    de que alguien los haya sembrado a mano, y un test que se salta siempre
    no prueba nada.
    """
    from sqlalchemy import text

    con_clave = "x" * 60  # no se usa para autenticarse: el service va directo

    with base.begin() as conexion:
        ids = {}
        for email, rol in (
            ("operador@empresa.com", "OPERATOR"),
            ("tecnico@empresa.com", "TECHNICIAN"),
        ):
            fila = conexion.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email},
            ).mappings().first()

            if fila:
                ids[email] = fila["id"]
                continue

            ids[email] = conexion.execute(
                text(
                    """
                    INSERT INTO users (email, password, full_name, phone, phone2, role_id)
                    VALUES (
                        :email,
                        :clave,
                        :nombre,
                        'N/A',
                        'N/A',
                        (SELECT id FROM roles WHERE name = :rol)
                    )
                    RETURNING id
                    """
                ),
                {
                    "email": email,
                    "clave": con_clave,
                    "nombre": f"Prueba {rol}",
                    "rol": rol,
                },
            ).scalar_one()

    yield {
        "reportador": ids["operador@empresa.com"],
        "tecnico": ids["tecnico@empresa.com"],
    }


@pytest.fixture
def equipo(base):
    """Un equipo en el deposito, listo para romperse."""
    from sqlalchemy import text

    with base.begin() as conexion:
        ubicacion = conexion.execute(
            text(
                """
                INSERT INTO locations (store_name, address, latitude, longitude,
                                       tolerance_radius_meters)
                VALUES ('Deposito Test SD', 'Calle 1', -34.6, -58.4, 50)
                RETURNING id
                """
            )
        ).scalar_one()

        modelo = conexion.execute(
            text(
                """
                INSERT INTO models (category_id, brand, model)
                VALUES ((SELECT id FROM categories LIMIT 1), 'ACME', :m)
                RETURNING id
                """
            ),
            {"m": f"SD-TEST-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()

    from src.modules.devices import service as devices

    device_id = devices.crear_dispositivo(
        {
            "serial_number": f"SD-SER-{uuid.uuid4().hex[:10]}",
            "model_id": modelo,
            "location_id": ubicacion,
        }
    )["id"]

    yield device_id

    with base.begin() as conexion:
        conexion.execute(
            text(
                """
                DELETE FROM repairs WHERE device_id = :d
                """
            ),
            {"d": device_id},
        )
        conexion.execute(
            text(
                """
                DELETE FROM disposal_certificates WHERE device_id = :d
                """
            ),
            {"d": device_id},
        )
        conexion.execute(
            text("DELETE FROM devices WHERE id = :d"), {"d": device_id}
        )
        conexion.execute(
            text("DELETE FROM models WHERE id = :m"), {"m": modelo}
        )
        conexion.execute(
            text("DELETE FROM locations WHERE id = :u"), {"u": ubicacion}
        )


@pytest.fixture
def limpieza(base):
    """Borra lo que crean los tests.

    TRUNCATE y no DELETE: el trigger de inmutabilidad prohibe borrar los
    eventos de un caso (por diseno: el historial es una prueba).
    """
    from sqlalchemy import text

    yield

    with base.begin() as conexion:
        conexion.execute(
            text(
                "TRUNCATE incident_events, incidents, incident_code_counters, "
                "incident_notifications CASCADE"
            )
        )


class TestBandeja:
    def test_el_caso_reportado_cae_en_la_bandeja(self, base, usuarios, limpieza):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Equipo que no prende en el deposito", "severity": "S3"},
            user_id=usuarios["reportador"],
        )

        bandeja = service.listar_incidencias(sin_atender=True, solo_abiertas=True)

        assert caso["id"] in [i["id"] for i in bandeja["items"]]

    def test_el_caso_tomado_sale_de_la_bandeja(self, base, usuarios, limpieza):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Monitor con manchas", "severity": "S3"},
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        bandeja = service.listar_incidencias(sin_atender=True, solo_abiertas=True)

        assert caso["id"] not in [i["id"] for i in bandeja["items"]]

    def test_tomar_el_caso_lo_asigna_y_arranca_el_reloj(self, base, usuarios, limpieza):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Teclado sin teclas", "severity": "S3"},
            user_id=usuarios["reportador"],
        )

        tomado = service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        assert str(tomado["assigned_to"]) == str(usuarios["tecnico"])
        assert tomado["status"] == "IN_PROGRESS"
        assert tomado["first_response_at"] is not None

    def test_dos_tecnicos_no_se_pueden_tomar_el_mismo_caso(
        self, base, usuarios, limpieza
    ):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Impresora sin tinta", "severity": "S3"},
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        # El admin lo toma otra vez: ya tiene dueno, y el error lo dice.
        from sqlalchemy import text

        with base.connect() as conexion:
            admin = conexion.execute(
                text(
                    "SELECT id FROM users WHERE role_id = (SELECT id FROM roles WHERE name = 'ADMIN') LIMIT 1"
                )
            ).scalar_one()

        with pytest.raises(service.IncidenciaInvalida):
            service.tomar_caso(caso["id"], user_id=admin)

    def test_el_reportador_recibe_el_aviso_de_la_toma(self, base, usuarios, limpieza):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Router sin señal", "severity": "S2"},
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        avisos = service.listar_notificaciones(user_id=usuarios["reportador"])

        assert any(a["type"] == "CASE_CLAIMED" for a in avisos)


class TestCierre:
    def test_al_resolver_el_reportador_recibe_el_aviso(
        self, base, usuarios, limpieza
    ):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Cargador quemado", "severity": "S3"},
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])
        service.cerrar_caso(
            caso["id"],
            {"resolution": "Se cambio el cargador.", "estado": "RESOLVED"},
            user_id=usuarios["tecnico"],
        )

        avisos = service.listar_notificaciones(user_id=usuarios["reportador"])

        assert any(a["type"] == "CASE_RESOLVED" for a in avisos)

    def test_el_taller_manda_el_equipo_con_su_orden_de_trabajo(
        self, base, usuarios, equipo, limpieza
    ):
        from sqlalchemy import text

        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {
                "title": "Equipo roto que va al taller",
                "severity": "S3",
                "device_id": equipo,
            },
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        cerrado = service.cerrar_caso(
            caso["id"],
            {
                "resolution": "Se cambio la placa madre.",
                "estado": "RESOLVED",
                "destino_equipo": "TALLER",
            },
            user_id=usuarios["tecnico"],
        )

        assert cerrado["status"] == "RESOLVED"
        assert "taller" in (cerrado["mensaje_equipo"] or "").lower()

        with base.connect() as conexion:
            estado = conexion.execute(
                text("SELECT current_status FROM devices WHERE id = :d"),
                {"d": equipo},
            ).scalar_one()

            orden = conexion.execute(
                text(
                    """
                    SELECT id, status, incident_id
                    FROM repairs
                    WHERE device_id = :d
                    """
                ),
                {"d": equipo},
            ).mappings().first()

        assert estado == "IN_REPAIR"
        # La orden de trabajo abierta es la que exige la base para dejar el
        # equipo en el taller, y esta LIGADA al caso.
        assert orden is not None
        assert orden["status"] not in ("REPAIRED", "UNREPAIRABLE")
        assert str(orden["incident_id"]) == str(caso["id"])

    def test_el_dano_total_saca_el_equipo_del_stock(self, base, usuarios, equipo, limpieza):
        from sqlalchemy import text

        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {
                "title": "Equipo con dano total",
                "severity": "S2",
                "device_id": equipo,
            },
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])
        service.cerrar_caso(
            caso["id"],
            {
                "resolution": "Placa quemada. No hay repuesto.",
                "estado": "RESOLVED",
                "destino_equipo": "DANO_TOTAL",
            },
            user_id=usuarios["tecnico"],
        )

        with base.connect() as conexion:
            fila = conexion.execute(
                text(
                    """
                    SELECT current_status, deleted_at
                    FROM devices WHERE id = :d
                    """
                ),
                {"d": equipo},
            ).mappings().first()

            certificado = conexion.execute(
                text(
                    """
                    SELECT id, repair_id FROM disposal_certificates
                    WHERE device_id = :d
                    """
                ),
                {"d": equipo},
            ).mappings().first()

            orden = conexion.execute(
                text(
                    "SELECT status FROM repairs WHERE device_id = :d"
                ),
                {"d": equipo},
            ).mappings().first()

        # El equipo salio del stock: esta dado de baja y no se lista mas.
        assert fila["current_status"] == "PERMANENTLY_DISPOSED"
        assert fila["deleted_at"] is not None
        # Con su certificado, que apunta a la orden que demostro que no se
        # podia arreglar.
        assert certificado is not None
        assert certificado["repair_id"] is not None
        assert orden["status"] == "UNREPAIRABLE"

    def test_un_destino_imposible_no_resuelve_el_caso(self, base, usuarios, equipo, limpieza):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {
                "title": "Equipo en el deposito que se quiere devolver",
                "severity": "S3",
                "device_id": equipo,
            },
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        # El equipo esta en el deposito: no puede "volver" al deposito.
        with pytest.raises(service.IncidenciaInvalida):
            service.cerrar_caso(
                caso["id"],
                {
                    "resolution": "Prueba de destino invalido.",
                    "estado": "RESOLVED",
                    "destino_equipo": "REPARADO",
                },
                user_id=usuarios["tecnico"],
            )

        # Y el caso SIGUE ABIERTO: no se cerrio a medias.
        assert service.obtener_incidencia(caso["id"])["status"] == "IN_PROGRESS"

    def test_no_se_puede_cerrar_sin_resolucion(self, base, usuarios, limpieza):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Caso sin texto de resolucion", "severity": "S3"},
            user_id=usuarios["reportador"],
        )
        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        with pytest.raises(service.IncidenciaInvalida):
            service.cerrar_caso(
                caso["id"],
                {"resolution": "  ", "estado": "RESOLVED"},
                user_id=usuarios["tecnico"],
            )


class TestMetricas:
    def test_las_metricas_cuentan_el_backlog(self, base, usuarios, limpieza):
        from src.modules.incidents import service

        caso = service.crear_incidencia(
            {"title": "Caso que nadie toma", "severity": "S3"},
            user_id=usuarios["reportador"],
        )

        resumen = service.metricas(user_id=usuarios["tecnico"])["resumen"]

        assert resumen["sin_atender"] >= 1
        assert resumen["sin_atender_antiguedad_h"] is not None
        assert resumen["mias"] == 0

        service.tomar_caso(caso["id"], user_id=usuarios["tecnico"])

        resumen = service.metricas(user_id=usuarios["tecnico"])["resumen"]

        assert resumen["mias"] == 1
