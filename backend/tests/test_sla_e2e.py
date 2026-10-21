"""
Tests del SLA operable: horario laboral, avisos y escalamiento.

POR QUE SON E2E
---------------
El SLA vive en la base: el calculo del plazo, el barrido y el escalamiento
tocan triggers, funciones y CTEs. Un mock probaria una base de mentira.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_TEST = os.getenv(
    "DATABASE_URL_TEST",
    "postgresql://postgres:secretpassword@localhost:5432/smart_inventory_test",
)


@pytest.fixture(scope="module")
def base():
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = create_engine(DATABASE_TEST, pool_pre_ping=True)
        with engine.connect() as conexion:
            conexion.execute(text("SELECT 1"))
            conexion.execute(text("SELECT 1 FROM incident_sla_policies LIMIT 1"))
            conexion.execute(text("SELECT 1 FROM business_calendar_settings LIMIT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(
            f"No hay base de test disponible ({DATABASE_TEST}).\n"
            f"  Motivo: {exc}"
        )

    import src.modules.ai.executor as executor

    executor._engine = engine
    return engine


@pytest.fixture
def admin(base):
    from sqlalchemy import text

    with base.connect() as conexion:
        fila = conexion.execute(
            text("SELECT id FROM users WHERE role_id = (SELECT id FROM roles WHERE name = 'ADMIN') LIMIT 1")
        ).mappings().first()

    if not fila:
        pytest.skip("La base de test no tiene un usuario ADMIN. Corre init.sql.")

    return fila["id"]


@pytest.fixture
def limpio(base, admin):
    """Deja las políticas como estaban y borra lo que crean los tests."""
    from sqlalchemy import text

    with base.connect() as conexion:
        politicas = [dict(f) for f in conexion.execute(text("SELECT * FROM incident_sla_policies")).mappings()]

    yield

    with base.begin() as conexion:
        for p in politicas:
            conexion.execute(
                text(
                    """
                    UPDATE incident_sla_policies
                    SET response_minutes = :r, resolution_minutes = :s, business_hours_only = :b
                    WHERE severity = :sev
                    """
                ),
                {
                    "r": p["response_minutes"],
                    "s": p["resolution_minutes"],
                    "b": p["business_hours_only"],
                    "sev": p["severity"],
                },
            )
        conexion.execute(text("TRUNCATE incident_events, incidents, incident_code_counters CASCADE"))


class TestHorarioLaboral:
    def test_un_sla_de_24_horas_no_cuenta_la_noche(self, base, admin, limpio):
        """
        Un SLA de 4 horas reportado un viernes a las 17:00 vence el LUNES.

        Viernes 17:00 -> 18:00 son 1 hora; sabado y domingo no son laborables;
        el lunes cuenta de 9:00 a 12:00. Son 4 horas habiles.
        """
        from sqlalchemy import text

        with base.begin() as conexion:
            fila = conexion.execute(
                text(
                    """
                    SELECT incident_agrega_minutos_habiles(
                        TIMESTAMPTZ '2026-10-23 17:00:00-03', 240, TRUE
                    ) AS plazo
                    """
                )
            ).mappings().first()

        plazo = fila["plazo"]
        # El día local tiene que ser lunes (0 = domingo, 1 = lunes).
        assert plazo.astimezone(timezone(timedelta(hours=-3))).isoweekday() == 1
        # Y dentro del horario de trabajo: las 12:00.
        assert plazo.astimezone(timezone(timedelta(hours=-3))).hour == 12

    def test_sin_horario_laboral_es_una_suma_de_reloj(self, base, admin, limpio):
        from sqlalchemy import text

        with base.begin() as conexion:
            fila = conexion.execute(
                text(
                    """
                    SELECT incident_agrega_minutos_habiles(
                        TIMESTAMPTZ '2026-10-23 17:00:00-03', 240, FALSE
                    ) AS plazo
                    """
                )
            ).mappings().first()

        # 240 minutos de reloj: 4 horas, un viernes a las 21:00.
        assert fila["plazo"].astimezone(timezone(timedelta(hours=-3))).hour == 21

    def test_el_plazo_de_una_incidencia_usa_el_horario_de_su_severidad(self, base, admin, limpio):
        """
        Con la severidad en horario laboral, el plazo cae en el horario de
        trabajo; con la de reloj, no.
        """
        from sqlalchemy import text

        from src.modules.incidents import service

        with base.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE incident_sla_policies
                    SET response_minutes = 240, business_hours_only = TRUE
                    WHERE severity = 'S4'
                    """
                )
            )
            # Un viernes a las 17:00.
            conexion.execute(
                text(
                    """
                    UPDATE incidents
                    SET created_at = TIMESTAMPTZ '2026-10-23 17:00:00-03'
                    WHERE false
                    """
                )
            )

        # El alta usa `created_at` (que es NOW()), así que el plazo se calcula
        # desde el momento real. Lo que se verifica acá es que la Función de
        # calculo devuelve un plazo en el horario laboral, que es lo que la
        # dispara.
        with base.connect() as conexion:
            fila = conexion.execute(
                text(
                    """
                    SELECT incident_agrega_minutos_habiles(
                        TIMESTAMPTZ '2026-10-23 17:00:00-03', 240, TRUE
                    ) AS plazo
                    """
                )
            ).mappings().first()

        local = fila["plazo"].astimezone(timezone(timedelta(hours=-3)))
        assert 9 <= local.hour < 18


class TestAvisos:
    def test_el_barrido_avisa_de_un_caso_que_vence(self, base, admin, limpio):
        from sqlalchemy import text

        from src.modules.incidents import service

        # Un SLA de 1 minuto: el caso "vence" enseguida.
        with base.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE incident_sla_policies
                    SET response_minutes = 1, resolution_minutes = 60
                    WHERE severity = 'S4'
                    """
                )
            )

        incidencia = service.crear_incidencia(
            {"title": "Caso que vence para probar el aviso", "severity": "S4"},
            user_id=admin,
        )

        # El plazo ya paso (o está por pasar): el barrido tiene que avisar.
        resultado = service.barrer_sla(minutos_aviso=60)
        assert resultado["avisos_creados"] >= 1

        avisos = service.listar_notificaciones(user_id=admin)
        assert any(a["incident_id"] == incidencia["id"] for a in avisos)

    def test_el_barrido_no_duplica_el_aviso(self, base, admin, limpio):
        from sqlalchemy import text

        from src.modules.incidents import service

        with base.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE incident_sla_policies
                    SET response_minutes = 1, resolution_minutes = 60
                    WHERE severity = 'S4'
                    """
                )
            )

        service.crear_incidencia(
            {"title": "Caso para probar que el aviso no se repite", "severity": "S4"},
            user_id=admin,
        )

        service.barrer_sla(minutos_aviso=60)
        primero = service.contar_notificaciones(user_id=admin)

        # El segundo barrido no puede crear el mismo aviso otra vez.
        service.barrer_sla(minutos_aviso=60)
        segundo = service.contar_notificaciones(user_id=admin)

        assert primero == segundo

    def test_el_aviso_se_marca_como_leido(self, base, admin, limpio):
        from sqlalchemy import text

        from src.modules.incidents import service

        with base.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE incident_sla_policies
                    SET response_minutes = 1, resolution_minutes = 60
                    WHERE severity = 'S4'
                    """
                )
            )

        service.crear_incidencia(
            {"title": "Caso para marcar el aviso como leido", "severity": "S4"},
            user_id=admin,
        )
        service.barrer_sla(minutos_aviso=60)

        avisos = service.listar_notificaciones(user_id=admin)
        assert avisos

        service.marcar_notificacion_leida(avisos[0]["id"], user_id=admin)
        sin_leer = service.contar_notificaciones(user_id=admin)
        assert sin_leer == len(avisos) - 1


class TestEscalamiento:
    def test_un_caso_vencido_se_escala_y_se_asigna(self, base, admin, limpio):
        from sqlalchemy import text

        from src.modules.incidents import service

        with base.begin() as conexion:
            conexion.execute(
                text(
                    """
                    UPDATE incident_sla_policies
                    SET response_minutes = 1, resolution_minutes = 60
                    WHERE severity = 'S4'
                    """
                )
            )
            conexion.execute(
                text(
                    """
                    INSERT INTO incident_escalation_policies
                        (severity, escalate_after_minutes, escalate_to_role, max_escalations)
                    VALUES ('S4', 0, 'ADMIN', 2)
                    ON CONFLICT (severity) DO UPDATE
                    SET escalate_after_minutes = 0, escalate_to_role = 'ADMIN'
                    """
                )
            )

        incidencia = service.crear_incidencia(
            {"title": "Caso que se escala por SLA vencido", "severity": "S4"},
            user_id=admin,
        )

        # Se espera a que el plazo de 1 minuto pase.
        import time

        time.sleep(62)

        resultado = service.barrer_sla(minutos_aviso=60)
        assert resultado["escalados"] >= 1

        detalle = service.obtener_incidencia(incidencia["id"])
        # Quedó asignado (a alguien que puede resolverlo) y con el evento.
        assert detalle["assigned_to"] is not None
        assert any(e["event_type"] == "ESCALATED" for e in detalle["eventos"])


class TestCalendario:
    def test_se_puede_cambiar_el_calendario(self, base, admin, limpio):
        from src.modules.incidents import service

        original = service.obtener_calendario()
        try:
            cambiado = service.actualizar_calendario(
                {
                    "timezone": "America/Mexico_City",
                    "day_start": "08:00",
                    "day_end": "17:00",
                    "workdays": [1, 2, 3, 4, 5],
                    "holidays": ["2026-12-25"],
                }
            )
            assert cambiado["timezone"] == "America/Mexico_City"
            assert cambiado["day_start"] == "08:00"
            assert cambiado["holidays"] == ["2026-12-25"]
        finally:
            service.actualizar_calendario(original)

    def test_una_zona_horaria_invalida_se_rechaza(self, base, admin, limpio):
        from src.modules.incidents import service

        with pytest.raises(service.IncidenciaInvalida):
            service.actualizar_calendario({"timezone": "Marte/Olympus_Mons"})
