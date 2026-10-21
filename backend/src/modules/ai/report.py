"""
Generacion de reportes en lenguaje natural.

DIFERENCIA CON EL AGENTE DE PREGUNTAS
-------------------------------------
`sql_agent` responde a UNA pregunta puntual. Esto arma un reporte: un recorte
fijo de la operacion, con su propio SQL, que despues se explica.

Por que el SQL lo arma una consulta fija y no el modelo: un reporte tiene que
salir IGUAL todas las veces. "Cuanto inventario hay en el almacen" con una
consulta fija da el mismo número el lunes y el martes; con un LLM escribiendo la
consulta, dos días seguidos puede dar dos números distintos porque escribio dos
JOIN distintos. Para un reporte que se usa para decidir, eso es inaceptable.

El modelo redacta. Los números los pone Postgres.

POR QUE EL ALCANCE ES UN Parametro Y NO UNA FRASE LIBRE
-------------------------------------------------------
Un `alcance` en lenguaje natural escrito por el modelo seria otra consulta
generada, con el mismo problema de reproducibilidad. Se limita a los tipos de
recorte que el endpoint implementa, cada uno con su WHERE, y el modelo elige
cual usar a partir de la frase del usuario. El filtro de seguridad de `guard.py`
corre igual sobre el SQL armado.
"""

from typing import Any

from src.core.llm import LLMError, LLMProvider, get_llm
from src.modules.ai import executor, guard
from src.modules.ai.schemas import ReporteResponse
from src.modules.ai.table_format import formatear_tabla

# ---------------------------------------------------------------------------
# Los recortes disponibles. Agregar uno nuevo es agregar una entrada acá y un
# `elif` en `_consulta`. No se escribe SQL nuevo en ningún otro lado.
# ---------------------------------------------------------------------------
PERIODOS = {
    "mes_actual": "el mes en curso",
    "ultimo_trimestre": "los ultimos 90 dias",
    "anio_actual": "el ano en curso",
    "todo": "todo el historico",
}

FILTRO_PERIODO = {
    "mes_actual": "movement_date >= date_trunc('month', NOW())",
    "ultimo_trimestre": "movement_date >= NOW() - INTERVAL '90 days'",
    "anio_actual": "movement_date >= date_trunc('year', NOW())",
    "todo": "TRUE",
}

SISTEMA_REPORTE = """Sos el analista que escribe reportes para el jefe de operaciones de un inventario de dispositivos.

Recibis una lista de valores con su nombre, y una consulta que los produjo.

Reglas:
- Copia cada valor a la frase que le corresponde. Cada línea de los datos es un
  par "nombre: valor": si dice "nombre: 3", en el reporte ese nombre tiene que
  aparecer con 3. No cambies los números de lugar ni de magnitud.
- Un valor de 0 se reporta con la palabra "cero" o "ninguno", NO como "no hay".
  Un valor de 3 o más se reporta con la cifra.
- No inventes valores que no esten escritos.
- Arranca con la conclusion en la primera frase. No copies ni menciones estás
  instrucciones: solo el texto del reporte.
- Máximo 200 palabras. Es un reporte de una pantalla.
- Español rioplatense, directo, sin preambulo.
- Sin tablas Markdown salvo que haya muchas filas.

EJEMPLO DE FORMATO, con datos inventados para que veas la mecanica:
  datos: "- hp: 3"  "- lokasi: 1"
  reporte: "Hay 3 equipos hp y 1 en lokasi."
"""


def generar_reporte(
    alcance: str | None = None,
    periodo: str = "mes_actual",
    tipo: str = "resumen",
    llm: LLMProvider | None = None,
) -> ReporteResponse:
    llm = llm or get_llm()

    if periodo not in PERIODOS:
        periodo = "mes_actual"

    sql, alcance_aplicado = _construir_sql(alcance, periodo)

    # Se ejecuta SIEMPRE, incluso si el modelo está caido. Un reporte con los
    # números y sin la glosa es utilizable; un 502 sin números no lo es.
    try:
        resultado = executor.ejecutar_lectura(sql)
    except (executor.ConsultaRechazada, guard.SQLRechazado) as exc:
        return ReporteResponse(
            tipo=tipo,
            alcance=alcance,
            periodo=periodo,
            reporte=f"No se pudo generar el reporte: {exc}",
            error=str(exc),
            proveedor=llm.name,
        )

    # Formato POSICIONAL, no JSON de diccionarios: ver table_format.py. Con
    # JSON, el modelo decio "no hay equipos en reparación" habiendo uno.
    contexto = (
        f"TIPO: {tipo}\n"
        f"ALCANCE: {alcance_aplicado}\n"
        f"PERIODO: {PERIODOS[periodo]}\n"
        f"TOTAL DE FILAS: {resultado['cantidad_filas']}\n\n"
        + formatear_tabla(resultado["columnas"], resultado["filas"])
    )

    try:
        respuesta = llm.chat(
            f"Datos del reporte:\n\n{contexto}\n\nEscribi el reporte.",
            system=SISTEMA_REPORTE,
        )
        texto = respuesta.text
        modelo = respuesta.model_used
        latencia = respuesta.latency_ms
    except LLMError:
        texto = (
            f"El proveedor de IA no respondio. Los datos del reporte estan "
            f"disponibles en `datos` ({resultado['cantidad_filas']} filas)."
        )
        modelo = None
        latencia = None

    return ReporteResponse(
        tipo=tipo,
        alcance=alcance_aplicado,
        periodo=periodo,
        reporte=texto,
        sql=sql,
        datos=resultado["filas"],
        columnas=resultado["columnas"],
        modelo=modelo,
        proveedor=llm.name,
        latencia_ms=latencia,
    )


def _construir_sql(alcance: str | None, periodo: str) -> "tuple[str, str]":
    """
    Devuelve (sql, alcance_aplicado).

    `alcance_aplicado` puede diferir del pedido: si el usuario pide un recorte
    que no existe, se usa el general y se DICE cual fue. Devolver el recorte
    pedido sin aplicarlo haria que el reporte dijera una cosa y mostrara otra.
    """
    filtro = FILTRO_PERIODO[periodo]

    if not alcance:
        return (
            """
            SELECT
                COUNT(*) FILTER (WHERE d.deleted_at IS NULL) AS equipos_activos,
                COUNT(*) FILTER (WHERE d.is_warehouse AND d.deleted_at IS NULL) AS en_almacen,
                COUNT(*) FILTER (WHERE d.current_status = 'INSTALLED' AND d.deleted_at IS NULL) AS instalados,
                COUNT(*) FILTER (WHERE d.current_status = 'IN_REPAIR' AND d.deleted_at IS NULL) AS en_reparacion,
                COUNT(*) FILTER (WHERE d.current_status = 'IN_TRANSIT' AND d.deleted_at IS NULL) AS en_transito,
                COUNT(*) FILTER (WHERE d.current_status = 'PERMANENTLY_DISPOSED') AS dados_de_baja
            FROM devices d
            """,
            "inventario completo",
        )

    texto = alcance.lower()

    # Un recorte por cliente. Se busca por el tax_id, no por el nombre: el
    # nombre tiene espacios y acentos y el tax_id es único. Buscar por texto
    # libre armaria el SQL con un LIKE sobre un valor del usuario.
    if "cliente" in texto or "sede" in texto:
        return (
            """
            SELECT
                COALESCE(cl.commercial_name, 'Sin cliente asignado') AS cliente,
                COUNT(d.id) AS equipos,
                COUNT(*) FILTER (WHERE d.is_warehouse) AS en_almacen,
                COUNT(*) FILTER (WHERE d.current_status = 'IN_REPAIR') AS en_reparacion,
                COUNT(*) FILTER (WHERE d.current_status = 'INSTALLED') AS instalados
            FROM devices d
            JOIN locations l ON l.id = d.current_location_id
            JOIN clients cl   ON cl.id = l.client_id
            WHERE d.deleted_at IS NULL
            GROUP BY cl.commercial_name
            ORDER BY equipos DESC
            """,
            "inventario por cliente",
        )

    if "reparacion" in texto or "taller" in texto or "costo" in texto:
        return (
            """
            SELECT
                r.status AS estado,
                COUNT(*) AS cantidad,
                SUM(COALESCE(r.estimated_cost, 0)) AS costo_estimado_total,
                ROUND(AVG(COALESCE(r.estimated_cost, 0)), 2) AS costo_promedio,
                COUNT(*) FILTER (WHERE r.is_operational) AS reparados_operativos
            FROM repairs r
            JOIN devices d ON d.id = r.device_id
            WHERE d.deleted_at IS NULL AND r.entry_date >= NOW() - INTERVAL '1 year'
            GROUP BY r.status
            ORDER BY cantidad DESC
            """,
            "actividad del taller en el ultimo año",
        )

    if "movimiento" in texto or "actividad" in texto:
        return (
            f"""
            SELECT
                mt.name AS tipo,
                COUNT(*) AS cantidad,
                COUNT(*) FILTER (WHERE mh.is_valid_location = FALSE) AS fuera_de_radio,
                -- AVG(booleano) NO EXISTE en Postgres: no hay conversion
                -- implicita de bool a numeric, y la consulta muere con
                -- "function avg(boolean) does not exist". El CASE es la forma.
                ROUND(AVG(CASE WHEN mh.is_valid_location THEN 1.0 ELSE 0.0 END) * 100, 1)
                    AS pct_validos
            FROM movement_history mh
            JOIN movement_types mt ON mt.id = mh.movement_type_id
            WHERE {filtro}
            GROUP BY mt.name
            ORDER BY cantidad DESC
            """,
            f"movimientos de {PERIODOS[periodo]}",
        )

    # Ningún recorte conocido. Se usa el general y se avisa, en vez de
    # inventar un WHERE con texto del usuario.
    sql, _ = _construir_sql(None, periodo)
    return sql, "inventario completo (el recorte pedido no existe)"


def listar_alcances() -> list[dict[str, Any]]:
    """Lo que el endpoint acepta. Para que el frontend no adivine."""
    return [
        {"clave": "general", "descripcion": "Inventario completo por estado."},
        {"clave": "clientes", "descripcion": "Equipos agrupados por cliente."},
        {
            "clave": "movimientos",
            "descripcion": "Actividad de movimientos del periodo.",
        },
        {"clave": "taller", "descripcion": "Reparaciones por estado y costo."},
    ]
