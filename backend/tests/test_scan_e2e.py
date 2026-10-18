"""
Tests de escaneo contra una base REAL.

POR QUE ESTOS TESTS NO SON COMO LOS OTROS
-----------------------------------------
Los tests de `guard.py` y de `geo.py` prueban funciones puras: se pueden
escribir sin base. Un escaneo NO es una funcion pura: su garantia esta en que
el `UNIQUE` de `scan_receipts` rechaza el reintento y en que el trigger de
estado rechaza la transicion imposible.

Esas dos garantias son del MOTOR, no del codigo. Un test que simula la base con
un mock pasa lo mismo con y sin indice unico, y por lo tanto no prueba nada de
lo que importa.

QUE HACE FALTA PARA CORRERLOS
-----------------------------
Una base Postgres con el esquema de `database/init.sql` aplicado. Si no hay
conexion, los tests se omiten con un motivo claro en vez de fallar con un error
de conexion que parece un bug.

    export DATABASE_URL_TEST=postgresql://postgres:...@localhost:5432/si_test
    python3 -m pytest tests/test_scan_e2e.py -v
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.movements import service as movimientos

# La base de test. Deliberadamente distinta de `DATABASE_URL`: estos tests
# BORRAN y escriben, y apuntarlos a la de desarrollo seria una forma cara de
# perder datos.
DATABASE_TEST = os.getenv(
    "DATABASE_URL_TEST",
    "postgresql://postgres:secretpassword@localhost:5432/smart_inventory_test",
)

BA_LAT, BA_LON = -34.6037, -58.3816


@pytest.fixture(scope="module")
def base():
    """Conecta, verifica que el esquema este, y corta si no hay nada que probar."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = create_engine(DATABASE_TEST, pool_pre_ping=True)
        with engine.connect() as conexion:
            conexion.execute(text("SELECT 1"))
            # Se verifica que exista la tabla del trigger y la del recibo. Si
            # faltan, el esquema no esta migrado y los tests darian false.
            conexion.execute(text("SELECT 1 FROM scan_receipts LIMIT 1"))
            conexion.execute(text("SELECT 1 FROM devices LIMIT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(
            f"No hay base de test disponible ({DATABASE_TEST}).\n"
            f"  Levanta una y corré: createdb si_test && psql -d si_test -f database/init.sql\n"
            f"  Motivo: {exc}"
        )

    import src.modules.ai.executor as executor

    # Se reemplaza el engine de la app por el de test. Es la unica costura que
    # hace falta: el service pide `get_ai_engine()`, no una conexion.
    executor._engine = engine
    return engine


@pytest.fixture
def escenario(base):
    """
    Un mundo pequeno y limpio: un admin, una sede con GPS, un modelo y un
    componente en deposito.

    Se borra al final porque los tests de idempotencia dejan filas con UUID
    fijos y un `DELETE` al final evita que el segundo test los encuentre.
    """
    from sqlalchemy import text

    ids: dict[str, object] = {}

    with base.begin() as conexion:
        ids["admin"] = conexion.execute(
            text(
                "SELECT id FROM users WHERE role_id = "
                "(SELECT id FROM roles WHERE name = 'ADMIN') LIMIT 1"
            )
        ).scalar_one()

        # Si no hay admin, la base de test no esta sembrada.
        if not ids["admin"]:
            pytest.skip("La base de test no tiene un usuario ADMIN. Corré init.sql.")

        ids["ubicacion"] = conexion.execute(
            text(
                """
                INSERT INTO locations (store_name, address, latitude, longitude,
                                       tolerance_radius_meters)
                VALUES ('Deposito Test', 'Calle 1', :lat, :lon, 50)
                RETURNING id
                """
            ),
            {"lat": BA_LAT, "lon": BA_LON},
        ).scalar_one()

        ids["modelo"] = conexion.execute(
            text(
                """
                INSERT INTO models (category_id, brand, model)
                VALUES ((SELECT id FROM categories LIMIT 1), 'ACME', :m)
                RETURNING id
                """
            ),
            {"m": f"TEST-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()

    yield ids

    with base.begin() as conexion:
        conexion.execute(text("DELETE FROM scan_receipts"))
        conexion.execute(text("DELETE FROM movement_history"))
        conexion.execute(text("DELETE FROM devices WHERE current_location_id = :u OR model_id = :m"),
                         {"u": ids["ubicacion"], "m": ids["modelo"]})
        conexion.execute(text("DELETE FROM locations WHERE id = :u"), {"u": ids["ubicacion"]})
        conexion.execute(text("DELETE FROM models WHERE id = :m"), {"m": ids["modelo"]})


def _crear_dispositivo(escenario, **kwargs) -> int:
    """Da de alta un componente en deposito y devuelve su id."""
    from src.modules.devices import service as devices

    datos = {
        "serial_number": f"SER-{uuid.uuid4().hex[:10]}",
        "model_id": escenario["modelo"],
        "location_id": escenario["ubicacion"],
    }
    datos.update(kwargs)
    return devices.crear_dispositivo(datos)["id"]


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------
class TestIdempotencia:
    def test_el_mismo_client_scan_id_no_duplica(self, base, escenario):
        """
        EL caso que motiva la tabla `scan_receipts`.

        El telefono reintenta el mismo envio dos veces. Sin el recibo, quedan
        dos movimientos y "cuantos componentes entraron hoy" da el doble.
        """
        from src.modules.devices import service as devices

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]
        cid = str(uuid.uuid4())

        primero = movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=cid,
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )
        segundo = movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=cid,
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )

        assert primero["replay"] is False
        assert segundo["replay"] is True
        assert primero["movement_id"] == segundo["movement_id"]

    def test_el_reintento_no_cambia_el_estado(self, base, escenario):
        """
        Un reintento no puede hacer el trabajo dos veces.

        Si el reintento volviera a aplicar la transicion de estado, un
        `IN_TRANSIT -> INSTALLED` repetido dejaria el componente instalado
        cuando el reintento era solo un reintento.
        """
        from src.modules.devices import service as devices

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]
        cid = str(uuid.uuid4())

        movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=cid,
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )
        movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=cid,
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )

        # El estado tiene que ser IN_TRANSIT, no INSTALLED: el reintento no
        # avanzo el componente.
        assert devices.obtener_dispositivo(device_id)["current_status"] == "IN_TRANSIT"

    def test_client_scan_ids_distintos_son_eventos_distintos(self, base, escenario):
        """
        Dos envios con UUID distintos son dos eventos, aunque sean el mismo
        equipo y el mismo tipo de movimiento.

        Dos escaneos REALES del mismo componente en el mismo segundo son
        legitimos: uno entra al deposito y al segundo sale hacia un cliente. Por
        eso la idempotencia va por `client_scan_id` y no por
        (device_id, fecha), que habria rechazado algo que ocurrio.
        """
        from src.modules.devices import service as devices

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        primero = movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )
        segundo = movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )

        assert primero["movement_id"] != segundo["movement_id"]

    def test_client_scan_id_invalido_es_400(self, base, escenario):
        """Un id que no es UUID se rechaza antes de tocar la base."""
        with pytest.raises(movimientos.ErrorDeEscaneo):
            movimientos.escanear(
                codigo="INV-cualquiera",
                tipo_movimiento="OUT_TO_CLIENT",
                client_scan_id="no-soy-uuid",
                user_id=str(escenario["admin"]),
            )


# ---------------------------------------------------------------------------
# Estado y transiciones
# ---------------------------------------------------------------------------
class TestEstadoYTransiciones:
    def test_el_movimiento_cambia_el_estado(self, base, escenario):
        """Escanear un traslado tiene que mover el estado del componente."""
        from src.modules.devices import service as devices

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )

        assert devices.obtener_dispositivo(device_id)["current_status"] == "IN_TRANSIT"

    def test_transicion_imposible_no_escribe_movimiento(self, base, escenario):
        """
        ESTA es la garantia de que el historial y el estado no se separan.

        El componente esta en deposito y se le pide una `INSTALLATION`. El
        trigger de la maquina de estados lo rechaza porque de deposito no se pasa
        directo a instalado. Y como el estado y el movimiento van en la misma
        transaccion, el movimiento tampoco se escribe.

        Sin la misma transaccion, quedaria un movimiento que dice "instalado"
        sobre un componente que sigue en el deposito: el historial cuenta una
        historia que no paso.
        """
        from src.modules.devices import service as devices
        from sqlalchemy import text

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        with pytest.raises(Exception) as exc:
            movimientos.escanear(
                codigo=serial,
                tipo_movimiento="INSTALLATION",
                client_scan_id=str(uuid.uuid4()),
                user_id=str(escenario["admin"]),
                ubicacion_id=escenario["ubicacion"],
            )

        assert "Transicion de estado invalida" in str(exc.value)

        # Y lo importante: NO quedo ningun movimiento huerfano.
        with devices.get_ai_engine().connect() as conexion:
            count = conexion.execute(
                text("SELECT COUNT(*) FROM movement_history WHERE device_id = :id"),
                {"id": device_id},
            ).scalar_one()

        assert count == 0

    def test_taller_requiere_reparacion_abierta(self, base, escenario):
        """Mandar al taller sin reparacion registrada es rechazado por el trigger."""
        from src.modules.devices import service as devices

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        with pytest.raises(Exception) as exc:
            movimientos.escanear(
                codigo=serial,
                tipo_movimiento="WITHDRAWAL_TO_WORKSHOP",
                client_scan_id=str(uuid.uuid4()),
                user_id=str(escenario["admin"]),
                ubicacion_id=escenario["ubicacion"],
            )

        assert "IN_REPAIR" in str(exc.value)

    def test_la_regla_llega_como_error_de_regla_y_no_de_fallo(self, base, escenario):
        """
        ESTE TEST IMPORTA MAS DE LO QUE PARECE.

        El trigger levanta una excepcion de Postgres. Sin traduzirla, el
        handler global la convierte en un 500 "Error interno", y el operador ve
        un fallo del sistema donde en realidad le estan diciendo que esta
        intentando algo imposible. Un 500 tambien hace que el orquestador lo
        cuente como incidente.

        Se verifica el TIPO de la excepcion (409 = conflicto de estado, no 500)
        y que el mensaje conserve el texto del trigger, que es el que explica
        que hacer.
        """
        from src.modules.devices import service as devices

        # Se lleva el componente a INSTALLED primero, para despues intentar un
        # salto que la maquina de estados prohibe.
        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )
        movimientos.escanear(
            codigo=serial,
            tipo_movimiento="INSTALLATION",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )

        # De INSTALLED no se pasa directo a deposito: hay que pasar por transito.
        with pytest.raises(movimientos.TransicionInvalida) as exc:
            movimientos.escanear(
                codigo=serial,
                tipo_movimiento="WAREHOUSE_REENTRY",
                client_scan_id=str(uuid.uuid4()),
                user_id=str(escenario["admin"]),
                ubicacion_id=escenario["ubicacion"],
            )

        # El mensaje tiene que explicar QUE pasar, no solo que fallo.
        mensaje = str(exc.value).lower()
        assert "invalida" in mensaje
        assert "transicion" in mensaje or "instalado" in mensaje


# ---------------------------------------------------------------------------
# Geo-validacion
# ---------------------------------------------------------------------------
class TestGeoValidacion:
    def test_escaneo_en_la_sede_es_valido(self, base, escenario):
        from src.modules.devices import service as devices

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        resultado = movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
            latitude=BA_LAT,
            longitude=BA_LON,
        )

        assert resultado["geo"]["dentro_del_radio"] is True
        assert "sede" in resultado["mensaje"]

    def test_escaneo_lejos_se_registra_pero_se_marca(self, base, escenario):
        """
        Un escaneo fuera de radio NO se rechaza: se registra marcado.

        La razon es de operacion: si el GPS de un celular en el interior de un
        deposito de chapa deriva 400 metros, rechazar el escaneo deja al
        operador sin poder trabajar y el componente parece perdido. Se registra
        y queda para revisar. El dato falso se senala, no se oculta.
        """
        from src.modules.devices import service as devices

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        resultado = movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
            latitude=BA_LAT + 0.05,  # ~5 km
            longitude=BA_LON,
        )

        assert resultado["geo"]["dentro_del_radio"] is False
        assert resultado["geo"]["distancia_metros"] > 1000
        assert "ATENCION" in resultado["mensaje"]

    def test_sin_gps_queda_indeterminado_no_falso(self, base, escenario):
        """
        Sin coordenadas, `is_valid_location` es None, NO False.

        La diferencia es la que separa "no estabas ahi" de "no se puede saber".
        Un False sin coordenadas convierte cada escaneo de un movil sin GPS en
        una inspeccion, y el operador deja de mirar la columna.
        """
        from src.modules.devices import service as devices
        from sqlalchemy import text

        device_id = _crear_dispositivo(escenario)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        resultado = movimientos.escanear(
            codigo=serial,
            tipo_movimiento="OUT_TO_CLIENT",
            client_scan_id=str(uuid.uuid4()),
            user_id=str(escenario["admin"]),
            ubicacion_id=escenario["ubicacion"],
        )

        assert resultado["geo"]["dentro_del_radio"] is None

        with devices.get_ai_engine().connect() as conexion:
            valor = conexion.execute(
                text("SELECT is_valid_location FROM movement_history WHERE id = :id"),
                {"id": resultado["movement_id"]},
            ).scalar_one()

        assert valor is None

    def test_latitud_de_400_se_rechaza(self, base, escenario):
        """
        Una coordenada rota se rechaza con un error entendible.

        Sin la validacion, Postgres acepta la fila con una latitud de 400 y el
        error aparece tres reportes mas adelante, cuando alguien pregunta la
        distancia.
        """
        from src.core.geo import CoordenadaInvalida

        with pytest.raises(CoordenadaInvalida):
            movimientos.escanear(
                codigo="INV-cualquiera",
                tipo_movimiento="OUT_TO_CLIENT",
                client_scan_id=str(uuid.uuid4()),
                user_id=str(escenario["admin"]),
                ubicacion_id=escenario["ubicacion"],
                latitude=400,
                longitude=0,
            )


# ---------------------------------------------------------------------------
# Resolucion de codigos
# ---------------------------------------------------------------------------
class TestResolucionDeCodigos:
    def test_resuelve_por_serial_y_por_qr(self, base, escenario):
        """El operador puede escanear cualquiera de los dos."""
        from src.modules.devices import service as devices

        qr = f"INV-{uuid.uuid4().hex[:8]}"
        device_id = _crear_dispositivo(escenario, qr_code=qr)
        serial = devices.obtener_dispositivo(device_id)["serial_number"]

        por_serial = devices.resolver_codigo(serial)
        por_qr = devices.resolver_codigo(qr)

        assert por_serial["encontrado"] is True
        assert por_qr["encontrado"] is True
        assert por_serial["dispositivo"]["id"] == device_id
        assert por_qr["dispositivo"]["id"] == device_id

    def test_codigo_inexistente_explica_que_hacer(self, base, escenario):
        """Un 404 con un motivo accionable, no un error generico."""
        from src.modules.devices import service as devices

        resultado = devices.resolver_codigo("INV-no-existe-este-codigo")

        assert resultado["encontrado"] is False
        assert "etiqueta de otro sistema" in resultado["motivo"]

    def test_codigo_vacio_no_revienta(self, base, escenario):
        from src.modules.devices import service as devices

        resultado = devices.resolver_codigo("")

        assert resultado["encontrado"] is False
        assert "vacio" in resultado["motivo"]

    def test_codigo_larguisimo_se_corta(self, base, escenario):
        """
        El limite de longitud evita mandar 4 MB a una consulta de base.

        Un "codigo" enorme es un string, y un string es un ataque de
        recursos si no se lo topa.
        """
        from src.modules.devices import service as devices

        resultado = devices.resolver_codigo("X" * 5000)

        assert resultado["encontrado"] is False
        assert "largo" in resultado["motivo"].lower()
