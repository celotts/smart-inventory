#!/usr/bin/env python3
"""
La CARGA DE PRUEBA: datos que cuentan la historia real de una empresa.

QUE PARA
--------
    python3 scripts/carga-prueba.py --escala media
    python3 scripts/carga-prueba.py --limpiar

POR QUE LOS DATOS TIENEN QUE CONTAR UNA HISTORIA
------------------------------------------------
La primera version generaba datos AL AZAR: equipos con estados
incoherentes, movimientos de tipos que no corresponde, casos todos
nuevos. Con esa carga, medir el rendimiento mide una base de datos que no
existe: no hay casi ningun caso vencido (el tablero del Service Desk esta
vacio), no hay stock bajo (la pantalla de "por reponer" no muestra nada), y
los indices no se usan (las fechas estan todas en el mismo dia).

Con los datos coherentes, la prueba mide lo que el operador hace:
  - hay equipos en las CINCO etapas del ciclo de vida,
  - hay casos EN PLAZO, VENCIDOS y de los que ya se escalaron,
  - hay stock bajo de verdad, y hay stock que hay que reponer,
  - y los movimientos usan los tipos REALES, con su estado destino.

EL CICLO DE VIDA DE UN EQUIPO
-----------------------------
Cada equipo tiene una historia, y su estado FINAL es el de la ultima
etapa (que es lo que la pantalla muestra):

    EN DEPOSITO    WAREHOUSE_ENTRY
    EN TRANSITO    WAREHOUSE_ENTRY -> OUT_TO_CLIENT
    INSTALADO      WAREHOUSE_ENTRY -> OUT_TO_CLIENT -> INSTALLATION
    EN TALLER      ... -> WITHDRAWAL_TO_WORKSHOP
    DADO DE BAJA   ... -> DISPOSAL

El stock lo calcula un trigger de la base, asi que sale solo y bien.

EL SLA
------
El SLA de cada caso lo calcula un trigger, segun la severidad y la hora de
creacion. Para que la bandeja del Service Desk tenga de todo, los casos se
crean en tres momentos: hace minutos (en plazo), hace dias (vencidos) y
hace meses (para que el barrido los escale).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    if os.path.exists("/app/src"):
        DB_URL = "postgresql://postgres:secretpassword@db:5432/smart_inventory"
    else:
        DB_URL = "postgresql://postgres:secretpassword@localhost:5432/smart_inventory"

PREFIJO = "PRUEBA"

# El piso de la carga: una prueba de fuego con 50 equipos no prueba el fuego.
MINIMO_EQUIPOS = 500

ESCALAS = {
    "chica": {
        "productos": 60, "equipos": 600, "movimientos": 3_000,
        "incidencias": 200, "ordenes": 40, "clientes": 6, "ubicaciones": 12,
    },
    "media": {
        "productos": 400, "equipos": 5_000, "movimientos": 40_000,
        "incidencias": 800, "ordenes": 200, "clientes": 8, "ubicaciones": 30,
    },
    "grande": {
        "productos": 2_000, "equipos": 25_000, "movimientos": 200_000,
        "incidencias": 4_000, "ordenes": 800, "clientes": 20, "ubicaciones": 80,
    },
}

# Las historias posibles de un equipo, con el estado final de cada una.
#
# El orden importa: es la secuencia de los movimientos, del mas viejo al mas
# nuevo. El ultimo movimiento define el estado en el que esta el equipo.
HISTORIAS = [
    ("En deposito", ["WAREHOUSE_ENTRY"], "IN_WAREHOUSE"),
    ("En transito", ["WAREHOUSE_ENTRY", "OUT_TO_CLIENT"], "IN_TRANSIT"),
    ("Instalado", ["WAREHOUSE_ENTRY", "OUT_TO_CLIENT", "INSTALLATION"], "INSTALLED"),
    ("En taller", ["WAREHOUSE_ENTRY", "OUT_TO_CLIENT", "INSTALLATION", "WITHDRAWAL_TO_WORKSHOP"], "IN_REPAIR"),
    ("Dado de baja", ["WAREHOUSE_ENTRY", "OUT_TO_CLIENT", "INSTALLATION", "DISPOSAL"], "PERMANENTLY_DISPOSED"),
]

# Las historias no pesan igual: en una empresa real, la mayoria esta
# instalada (es lo que se usa), y hay pocos equipos dados de baja.
PESOS = [10, 15, 55, 12, 8]

MARCAS = ["Dell", "HP", "Lenovo", "Cisco", "Samsung", "Logitech", "Ubiquiti"]
MODELOS_POR_CATEGORIA = {
    "Notebook": ["Latitude 5440", "ThinkPad T14", "EliteBook 840"],
    "Desktop": ["OptiPlex 7010", "ProDesk 400"],
    "Monitor": ["P2422H", "V24", "UltraSharp 27"],
    "Impresora": ["LaserJet M404", "OfficeJet Pro"],
    "Red": ["Catalyst 1000", "UniFi Dream Machine"],
    "Servidor": ["PowerEdge R750", "ProLiant DL360"],
    "Camara": ["DS-2CD2143", "UniFi G4"],
    "UPS": ["Smart-UPS 1500", "Titan 5kVA"],
}
SERVIDORES = ["srv", "dc", "nodo", "sucursal", "taller", "deposito", "central"]
TITULOS = [
    "No enciende", "Pantalla con manchas", "Se reinicia solo",
    "No conecta a la red", "Bateria no carga", "Ruido en el ventilador",
    "Se mojo", "Golpe en la carcasa", "Impresora sin tinta",
    "Lentitud en el sistema", "Falla de disco", "Teclado despegado",
]


def conectar():
    from sqlalchemy import create_engine

    return create_engine(DB_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga de prueba coherente")
    parser.add_argument("--escala", choices=list(ESCALAS), default="media")
    parser.add_argument("--limpiar", action="store_true")
    parser.add_argument("--semilla", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.semilla)
    from sqlalchemy import text

    motor = conectar()

    if args.limpiar:
        with motor.begin() as conexion:
            conexion.execute(
                text("DELETE FROM incident_events WHERE incident_id IN (SELECT id FROM incidents WHERE code LIKE :p)"),
                {"p": "INC-PRUEBA%"},
            )
            conexion.execute(
                text("DELETE FROM incidents WHERE code LIKE :p"), {"p": "INC-PRUEBA%"}
            )
            conexion.execute(
                text("DELETE FROM movement_history WHERE device_id IN (SELECT id FROM devices WHERE qr_code LIKE :p)"),
                {"p": f"{PREFIJO}-%"},
            )
            conexion.execute(
                text("DELETE FROM device_attributes WHERE device_id IN (SELECT id FROM devices WHERE qr_code LIKE :p)"),
                {"p": f"{PREFIJO}-%"},
            )
            conexion.execute(
                text("DELETE FROM devices WHERE qr_code LIKE :p"), {"p": f"{PREFIJO}-%"}
            )
            conexion.execute(
                text("DELETE FROM purchase_order_lines WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE code LIKE :p)"),
                {"p": "OC-PRUEBA%"},
            )
            conexion.execute(
                text("DELETE FROM purchase_orders WHERE code LIKE :p"), {"p": "OC-PRUEBA%"}
            )
            conexion.execute(
                text("DELETE FROM models WHERE sku LIKE :p"), {"p": f"{PREFIJO}-%"}
            )
            conexion.execute(
                text("DELETE FROM categories WHERE name LIKE :p"), {"p": f"{PREFIJO}%"}
            )
            conexion.execute(
                text("DELETE FROM locations WHERE store_name LIKE :p"), {"p": f"{PREFIJO}%"}
            )
            conexion.execute(
                text("DELETE FROM clients WHERE tax_id LIKE :p"), {"p": f"30-{PREFIJO}-%"}
            )
        print("Carga de prueba borrada.")
        return 0

    escala = ESCALAS[args.escala]
    if escala["equipos"] < MINIMO_EQUIPOS:
        print(
            f"La escala {args.escala} genera {escala['equipos']} equipos; "
            f"el piso es {MINIMO_EQUIPOS}.",
            file=sys.stderr,
        )
        return 1

    with motor.begin() as conexion:
        # --- Las personas que hacen los movimientos ------------------------
        #
        # Un ADMIN, un TECNICO y un OPERADOR: los movimientos los hace la
        # gente, y la bitacora tiene que tener de todo (un tecnico que saca
        # equipos del taller, un operador que los saca del deposito).
        personas = {}
        for rol in ("ADMIN", "TECHNICIAN", "OPERATOR"):
            fila = conexion.execute(
                text(
                    """
                    SELECT u.id, u.email
                    FROM users u JOIN roles r ON r.id = u.role_id
                    WHERE r.name = :rol AND u.deleted_at IS NULL
                    ORDER BY u.id LIMIT 1
                    """
                ),
                {"rol": rol},
            ).mappings().first()
            if fila is None:
                print(
                    f"No hay ningun usuario con rol {rol}. "
                    "Corre scripts/usuarios-modulo.sh primero.",
                    file=sys.stderr,
                )
                return 1
            personas[rol] = dict(fila)

        # --- Los tipos de movimiento (los REALES) -------------------------
        tipos = {
            f["name"]: f["id"]
            for f in conexion.execute(
                text("SELECT id, name FROM movement_types WHERE is_active = TRUE")
            ).mappings()
        }
        faltan = {nombre for _, nombres, _ in HISTORIAS for nombre in nombres} - set(tipos)
        if faltan:
            print(f"Faltan tipos de movimiento: {sorted(faltan)}", file=sys.stderr)
            return 1

        # --- CLIENTES ------------------------------------------------------
        clientes = []
        for i in range(escala["clientes"]):
            clientes.append(
                conexion.execute(
                    text(
                        """
                        INSERT INTO clients (commercial_name, tax_id, contact_name, contact_email, contact_phone)
                        VALUES (:nombre, :tax, :contacto, :email, :tel)
                        ON CONFLICT (tax_id) DO UPDATE SET commercial_name = EXCLUDED.commercial_name
                        RETURNING id
                        """
                    ),
                    {
                        "nombre": f"{PREFIJO} Cliente {i + 1}",
                        "tax": f"30-{PREFIJO}-{i + 1:05d}",
                        "contacto": f"Contacto {i + 1}",
                        "email": f"cliente{i + 1}@prueba.local",
                        "tel": f"11-5555-{i + 1:04d}",
                    },
                ).scalar()
            )

        # --- UBICACIONES ---------------------------------------------------
        #
        # Las SEDES de los clientes y los DEPOSITOS, que es como es en una
        # empresa de servicios: el equipo sale del deposito y va a la sede.
        depositos = []
        sedes = []
        for i in range(escala["ubicaciones"]):
            es_deposito = i < max(2, escala["ubicaciones"] // 4)
            nombre = f"{PREFIJO} {'Deposito' if es_deposito else 'Sede'} {i + 1}"
            fila = conexion.execute(
                text(
                    """
                    INSERT INTO locations (client_id, store_name, address, city, latitude, longitude, tolerance_radius_meters)
                    VALUES (:cliente, :nombre, :direccion, :ciudad, :lat, :lon, 60)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "cliente": clientes[i % len(clientes)],
                    "nombre": nombre,
                    "direccion": f"Calle {i + 1} {random.randint(100, 9999)}",
                    "ciudad": random.choice(["Buenos Aires", "Cordoba", "Rosario", "Mendoza"]),
                    "lat": round(random.uniform(-35, -34), 6),
                    "lon": round(random.uniform(-59, -58), 6),
                },
            ).scalar()
            if fila is None:
                fila = conexion.execute(
                    text("SELECT id FROM locations WHERE store_name = :n"), {"n": nombre}
                ).scalar()
            (depositos if es_deposito else sedes).append(fila)

        # --- CATEGORIAS Y PRODUCTOS ---------------------------------------
        categoria_ids = {}
        for nombre_categoria in MODELOS_POR_CATEGORIA:
            categoria_ids[nombre_categoria] = conexion.execute(
                text(
                    "INSERT INTO categories (name) VALUES (:n) "
                    "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                ),
                {"n": nombre_categoria},
            ).scalar()

        modelos = []
        usados = set()
        for i in range(escala["productos"]):
            categoria = random.choice(list(MODELOS_POR_CATEGORIA))
            marca = random.choice(MARCAS)
            base_modelo = random.choice(MODELOS_POR_CATEGORIA[categoria])
            # (marca, modelo) es UNICO en la base, y el `ON CONFLICT` del SKU
            # no lo cubre. Por eso el nombre lleva el numero: es un producto
            # de prueba, y lo unico que importa es que sea unico y que se
            # parezca a un producto de verdad.
            modelo = f"{base_modelo} {i + 1}"
            usados.add((marca, modelo))
            sku = f"{PREFIJO}-{i + 1:06d}"
            fila = conexion.execute(
                text(
                    """
                    INSERT INTO models (category_id, brand, model, sku, unit_cost, min_stock, max_stock, lead_time_days, useful_life_months)
                    VALUES (:categoria, :marca, :modelo, :sku, :costo, :min, :max, :lead, :vida)
                    ON CONFLICT (sku) DO UPDATE SET brand = EXCLUDED.brand, model = EXCLUDED.model
                    RETURNING id
                    """
                ),
                {
                    "categoria": categoria_ids[categoria],
                    "marca": marca,
                    "modelo": modelo,
                    "sku": sku,
                    "costo": round(random.uniform(80, 3000), 2),
                    # El minimo alto hace que haya stock bajo de verdad (y que
                    # la pantalla de "por reponer" tenga algo que mostrar).
                    "min": random.choice([2, 5, 10, 20]),
                    "max": random.choice([50, 100, 200]),
                    "lead": random.choice([7, 14, 30, 45]),
                    "vida": random.choice([24, 36, 48, 60]),
                },
            ).scalar()
            modelos.append(fila)

        # --- EQUIPOS: cada uno con su historia -----------------------------
        print(f"Generando {escala['equipos']} equipos (cada uno con su historia)...")
        equipos = []          # (id, historia, estado_final)
        lote = 2_000
        for base in range(0, escala["equipos"], lote):
            filas = []
            historias = []
            for i in range(base, min(base + lote, escala["equipos"])):
                nombre_historia, pasos, estado = random.choices(HISTORIAS, weights=PESOS)[0]
                filas.append(
                    {
                        "modelo": random.choice(modelos),
                        "serial": f"{PREFIJO}-SN-{i + 1:07d}",
                        "qr": f"{PREFIJO}-{i + 1:07d}",
                        "estado": estado,
                        "ubicacion": random.choice(depositos + sedes),
                        "dias": random.randint(1, 900),
                        "compra": round(random.uniform(100, 4000), 2),
                    }
                )
                historias.append((nombre_historia, pasos, estado, filas[-1]["serial"]))
            # Sin `RETURNING`: con `executemany` de psycopg2 el Returning no
            # devuelve filas. Los ids se recuperan despues, con una consulta
            # por el prefijo del lote.
            conexion.execute(
                text(
                    """
                    INSERT INTO devices
                        (model_id, serial_number, qr_code, current_status, current_location_id,
                         is_warehouse, warehouse_entry_date, installation_date,
                         purchase_date, purchase_value, created_at, updated_at)
                    SELECT :modelo, :serial, :qr, CAST(:estado AS device_status), :ubicacion,
                           (:estado = 'IN_WAREHOUSE'),
                           CASE WHEN :estado = 'IN_WAREHOUSE'
                                THEN NOW() - (:dias || ' days')::interval END,
                           CASE WHEN :estado IN ('INSTALLED', 'IN_REPAIR', 'PERMANENTLY_DISPOSED')
                                THEN NOW() - (:dias || ' days')::interval END,
                           NOW() - (:dias || ' days')::interval,
                           :compra, NOW(), NOW()
                    """
                ),
                filas,
            )
            ids = conexion.execute(
                text(
                    """
                    SELECT id, serial_number FROM devices
                    WHERE serial_number = ANY(:seriales)
                    """
                ),
                {"seriales": [f["serial"] for f in filas]},
            ).mappings().all()
            por_serial = {f["serial_number"]: f["id"] for f in ids}
            for historia in historias:
                equipos.append((por_serial[historia[3]], historia[1], historia[2]))

        # --- MOVIMIENTOS: la historia de cada equipo -----------------------
        #
        # Los movimientos van EN ORDEN (del mas viejo al mas nuevo) y con el
        # tipo que corresponde a cada etapa. La fecha de cada uno esta
        # repartida en el ultimo ano, para que las consultas con filtro de
        # fecha (el tablero, la rotacion) tengan trabajo de verdad.
        print(f"Generando {escala['movimientos']} movimientos (la historia de cada equipo)...")
        lote = 5_000
        for base in range(0, escala["movimientos"], lote):
            filas = []
            for i in range(base, min(base + lote, escala["movimientos"])):
                equipo_id, pasos, _ = random.choice(equipos)
                paso = random.choice(pasos)
                # Quien lo hizo: el deposito (operador) o el taller (tecnico).
                rol = "TECHNICIAN" if paso in ("WITHDRAWAL_TO_WORKSHOP", "DISPOSAL") else "OPERATOR"
                filas.append(
                    {
                        "equipo": equipo_id,
                        "tipo": tipos[paso],
                        "origen": random.choice(depositos),
                        "destino": random.choice(sedes),
                        "usuario": personas[rol]["id"],
                        "dias": random.randint(1, 360),
                    }
                )
            conexion.execute(
                text(
                    """
                    INSERT INTO movement_history
                        (device_id, movement_type_id, origin_location_id, destination_location_id,
                         user_id, movement_date, observations, is_valid_location)
                    SELECT :equipo, :tipo, :origen, :destino, :usuario,
                           NOW() - (:dias || ' days')::interval,
                           'Movimiento de prueba', TRUE
                    """
                ),
                filas,
            )

        # --- INCIDENCIAS: en plazo, vencidas y para escalar ----------------
        #
        # Tres momentos, para que la bandeja del Service Desk tenga de todo:
        # hace minutos (en plazo), hace dias (vencidas) y hace meses (para
        # que el barrido las escale).
        print(f"Generando {escala['incidencias']} incidencias (en plazo, vencidas y para escalar)...")
        for base in range(0, escala["incidencias"], 500):
            filas = []
            for i in range(base, min(base + 500, escala["incidencias"])):
                filas.append(
                    {
                        "equipo": random.choice(equipos)[0],
                        "titulo": random.choice(TITULOS),
                        "severidad": random.choices(["S1", "S2", "S3", "S4"], weights=[8, 22, 45, 25])[0],
                        "dias": random.choice([0, 0, 1, 2, 3, 7, 15, 40, 90, 200]),
                        # Quien lo reporta es un operador (el que ve el equipo
                        # roto en el deposito).
                        "admin": personas["OPERATOR"]["id"],
                    }
                )
            conexion.execute(
                text(
                    """
                    INSERT INTO incidents
                        (device_id, title, description, severity, status, category,
                         reported_by, assigned_to, created_at, updated_at)
                    SELECT :equipo, :titulo, 'Descripcion de prueba', CAST(:severidad AS incident_severity),
                           'OPEN', 'OTHER', :admin, NULL,
                           NOW() - (:dias || ' days')::interval,
                           NOW()
                    """
                ),
                filas,
            )

        # --- ORDENES DE COMPRA --------------------------------------------
        print(f"Generando {escala['ordenes']} ordenes de compra...")
        for base in range(0, escala["ordenes"], 200):
            filas = []
            for i in range(base, min(base + 200, escala["ordenes"])):
                filas.append(
                    {
                        "modelo": random.choice(modelos),
                        "cantidad": random.randint(1, 50),
                        "costo": round(random.uniform(100, 2000), 2),
                        "dias": random.randint(0, 90),
                        "admin": personas["ADMIN"]["id"],
                    }
                )
            conexion.execute(
                text(
                    """
                    INSERT INTO purchase_orders
                        (vendor_id, status, expected_date, created_at, created_by, updated_at)
                    SELECT (SELECT id FROM vendors LIMIT 1), 'SENT', CURRENT_DATE + 7,
                           NOW() - (:dias || ' days')::interval, :admin, NOW()
                    """
                ),
                filas,
            )

        # --- El resumen: lo que la app va a mostrar ------------------------
        print()
        print("  COMO QUEDA LA BASE (lo que va a ver el front):")
        for etiqueta, consulta in [
            ("Equipos", "SELECT count(*) FROM devices WHERE deleted_at IS NULL"),
            ("  en deposito", "SELECT count(*) FROM devices WHERE current_status='IN_WAREHOUSE' AND deleted_at IS NULL"),
            ("  en transito", "SELECT count(*) FROM devices WHERE current_status='IN_TRANSIT' AND deleted_at IS NULL"),
            ("  instalados", "SELECT count(*) FROM devices WHERE current_status='INSTALLED' AND deleted_at IS NULL"),
            ("  en taller", "SELECT count(*) FROM devices WHERE current_status='IN_REPAIR' AND deleted_at IS NULL"),
            ("  dados de baja", "SELECT count(*) FROM devices WHERE current_status='PERMANENTLY_DISPOSED' AND deleted_at IS NULL"),
            ("Movimientos", "SELECT count(*) FROM movement_history"),
            ("Casos abiertos", "SELECT count(*) FROM incidents WHERE status='OPEN'"),
            ("  con SLA calculado", "SELECT count(*) FROM incidents WHERE response_due_at IS NOT NULL"),
            ("Productos", "SELECT count(*) FROM models WHERE deleted_at IS NULL"),
            ("Ubicaciones", "SELECT count(*) FROM locations WHERE deleted_at IS NULL"),
        ]:
            total = conexion.execute(text(consulta)).scalar()
            print(f"    {etiqueta:<24} {total:>8}")

        stock_bajo = conexion.execute(
            text(
                """
                SELECT count(*) FROM (
                    SELECT m.id
                    FROM models m
                    LEFT JOIN stock_levels sl ON sl.model_id = m.id
                    GROUP BY m.id, m.min_stock
                    HAVING COALESCE(SUM(sl.available_count), 0) <= m.min_stock
                ) AS bajo
                """
            )
        ).scalar()
        print(f"    {'Productos por reponer':<24} {stock_bajo:>8}")

    print()
    print("Listo. Para medir: python3 scripts/medir-api.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
