"""
Ejecucion de consultas de Solo LECTURA.

La regla de la casa: este módulo es el Único que puede tocar la base por
cuenta de la IA. Si aparece un `engine.execute` en otro lado, es un bug de
revision.

Las tres garantias que aporta, y por que estan aqui y no en el router:

  - READ ONLY: lo impone Postgres con `SET TRANSACTION READ ONLY`. Es la única
    garantia que no depende de que el validador textual de `guard.py` se haya
    escrito bien. Si el SQL llega a ejecutar un INSERT, la transaccion aborta
    sola.
  - statement_timeout: el motor corta la consulta. Un modelo chico que responde
    "SELECT * FROM movement_history" sin filtro no cuelga la API.
  - rollback explicito: la transaccion se cierra sin commit aunque la lectura
    haya ido bien. Una lectura no deja nada que confirmar y Asi queda escrito.
"""

from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from src.core.config import get_settings
from src.modules.ai import guard

# Pool chico a propósito. Este motor es el que usa la IA, que hace pocas
# consultas y cortas. Un pool grande seria conexiones de Postgres
# reservadas para requests que van a durar milisegundos.
_engine: Engine | None = None


def get_ai_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_size=3,
            max_overflow=2,
            pool_pre_ping=True,
        )
    return _engine


class ConsultaRechazada(RuntimeError):
    """La base rechazó la consulta. No es culpa del modelo necesariamente."""


def ejecutar_lectura(sql: str) -> dict[str, Any]:
    """
    Ejecuta un SELECT ya validado y devuelve filas como datos planos.

    Las filas se normalizan a `dict` porque JSON no tiene tipo Decimal ni
    datetime: `estimated_cost NUMERIC(10,2)` viene como Decimal y
    `movement_date` como datetime, y FastAPI los serializa mal o los corta.
    Normalizar aqui es lo que evita el error de "Object of type Decimal is not
    JSON serializable" que de otro modo aparece en el router, tres archivos
    más lejos de la causa.
    """
    config = get_settings()

    sql_validado = guard.validar_select(sql)
    sql_limitado = guard.aplicar_limite(sql_validado, config.ai_max_rows)

    engine = get_ai_engine()

    try:
        with engine.connect() as conexion:
            transaccion = conexion.begin()

            # Estás dos tienen que ser las primeras sentencias de la
            # transaccion; Postgres las ignora si ya se ejecuto algo.
            conexion.execute(text("SET TRANSACTION READ ONLY"))
            # statement_timeout va en milisegundos y es POR Conexión de la
            # transaccion: al hacer rollback desaparece solo, no hay que
            # acordarse de resetearlo.
            conexion.execute(
                text(
                    f"SET LOCAL statement_timeout = {int(config.ai_query_timeout_seconds * 1000)}"
                )
            )

            resultado = conexion.execute(text(sql_limitado))
            columnas = list(resultado.keys())
            # `.mappings()` y no `dict(row)`: en SQLAlchemy 2.0 la Row no es
            # convertible con `dict()` y revienta con
            # "cannot convert dictionary update sequence element #0 to a
            # sequence". El RowMapping si.
            filas = [_normalizar(dict(fila)) for fila in resultado.mappings().all()]

            transaccion.rollback()  # lectura: no hay nada que confirmar
    except guard.SQLRechazado:
        # Ya viene con el motivo. Se deja pasar tal cual.
        raise
    except OperationalError as exc:
        # statement_timeout se manifiesta como OperationalError con SQLSTATE
        # 57014. Traducirlo a un mensaje entendible evita el "canceling statement
        # due to statement timeout" sin contexto que deja el operador pensando
        # que la base está mala.
        if "57014" in str(exc.orig):
            raise ConsultaRechazada(
                f"La consulta tardo mas de {config.ai_query_timeout_seconds}s y fue "
                "cortada. Suele ser una consulta sin filtro: agrega un WHERE con "
                "un rango de fechas o un LIMIT."
            ) from exc
        raise ConsultaRechazada(f"No se pudo ejecutar la consulta: {exc}") from exc
    except DBAPIError as exc:
        # Lo más comun: error de sintaxis del SQL que genero el modelo. Se
        # devuelve el mensaje de Postgres, que para un modelo de 3b es la
        # información más útil que hay: le dice que columna no existe.
        raise ConsultaRechazada(
            f"Postgres rechazo la consulta: {str(exc.orig)[:300]}"
        ) from exc

    return {
        "columnas": columnas,
        "filas": filas,
        "cantidad_filas": len(filas),
        "truncado": len(filas) >= config.ai_max_rows,
    }


def _normalizar(valor: Any) -> Any:
    """
    Convierte tipos de Postgres/psycopg2 a algo que `json.dumps` tolere.

    Decimal viene de NUMERIC, datetime de los TIMESTAMP sin zona, y UUID de las
    PKs que algun día se agreguen. Los tres existen hoy en el esquema y los tres
    revientan el JSON encoder si no se convierten.
    """
    import datetime
    import decimal
    import uuid

    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    if isinstance(valor, decimal.Decimal):
        # float y no str: el reporte se muestra en pantalla. Un costo de
        # 1234.56 como texto sale con comillas y parece un bug de formato.
        return float(valor)
    if isinstance(valor, (datetime.datetime, datetime.date, datetime.time)):
        return valor.isoformat()
    if isinstance(valor, uuid.UUID):
        return str(valor)
    if isinstance(valor, (list, tuple)):
        return [_normalizar(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _normalizar(v) for k, v in valor.items()}
    # JSONB y demas: el driver ya los devuelve como dict/list y normalmente
    # caen en las ramas de arriba. Este último return es el red de seguridad.
    return str(valor)
