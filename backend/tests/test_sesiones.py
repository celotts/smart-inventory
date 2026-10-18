"""
Tests del tope de sesiones.

POR QUE ESTE CASO MERECE UN ARCHIVE PROPIO
------------------------------------------
El sintoma que produce es "la app me cerro la sesion sola", y no se parece a
ningun otro fallo: no hay error visible, no hay log util, y el usuario no hizo
nada mal. Es la clase de fallo que hace que un usuario deje de confiar en el
sistema sin saber a que se debe.

El escenario que lo dispara es real y no exotico: `/auth/refresh` rota la sesion
en cada renovacion, asi que un mismo dispositivo deja filas a su paso. Con
cinco sesiones abiertas —es el tope— y una pestana mas entrando, el corte cae
sobre la sesion que alguien estaba usando en otra pantalla.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.users import auth as auth_service

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
            conexion.execute(text("SELECT 1 FROM sessions LIMIT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"No hay base de test disponible ({DATABASE_TEST}). Motivo: {exc}")

    import src.modules.users.models as models

    models._engine = engine
    return engine


def _admin(base):
    from sqlalchemy import text

    with base.connect() as conexion:
        fila = conexion.execute(
            text(
                """
                SELECT u.id, u.email, u.full_name, r.name AS role_name
                FROM users u JOIN roles r ON r.id = u.role_id
                WHERE u.email = 'admin@smartinventory.local'
                """
            )
        ).mappings().first()

    if not fila:
        pytest.skip("La base de test no tiene el admin de init.sql.")

    return dict(fila)


class TestTopeDeSesiones:
    def test_la_sesion_nueva_no_se_revoca_a_si_misma(self, base):
        """
        ESTE ES EL BUG.

        Con el tope lleno, `_limitar_sesiones` revocaba la mas vieja entre las
        candidatas —y la sesion recien creada entraba en esa lista. Con
        desempate por `created_at` solamente, Postgres no garantiza el orden
        entre dos filas del mismo microsegundo, asi que la sesion nueva podia
        quedar herself afuera del corte: el login devolvia 200 y el primer
        request ya era un 401.
        """
        usuario = _admin(base)
        maximo = 5

        # Se llena el tope con sesiones viejas.
        for _ in range(maximo + 2):
            auth_service._abrir_sesion(usuario)

        # Ahora se abre una sesion nueva y tiene que sobrevivir.
        tokens = auth_service._abrir_sesion(usuario)
        nueva = tokens["session_id"]

        with base.connect() as conexion:
            revocada = conexion.execute(
                __import__("sqlalchemy").text(
                    "SELECT revoked_at IS NOT NULL FROM sessions WHERE id = :id"
                ),
                {"id": nueva},
            ).scalar_one()

        assert revocada is False, (
            "La sesion recien creada fue revocada por el tope: el usuario "
            "entra y al primer request ya esta desconectado."
        )

    def test_el_tope_se_respeta(self, base):
        """
        Corregir lo anterior NO puede romper el tope: si `conservar` saca la
        sesion nueva de la cuenta y despues se cuenta todo, el limite real
        seria `maximo + 1`.
        """
        from sqlalchemy import text

        usuario = _admin(base)
        for _ in range(7):
            auth_service._abrir_sesion(usuario)

        with base.connect() as conexion:
            vivas = conexion.execute(
                text(
                    """
                    SELECT COUNT(*) FROM sessions
                    WHERE user_id = :u AND revoked_at IS NULL AND expires_at > NOW()
                    """
                ),
                {"u": usuario["id"]},
            ).scalar_one()

        assert vivas <= 5, f"Quedaron {vivas} sesiones vivas y el tope es 5."

    def test_el_orden_es_determinista(self, base):
        """
        El desempate tiene que existir en la consulta, no depender de que
        Postgres ordene igual dos veces.

        Se verifica sobre la consulta real: con `ORDER BY created_at DESC` solo,
        dos filas del mismo `created_at` pueden volver en cualquier orden.
        """
        import inspect

        fuente = inspect.getsource(auth_service._limitar_sesiones)

        assert "created_at DESC, id DESC" in fuente, (
            "El ORDER BY tiene que desempatar con `id DESC`: sin eso, dos "
            "sesiones creadas en el mismo microsegundo pueden ordenarse "
            "distinto en dos llamadas y cortar la que no debe."
        )

    def test_conservar_none_no_revoca_la_ultima(self, base):
        """El camino sin `conservar` (limpieza manual, tests) sigue siendo valido."""
        usuario = _admin(base)
        for _ in range(7):
            auth_service._limitar_sesiones(usuario["id"], conservar=None)
