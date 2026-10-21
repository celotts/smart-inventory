"""
Valor libro de los activos: lo que el equipo vale HOY.

QUE ES
------
El sistema sabe cuanto costaba un equipo. No sabia cuanto vale. Para una
empresa de servicios, la pregunta que hace el cliente (y contabilidad) es
exactamente esa: "cuanto vale mi flota ahora".

COMO SE CALCULA
---------------
Depreciacion LINEAL, la mas simple y la que se puede explicar en una
reunion:

    depreciacion_mensual = precio_de_compra / vida_util_en_meses
    depreciacion_acumulada = meses_transcurridos * depreciacion_mensual
    valor_libro = precio_de_compra - depreciacion_acumulada

Con dos limites que evitan los numeros raros:

- La depreciacion acumulada no pasa del precio de compra (el valor libro no
  es NEGATIVO: un equipo vale cero, no "debe" dinero).
- Pasada la vida util, el valor libro es CERO.

LO QUE NO SE INVENTA
--------------------
- Si el equipo no tiene fecha de compra, no se deprecia: no hay con que
  calcular los meses, y un valor libro estimado con una fecha inventada es
  una mentira con formato de numero.
- Si el modelo no tiene vida util, no se deprecia: se deberia definir (es un
  dato del modelo, no del equipo) pero no se inventa un default.
- El valor residual no se considera: es un dato del negocio (un equipo de
  TI suele ser residual cero o un porcentaje del precio), y si el negocio
  no lo define, es cero.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from src.db.session import get_db_session as db_session_factory

# El SQL del calculo. Va en SQL y no en Python por una razon: el filtro (por
# cliente, por estado, por categoria) se aplica ANTES de calcular, y el
# total sale de las mismas filas que se listan. Calcular en Python y traer
# todo para filtrar despues es traer el inventario entero del cliente para
# mostrar diez filas.
SQL_VALOR_LIBRO = """
SELECT
    d.id AS device_id,
    d.serial_number,
    d.qr_code,
    d.current_status,
    d.purchase_date,
    d.purchase_value,
    m.id AS model_id,
    m.sku,
    m.brand,
    m.model AS model_name,
    m.useful_life_months,
    COALESCE(c.name, '') AS category_name,
    COALESCE(l.store_name, '') AS location_name,
    COALESCE(cl.commercial_name, '') AS client_name,

    -- Meses que pasaron desde la compra. Negative si la compra es futura:
    -- un equipo comprado mañana vale lo que vale, no menos.
    CASE
        WHEN d.purchase_date IS NULL OR m.useful_life_months IS NULL
             OR m.useful_life_months <= 0 THEN NULL
        ELSE GREATEST(
            0,
            (EXTRACT(YEAR FROM AGE(CURRENT_DATE, d.purchase_date))::INT * 12)
            + EXTRACT(MONTH FROM AGE(CURRENT_DATE, d.purchase_date))::INT
        )
    END AS meses_transcurridos,

    -- La depreciacion mensual.
    CASE
        WHEN d.purchase_value IS NULL OR m.useful_life_months IS NULL
             OR m.useful_life_months <= 0 THEN NULL
        ELSE d.purchase_value / m.useful_life_months
    END AS depreciacion_mensual,

    -- La depreciacion acumulada, sin pasar del precio: el valor libro de un
    -- equipo no es negativo.
    CASE
        WHEN d.purchase_value IS NULL OR m.useful_life_months IS NULL
             OR m.useful_life_months <= 0 THEN NULL
        ELSE LEAST(
            d.purchase_value,
            GREATEST(0, (
                (EXTRACT(YEAR FROM AGE(CURRENT_DATE, d.purchase_date))::INT * 12)
                + EXTRACT(MONTH FROM AGE(CURRENT_DATE, d.purchase_date))::INT
            )) * (d.purchase_value / m.useful_life_months)
        )
    END AS depreciacion_acumulada,

    -- El valor libro de HOY.
    CASE
        WHEN d.purchase_value IS NULL THEN NULL
        WHEN m.useful_life_months IS NULL OR m.useful_life_months <= 0
            THEN d.purchase_value
        ELSE GREATEST(
            0,
            d.purchase_value - LEAST(
                d.purchase_value,
                GREATEST(0, (
                    (EXTRACT(YEAR FROM AGE(CURRENT_DATE, d.purchase_date))::INT * 12)
                    + EXTRACT(MONTH FROM AGE(CURRENT_DATE, d.purchase_date))::INT
                )) * (d.purchase_value / m.useful_life_months)
            )
        )
    END AS valor_libro,

    -- Si la vida util ya se cumplio, el equipo esta totalmente amortizado.
    CASE
        WHEN d.purchase_date IS NULL OR m.useful_life_months IS NULL
             OR m.useful_life_months <= 0 THEN FALSE
        ELSE (
            (EXTRACT(YEAR FROM AGE(CURRENT_DATE, d.purchase_date))::INT * 12)
            + EXTRACT(MONTH FROM AGE(CURRENT_DATE, d.purchase_date))::INT
        ) >= m.useful_life_months
    END AS amortizado
FROM devices d
JOIN models m ON m.id = d.model_id
LEFT JOIN categories c ON c.id = m.category_id
LEFT JOIN locations l ON l.id = d.current_location_id
LEFT JOIN clients cl ON cl.id = l.client_id
WHERE d.deleted_at IS NULL
  AND m.deleted_at IS NULL
"""


def valor_libro(
    *,
    client_id: int | None = None,
    estado: str | None = None,
    limite: int = 500,
) -> dict[str, Any]:
    """
    El valor libro de los activos, equipo por equipo y en total.

    `client_id` filtra por el cliente dontre esta instalado el equipo: es el
    recorte que pide el cliente ("cuanto vale lo que tengo en la calle").
    """
    condiciones = []
    parametros: dict[str, Any] = {"limite": limite}

    if client_id:
        condiciones.append("l.client_id = :client_id")
        parametros["client_id"] = client_id
    if estado:
        condiciones.append("d.current_status = :estado")
        parametros["estado"] = estado

    where = ""
    if condiciones:
        where = " AND " + " AND ".join(condiciones)

    db = db_session_factory()
    try:
        filas = db.execute(
            text(
                f"""
                {SQL_VALOR_LIBRO}
                {where}
                ORDER BY valor_libro DESC NULLS LAST, d.serial_number
                LIMIT :limite
                """
            ),
            parametros,
        ).mappings().all()

        # Los totales, sobre las MISMAS filas que se listan (no sobre todo el
        # inventario): si el listado esta filtrado, el total tambien.
        total_costo = sum(float(f["purchase_value"] or 0) for f in filas)
        total_depreciado = sum(float(f["depreciacion_acumulada"] or 0) for f in filas)
        total_libro = sum(float(f["valor_libro"] or 0) for f in filas)
        con_datos = sum(1 for f in filas if f["purchase_value"] is not None)
        amortizados = sum(1 for f in filas if f["amortizado"])

        return {
            "items": [{**dict(f), "device_id": int(f["device_id"])} for f in filas],
            "resumen": {
                "equipos": len(filas),
                "equipos_con_precio": con_datos,
                "costo_total": round(total_costo, 2),
                "depreciacion_acumulada": round(total_depreciado, 2),
                "valor_libro_total": round(total_libro, 2),
                "equipos_amortizados": amortizados,
                # El porcentaje de depreciacion de la flota, con los equipos que
                # tienen precio conocido. Sin precio no hay valor libro: se
                # dice cuantos son, en vez de promediar sobre la mitad.
                "depreciacion_pct": (
                    round(100.0 * total_depreciado / total_costo, 1) if total_costo > 0 else None
                ),
            },
        }
    finally:
        db.close()
