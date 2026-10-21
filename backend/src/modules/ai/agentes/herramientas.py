"""
Las herramientas de los agentes.

QUE SON
-------
Cada herramienta es una FUNCION de Python que devuelve datos ya consultados.
No es SQL que escribe el modelo: es una consulta fija, con sus parametros
ligados, que el agente puede pedir.

POR QUE NO SQL DEL MODELO
-------------------------
El `sql_agent` deja que el modelo escriba la consulta, y el `guard` la
revisa. Funciona, y por eso se sigue usando para las preguntas libres.

Pero una herramienta tiene tres ventajas que el SQL libre no tiene:

  1. **No puede estar mal**: la consulta la escribio y la probo el que
     escribio la app, no el modelo.
  2. **No hay que auditarla**: no hay un parser de SQL que pueda engañarse.
  3. **Es rapida**: el modelo no tiene que deducir el esquema, ya le llega
     el dato.

Y el modelo NO pierde libertad: si la pregunta necesita algo que ninguna
herramienta tiene, escribe su SQL y lo pasa por el guard, como siempre.

EL ROL DE LAS HERRAMIENTAS
--------------------------
Son el CONTEXTO del agente: los numeros que ya estan calculados y que el
modelo no puede inventar. "Cuanto stock hay bajo el minimo" se responde
con la lista real, no con lo que el modelo estima.
"""

from __future__ import annotations

from typing import Any, Callable

from src.modules.purchasing import service as purchasing
from src.modules.reporting import valor_libro
from src.modules.repairs import service as repairs
from src.modules.stock import service as stock


# ---------------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------------

def stock_bajo() -> list[dict[str, Any]]:
    """Los productos que estan en o por debajo de su minimo."""
    return stock.stock_bajo()


def por_reponer(limite: int = 25) -> list[dict[str, Any]]:
    """Los productos que hay que reponer, con su disponible y su minimo."""
    return stock.productos_por_reponer(limite=limite)


def resumen_de_stock() -> list[dict[str, Any]]:
    """El stock por modelo y por ubicacion."""
    return stock.resumen_stock()


# ---------------------------------------------------------------------------
# Mantenimiento
# ---------------------------------------------------------------------------

def reparaciones_abiertas(device_id: int | None = None) -> list[dict[str, Any]]:
    """
    Las reparaciones abiertas.

    Con `device_id` se pregunta por un equipo puntual; sin el, por todas.

    "ABIERTA" son los tres estados en los que un equipo NO esta operativo:
    `DIAGNOSIS` (recien entro), `IN_PROGRESS` (lo estan reparando) y
    `WAITING_FOR_PARTS` (espera un repuesto). Los otros dos, `REPAIRED` y
    `UNREPAIRABLE`, son cierres.
    """
    if device_id is None:
        from sqlalchemy import text

        from src.db.session import get_db_session

        with get_db_session() as db:
            filas = db.execute(
                text(
                    """
                    SELECT r.id,
                           r.device_id,
                           d.serial_number,
                           m.brand,
                           m.model,
                           r.status,
                           r.initial_diagnosis,
                           r.estimated_cost,
                           r.entry_date,
                           r.is_operational,
                           u.full_name AS tecnico
                    FROM repairs r
                    JOIN devices d ON d.id = r.device_id
                    LEFT JOIN models m ON m.id = d.model_id
                    LEFT JOIN users u ON u.id = r.technician_id
                    WHERE r.status IN ('DIAGNOSIS', 'IN_PROGRESS', 'WAITING_FOR_PARTS')
                    ORDER BY r.entry_date DESC
                    LIMIT 50
                    """
                )
            ).mappings().all()
            return [dict(f) for f in filas]
    return repairs.listar_reparaciones(device_id)


# ---------------------------------------------------------------------------
# Compras
# ---------------------------------------------------------------------------

def ordenes_pendientes(limite: int = 25) -> list[dict[str, Any]]:
    """Las ordenes de compra que todavia no recibieron todo."""
    ordenes = purchasing.listar_ordenes()
    pendientes = []
    for orden in ordenes[:limite]:
        lineas = orden.get("lineas", [])
        if any((linea.get("pendiente") or 0) > 0 for linea in lineas):
            pendientes.append(orden)
    return pendientes


def sugerir_reorden() -> list[dict[str, Any]]:
    """La sugerencia de reorden del backend (stock minimo contra cobertura)."""
    return purchasing.sugerir_reorden()


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------

def valor_de_la_flota() -> list[dict[str, Any]]:
    """El valor libro de la flota, equipo por equipo."""
    return valor_libro.valor_libro()


# ---------------------------------------------------------------------------
# El registro
# ---------------------------------------------------------------------------

# El nombre de la herramienta es lo que el agente pide; la funcion es lo que
# se ejecuta. Las dos cosas van juntas para que agregar una herramienta sea
# una linea.
HERRAMIENTAS: dict[str, Callable[..., Any]] = {
    "stock_bajo": stock_bajo,
    "por_reponer": por_reponer,
    "resumen_de_stock": resumen_de_stock,
    "reparaciones_abiertas": reparaciones_abiertas,
    "ordenes_pendientes": ordenes_pendientes,
    "sugerir_reorden": sugerir_reorden,
    "valor_de_la_flota": valor_de_la_flota,
}


def ejecutar(nombre: str, **argumentos: Any) -> Any:
    """
    Ejecuta una herramienta por su nombre.

    Un nombre que no existe lanza `KeyError`: es un error de programacion,
    no algo que venga de afuera.
    """
    herramienta = HERRAMIENTAS[nombre]
    return herramienta(**argumentos)
