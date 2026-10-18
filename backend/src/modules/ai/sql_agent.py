"""
Agente de preguntas en lenguaje natural sobre el inventario.

Flujo: pregunta -> SQL validado -> Postgres -> explicacion en prosa.

POR QUE SON DOS LLAMADAS Y NO UNA
---------------------------------
Se podria pedirle al modelo que devuelva a la vez el SQL y la respuesta, y es
lo que hacen casi todos los agentes. Es un error: la respuesta seria INVENTADA,
porque el modelo ya escribio antes de que nadie le dijera quantos hay. Un
`SELECT COUNT(*)` que de 47 devuelve "hay 47 dispositivos" aunque sean 5, y no
hay forma de distinguirlo del acierto.

Aca el SQL se ejecuta primero y la segunda llamada recibe el RESULTADO. Si hay
47 filas, el modelo solo puede decir 47. La alucinacion se reduce a lo que el
modelo puede afirmar sobre un conjunto de numeros que ya tiene delante, que es
mucho mas controllable.

QUE SE LE MANDA EN LA SEGUNDA LLAMADA
------------------------------------
Filas agregadas, con un tope. Si la consulta devolvio 500 filas de detalle, la
segunda llamada recibe un resumen, no las 500. Mandar el detalle seria lo
mismo que mandar filas crudas, con el riesgo de exfiltracion incluido, y sin
ganancia: para explicar "donde hay mas equipos rotos" no hacen falta 500 filas.
"""

from typing import Any

from src.core.config import get_settings
from src.core.llm import LLMError, LLMProvider, get_llm
from src.modules.ai import executor, guard
from src.modules.ai.schema_context import descripcion_esquema
from src.modules.ai.table_format import formatear_tabla

# Cuantas filas se le describen al modelo en la segunda llamada. 60 cabe en la
# ventana de contexto con el esquema adelante y es de sobra para que pueda
# decir "hay 4 mas de esto que aquello".
MAX_FILAS_PARA_EXPLICAR = 60

SYSTEM_SQL = """Eres un analista de datos de un sistema de inventario de dispositivos B2B.

Tu trabajo es escribir UNA consulta SQL de solo lectura (SELECT) que responda la pregunta.

Reglas:
- Responde SIEMPRE con un solo bloque ```sql ... ``` y nada mas. Sin explicaciones.
- Solo SELECT. Nunca INSERT, UPDATE, DELETE, DROP ni ninguna escritura.
- Una sola sentencia. Sin punto y coma al final.
- Usá SIEMPRE el nombre de tabla `models` para el catalogo, y `models.id` como su PK.
- Poné un WHERE cuando la pregunta sea sobre un subconjunto.
- Si la pregunta pide conteos o totales, usá COUNT, SUM o AVG. Si pide un
  detalle, traé las filas.
- No uses subconsultas anidadas con LIMIT.
"""

SYSTEM_RESPUESTA = """Sos el asistente que explica resultados de inventario a un operador.

Tenes el esquema del sistema, la consulta que se ejecuto y las filas que devolvio.

Reglas:
- Usá SOLO los numeros que aparecen en las filas. No calcules nada que no este
  ahi, no completes, no supongas.
- Si las filas estan vacias, decilo claramente: la consulta no encontro datos.
- Si la consulta fallo, explicá en una frase que se puede hacer y cual fue el
  problema.
- Respondé en español rioplatense, breve. Sin preámbulo tipo "¡Claro!" ni
  "Aquí tienes". Directamente la respuesta.
- Si hay varios numeros, decilos como una frase corta, no como una tabla
  Markdown salvo que haya muchas filas.
"""


class PreguntaNoRespondida(RuntimeError):
    """La IA no pudo responder. El router la traduce a un error de servidor."""


class _ErrorDeModelo(Exception):
    """El modelo devolvio algo que no se puede usar. No se lo muestra al usuario crudo."""


def responder_pregunta(pregunta: str, llm: LLMProvider | None = None) -> dict[str, Any]:
    """
    Responde una pregunta sobre el inventario.

    Devuelve el SQL generado, las filas y la explicacion en prosa. Las tres
    cosas se devuelven siempre: el SQL es lo que permite al operador verificar
    de un vistazo que el modelo no invento la fuente, que es la diferencia entre
    una herramienta confiable y una caja negra.
    """
    get_settings()
    llm = llm or get_llm()

    if not pregunta or not pregunta.strip():
        raise ValueError("La pregunta no puede ir vacia.")

    # -- Paso 1: el modelo escribe el SQL -------------------------------
    prompt_sql = (
        f"{descripcion_esquema()}\n\n"
        f"Pregunta: {pregunta}\n\n"
        "Escribi la consulta SQL que responde esto, en un bloque ```sql```."
    )

    try:
        respuesta_sql = llm.chat(prompt_sql, system=SYSTEM_SQL)
    except LLMError as exc:
        raise PreguntaNoRespondida(f"El proveedor de IA no respondio: {exc}") from exc

    try:
        sql = guard.validar_select(guard.extraer_sql(respuesta_sql.text))
    except guard.SQLRechazado as exc:
        # Se devuelve el motivo, no un 500 opaco: si el modelo intento
        # escribir algo, el operador TIENE que verlo.
        raise PreguntaNoRespondida(
            f"La consulta generada fue rechazada: {exc}"
        ) from exc

    # -- Paso 2: ejecutar ------------------------------------------------
    try:
        resultado = executor.ejecutar_lectura(sql)
    except executor.ConsultaRechazada as exc:
        return {
            "pregunta": pregunta,
            "sql": sql,
            "error": str(exc),
            "columnas": [],
            "filas": [],
            "cantidad_filas": 0,
            "respuesta": (
                f"No pude ejecutar la consulta generada. {exc} "
                "Probá reformular la pregunta."
            ),
            "modelo": respuesta_sql.model_used,
        }

    # -- Paso 3: el modelo explica un resultado que ya existe ----------
    filas_para_explicar: list[dict[str, Any]] = resultado["filas"][
        :MAX_FILAS_PARA_EXPLICAR
    ]

    # Formato POSICIONAL, no JSON de diccionarios: con JSON el modelo
    # desalinea las columnas y mete un numero en la etiqueta de al lado.
    contexto = (
        f"Consulta ejecutada: {resultado.get('sql', sql)}\n"
        f"Total de filas: {resultado['cantidad_filas']}\n\n"
        + formatear_tabla(resultado["columnas"], filas_para_explicar)
    )
    prompt_explicar = (
        f"Pregunta original: {pregunta}\n\n"
        f"Resultado de la consulta:\n\n{contexto}\n\n"
        "Explicame el resultado en una o dos frases. "
        "Los valores van en el orden de COLUMNAS: no los reasignes."
    )

    respuesta_final = ""
    modelo_usado = respuesta_sql.model_used
    uso_fallback = respuesta_sql.used_fallback

    try:
        explicacion = llm.chat(prompt_explicar, system=SYSTEM_RESPUESTA)
        respuesta_final = explicacion.text
        modelo_usado = explicacion.model_used
        uso_fallback = uso_fallback or explicacion.used_fallback
    except LLMError:
        # Si el modelo va y viene, las filas siguen siendo la respuesta. Un
        # reporte sin glosa es mil veces mas util que un 502.
        respuesta_final = (
            f"La consulta devolvio {resultado['cantidad_filas']} filas, pero el "
            "modelo no pudo redactar el resumen. Los datos estan en `filas`."
        )

    return {
        "pregunta": pregunta,
        "sql": sql,
        "columnas": resultado["columnas"],
        "filas": resultado["filas"],
        "cantidad_filas": resultado["cantidad_filas"],
        "truncado": resultado["truncado"],
        "respuesta": respuesta_final,
        "modelo": modelo_usado,
        "proveedor": llm.name,
        "usou_fallback": uso_fallback,
        "error": None,
    }
