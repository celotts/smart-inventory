"""
Exportacion de datos a CSV.

POR QUE CSV Y NO XLSX
--------------------
El proyecto no quiere dependencias nuevas, y un `.xlsx` es un ZIP con XML
dentro: escribirlo a mano es una tarea de un día entero. El CSV se abre con
Excel, con LibreOffice, con Google Sheets y con `import` de pandas, así que
para el caso de uso real —"pasame el inventario en Excel"— es lo mismo.

EL BOM UTF-8
------------
Sin BOM, Excel abre un CSV en UTF-8 con los acentos rotos: "Depósito" queda
"DepÃ³sito". El BOM (tres bytes al principio) le dice a Excel que el archivo
es UTF-8, y Excel lo respeta. Es un detalle de un byte que decide si el
operador ve "MÃ³vil" o "Móvil" en la primera fila.

EL SEPARADOR
------------
El `;` y no la `,`: el separador de la coma es el separador DECIMAL en
español, y un "1.234,56" en un CSV con comas rompe las columnas en el lugar
equivocado. El `;` con el decimal con coma es lo que Excel espera en un
archivo abierto con la configuración regional de un sistema en castellano.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from src.db.session import get_db_session

# El BOM de UTF-8. Sin el, Excel rompe los acentos.
BOM = "﻿"

SEPARADOR = ";"


# Los caracteres con los que Excel interpreta una celda como una FORMULA.
# Una celda que empieza por estos, cuando se abre el CSV, se EVALUA: no es
# texto, es codigo.
PREFIJOS_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _celda_segura(valor: Any) -> str:
    """
    Una celda que Excel no pueda ejecutar.

    EL RIESGO REAL
    --------------
    `csv.writer` escapa el separador y las comillas, asi que el CSV es
    correcto como CSV. Lo que NO escapa es que la celda EMPIECE por `=`, `+`,
    `-` o `@`: Excel (y LibreOffice) lo toman como una formula y la
    EVALUAN al abrir el archivo.

    Y los valores vienen de la base, que es de gente que escribe: el nombre
    de un producto, el de un cliente, el titulo de un caso, el serial de un
    equipo leido de una etiqueta. Con eso, cualquiera que pueda dar de alta un
    producto puede dejar en el CSV un `=HYPERLINK(...)` o un `=cmd|...` que
    se ejecuta en la maquina de quien abre el listado (el jefe).

    El prefijo de comilla simple (`'`) es la forma estandar de neutralizarlo:
    Excel lo muestra como texto y no lo evalua.

    El `\\t` y el `\\r` tambien se escapan: hay lectores que los saltean
    como un separador de celdas y convierten la celda siguiente en formula.
    """
    if valor is None:
        return ""
    # Un numero no es una formula: el `-` de un negativo es un signo, y
    # prefijarlo convertiria la celda en texto (y el "menor que" del jefe
    # dejaria de funcionar).
    if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
        return str(valor)
    texto = str(valor)
    if texto.startswith(PREFIJOS_DE_FORMULA):
        return "'" + texto
    return texto


def _a_csv(encabezados: list[str], filas: list[list[Any]]) -> str:
    """
    Arma el CSV.

    `csv.writer` con `lineterminator='\\r\\n'`: es lo que espera Excel en
    Windows, y con `\\n` las líneas se ven pegadas en algunos lectores.

    Cada celda pasa por `_celda_segura`: el CSV se abre en Excel, y Excel
    EJECUTA las celdas que empiezan por `=`.
    """
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=SEPARADOR, lineterminator="\r\n")
    escritor.writerow(encabezados)
    for fila in filas:
        escritor.writerow([_celda_segura(v) for v in fila])
    return BOM + buffer.getvalue()


def inventario() -> str:
    """
    El inventario completo, una fila por equipo.

    Que es lo que el operador necesita cuando el jefe pide "el listado de
    todo lo que tenemos": con el estado, la ubicación y el producto.
    """
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT
                    d.serial_number,
                    d.qr_code,
                    m.sku,
                    m.brand,
                    m.model,
                    m.variant,
                    c.name AS categoria,
                    d.current_status,
                    l.store_name AS ubicacion,
                    l.location_type AS tipo_ubicacion,
                    d.installation_date,
                    m.unit_cost,
                    (m.unit_cost * CASE WHEN d.current_status <> 'PERMANENTLY_DISPOSED' THEN 1 ELSE 0 END) AS valor
                FROM devices d
                JOIN models m ON m.id = d.model_id
                JOIN categories c ON c.id = m.category_id
                LEFT JOIN locations l ON l.id = d.current_location_id
                WHERE d.deleted_at IS NULL
                ORDER BY c.name, m.brand, m.model, d.serial_number
                """
            )
        ).mappings().all()
    finally:
        db.close()

    encabezados = [
        "Serial",
        "Codigo QR",
        "SKU",
        "Marca",
        "Modelo",
        "Variante",
        "Categoria",
        "Estado",
        "Ubicacion",
        "Tipo ubicacion",
        "Fecha instalacion",
        "Costo unitario",
        "Valor",
    ]
    return _a_csv(
        encabezados,
        [
            [
                f["serial_number"],
                f["qr_code"],
                f["sku"],
                f["brand"],
                f["model"],
                f["variant"],
                f["categoria"],
                f["current_status"],
                f["ubicacion"],
                f["tipo_ubicacion"],
                f["installation_date"],
                f["unit_cost"],
                f["valor"],
            ]
            for f in filas
        ],
    )


def productos() -> str:
    """El maestro de productos, con su stock y sus códigos de etiqueta."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT
                    m.sku,
                    m.brand,
                    m.model,
                    m.variant,
                    c.name AS categoria,
                    m.qr_code,
                    m.barcode,
                    m.unit_cost,
                    m.list_price,
                    m.currency,
                    m.min_stock,
                    m.max_stock,
                    (SELECT COUNT(*) FROM devices d
                      WHERE d.model_id = m.id AND d.deleted_at IS NULL) AS equipos,
                    (SELECT COUNT(*) FROM devices d
                      WHERE d.model_id = m.id AND d.deleted_at IS NULL
                        AND d.current_status = 'IN_WAREHOUSE') AS en_deposito,
                    m.is_active
                FROM models m
                JOIN categories c ON c.id = m.category_id
                WHERE m.deleted_at IS NULL
                ORDER BY c.name, m.brand, m.model
                """
            )
        ).mappings().all()
    finally:
        db.close()

    encabezados = [
        "SKU",
        "Marca",
        "Modelo",
        "Variante",
        "Categoria",
        "Codigo QR",
        "Codigo de barras",
        "Costo unitario",
        "Precio de lista",
        "Moneda",
        "Stock minimo",
        "Stock maximo",
        "Equipos",
        "En deposito",
        "Activo",
    ]
    return _a_csv(
        encabezados,
        [
            [
                f["sku"],
                f["brand"],
                f["model"],
                f["variant"],
                f["categoria"],
                f["qr_code"],
                f["barcode"],
                f["unit_cost"],
                f["list_price"],
                f["currency"],
                f["min_stock"],
                f["max_stock"],
                f["equipos"],
                f["en_deposito"],
                "Si" if f["is_active"] else "No",
            ]
            for f in filas
        ],
    )


def movimientos(limite: int = 5000) -> str:
    """
    El historial de movimientos: quien movio que, cuando, de donde a donde.

    El `límite` tiene tope a propósito: un export sin tope de un sistema con
    un million de movimientos devuelve un archivo de un gigabyte, y el
    navegador del operador se queda sin memoria.
    """
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT
                    mh.movement_date,
                    mt.label AS movimiento,
                    d.serial_number,
                    d.qr_code,
                    m.sku,
                    m.brand,
                    m.model,
                    lo.store_name AS origen,
                    ld.store_name AS destino,
                    u.full_name AS usuario,
                    mh.observations
                FROM movement_history mh
                JOIN devices d ON d.id = mh.device_id
                JOIN models m ON m.id = d.model_id
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                LEFT JOIN locations lo ON lo.id = mh.origin_location_id
                LEFT JOIN locations ld ON ld.id = mh.destination_location_id
                LEFT JOIN users u ON u.id = mh.user_id
                ORDER BY mh.movement_date DESC
                LIMIT :limite
                """
            ),
            {"limite": limite},
        ).mappings().all()
    finally:
        db.close()

    encabezados = [
        "Fecha",
        "Movimiento",
        "Serial",
        "Codigo QR",
        "SKU",
        "Marca",
        "Modelo",
        "Origen",
        "Destino",
        "Usuario",
        "Observaciones",
    ]
    return _a_csv(
        encabezados,
        [
            [
                f["movement_date"],
                f["movimiento"],
                f["serial_number"],
                f["qr_code"],
                f["sku"],
                f["brand"],
                f["model"],
                f["origen"],
                f["destino"],
                f["usuario"],
                f["observations"],
            ]
            for f in filas
        ],
    )


def auditoria(limite: int = 5000) -> str:
    """La línea de tiempo de la auditoría: que se creo, que se toco y quien."""
    db = get_db_session()
    try:
        filas = db.execute(
            text(
                """
                SELECT
                    al.created_at,
                    al.table_name,
                    al.record_id,
                    al.action,
                    al.old_value,
                    al.new_value,
                    u.full_name AS usuario,
                    al.ip_address
                FROM audit_logs al
                LEFT JOIN users u ON u.id = al.user_id
                ORDER BY al.created_at DESC
                LIMIT :limite
                """
            ),
            {"limite": limite},
        ).mappings().all()
    finally:
        db.close()

    encabezados = ["Fecha", "Tabla", "Registro", "Accion", "Valor anterior", "Valor nuevo", "Usuario", "IP"]
    return _a_csv(
        encabezados,
        [
            [
                f["created_at"],
                f["table_name"],
                f["record_id"],
                f["action"],
                f["old_value"],
                f["new_value"],
                f["usuario"],
                f["ip_address"],
            ]
            for f in filas
        ],
    )
