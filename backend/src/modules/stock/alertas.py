"""
Alertas de stock bajo: el barrido y la consulta.

QUE ES UNA ALERTA
-----------------
Un producto que esta por debajo de su stock minimo, en una ubicacion, desde
una fecha.

QUE HACE ESTE MODULO
--------------------
1. `barrer_alertas()`: recorre el stock y deja UNA alerta abierta por cada
   (producto, ubicacion) que esta por debajo del minimo. Si el stock se
   recupera, la alerta se resuelve sola.

   Es idempotente: el barrido puede correr cada minuto sin llenar la tabla
   de copias de la misma alerta.

2. `listar_alertas()`: las alertas, con la informacion que sirve para
   DECIDIR: el faltante, cuantos dias lleva el producto por debajo del
   minimo, y si la reposicion llega a tiempo con el lead time del proveedor.

POR QUE UNA ALERTA Y NO UN CALCULO EN CADA CONSULTA
----------------------------------------------------
El calculo al momento ("este producto esta bajo minimo") ya existia, y es lo
que se ve en el tablero. Lo que NO existia era el HISTORIAL: cuando aparecio
el faltante y cuanto tiempo lleva.

Sin esa fecha, el operador ve "6 productos por reponer" y no sabe si hay que
comprar hoy o el jueves. Con ella, el sistema dice "este hace 9 dias, y el
lead time es 14: si no se pide hoy, se rompe".

LA ALERTA SE RESUELVE SOLA
--------------------------
Cuando el stock vuelve, el barrido marca la alerta como resuelta y guarda
`resolved_at`. Asi se puede medir cuanto tiempo estuvo el producto por
debajo del minimo, que es el numero que sirve para AJUSTAR el punto de
pedido: un producto que siempre esta por debajo, tiene el minimo mal
puesto.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from src.db.session import get_db_session as db_session_factory

# Dias de cobertura: cuantos dias de stock quedan con el lead time del
# proveedor. Si los dias bajo minimo superan al lead time, la reposicion
# LLEGO TARDE y el stock ya se rompio.
DIAS_PARA_CUBRIR = 3


def barrer_alertas() -> dict[str, Any]:
    """
    Recorre el stock y deja las alertas al dia.

    QUE HACE, PASO A PASO
    ---------------------
    1. Calcula, por (producto, ubicacion), el disponible y el minimo.
    2. Cierra (resuelve) la alerta de los que ya NO estan por debajo: el
       stock se recupero.
    3. Abre o actualiza la de los que SI estan por debajo.

    Por que se resuelve y no se borra: la alerta resuelta es el HISTORIAL.
    "Este producto estubo 9 dias bajo minimo" sale de ahi.
    """
    db = db_session_factory()
    try:
        # 1. El estado real del stock.
        #
        # `available_count` es el disponible REAL (el trigger lo mantiene con
        # cada movimiento). Solo se miran los productos con minimo > 0: un
        # producto sin minimo definido no genera alerta (si no, cada producto
        # del catalogo aparece en la lista de faltantes).
        #
        # `min_stock` se compara con `<=` y no con `<`: un producto con
        # minimo 3 y disponibles 3 esta EN el minimo: todavia no hay faltante
        # para reponer, pero hay que reponer.
        #
        # El filtro de `l.deleted_at` y `m.deleted_at`: una ubicacion o un
        # producto dado de baja no genera alertas.
        #
        # El `ON CONFLICT` del punto 3 depende del indice unico
        # `uq_stock_alerts_modelo_ubicacion` (migracion 0018).
        db.execute(
            text(
                """
                INSERT INTO stock_alerts (
                    model_id, location_id, sku, producto, ubicacion,
                    minimo, actual, faltante, lead_time_days
                )
                SELECT
                    m.id,
                    sl.location_id,
                    m.sku,
                    TRIM(COALESCE(m.brand, '') || ' ' || COALESCE(m.model, '')),
                    l.store_name,
                    m.min_stock,
                    sl.available_count,
                    m.min_stock - sl.available_count,
                    m.lead_time_days
                FROM stock_levels sl
                JOIN models m ON m.id = sl.model_id
                JOIN locations l ON l.id = sl.location_id
                WHERE sl.available_count <= m.min_stock
                  AND m.min_stock > 0
                  AND m.is_active
                  AND m.deleted_at IS NULL
                  AND l.deleted_at IS NULL
                  AND l.is_active
                ON CONFLICT (model_id, location_id) WHERE resuelto = FALSE AND model_id IS NOT NULL
                DO UPDATE SET
                    minimo = EXCLUDED.minimo,
                    actual = EXCLUDED.actual,
                    faltante = EXCLUDED.faltante,
                    ubicacion = EXCLUDED.ubicacion,
                    sku = EXCLUDED.sku,
                    producto = EXCLUDED.producto,
                    lead_time_days = EXCLUDED.lead_time_days,
                    updated_at = NOW()
                """
            )
        )

        # 2. Las que se resolvieron: el stock volvio.
        resueltas = db.execute(
            text(
                """
                UPDATE stock_alerts a
                SET resuelto = TRUE,
                    resolved_at = NOW(),
                    updated_at = NOW()
                WHERE a.resuelto = FALSE
                  AND a.model_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM stock_levels sl
                      JOIN models m ON m.id = sl.model_id
                      WHERE sl.model_id = a.model_id
                        AND sl.location_id = a.location_id
                        AND sl.available_count <= m.min_stock
                        AND m.min_stock > 0
                        AND m.is_active
                        AND m.deleted_at IS NULL
                  )
                RETURNING a.id
                """
            )
        ).rowcount

        # 3. Cuantas hay abiertas ahora.
        abiertas = db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM stock_alerts
                WHERE resuelto = FALSE AND model_id IS NOT NULL
                """
            )
        ).scalar_one()

        db.commit()
        return {"abiertas": int(abiertas or 0), "resueltas": int(resueltas or 0)}
    finally:
        db.close()


def listar_alertas(
    *,
    solo_abiertas: bool = True,
    limite: int = 100,
) -> list[dict[str, Any]]:
    """
    Las alertas de stock bajo, con lo que hace falta para decidir.

    LA INFORMACION QUE SUMA
    -----------------------
    - `dias_bajo_minimo`: cuantos dias lleva el producto por debajo. Es el
      reloj de la reposicion.
    - `dias_para_romperse`: cuantos dias de stock quedan con el lead time
      del proveedor.
    - `reposicion_tarde`: `dias_bajo_minimo` ya supera al lead time: la
      compra llega tarde y el stock se rompe antes.
    """
    db = db_session_factory()
    try:
        where = "WHERE a.resuelto = FALSE AND a.model_id IS NOT NULL" if solo_abiertas else ""
        filas = db.execute(
            text(
                f"""
                SELECT
                    a.id,
                    a.model_id,
                    a.sku,
                    a.producto,
                    a.ubicacion,
                    a.location_id,
                    a.minimo,
                    a.actual,
                    a.faltante,
                    a.lead_time_days,
                    a.created_at,
                    a.updated_at,
                    GREATEST(0, EXTRACT(DAYS FROM (NOW() - a.created_at))::INT) AS dias_bajo_minimo,
                    CASE
                        WHEN a.lead_time_days IS NULL OR a.lead_time_days <= 0 THEN NULL
                        ELSE GREATEST(0, a.lead_time_days - EXTRACT(DAYS FROM (NOW() - a.created_at))::INT)
                    END AS dias_para_romperse,
                    CASE
                        WHEN a.lead_time_days IS NULL OR a.lead_time_days <= 0 THEN FALSE
                        ELSE EXTRACT(DAYS FROM (NOW() - a.created_at))::INT > a.lead_time_days
                    END AS reposicion_tarde
                FROM stock_alerts a
                {where}
                ORDER BY
                    reposicion_tarde DESC,
                    dias_bajo_minimo DESC,
                    a.faltante DESC
                LIMIT :limite
                """
            ),
            {"limite": limite},
        ).mappings().all()

        return [{**dict(f), "id": str(f["id"])} for f in filas]
    finally:
        db.close()
