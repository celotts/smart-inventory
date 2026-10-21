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
from src.modules.ai import executor, guard, intencion
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

COMO USAR EL HISTORIAL
----------------------
Si te pasan turnos anteriores de la conversacion, la pregunta actual puede
referirse a ellos con palabras como "ese", "esos", "el anterior", "los de
arriba", "de esos". En ese caso:

- USÁ el mismo conjunto de la pregunta anterior: si antes se contó lo que
  hay en deposito, ahora se pide el DETALLE de lo que hay en deposito.
- NO cambies el tema por tu cuenta. Si la pregunta anterior era de
  EQUIPOS y esta pide "cual es ese equipo", segui con EQUIPOS. Cambiar de
  tema es lo que hace que la respuesta contradiga a la anterior.
- Si la pregunta pide el detalle de algo que antes se CONTÓ: partí de la
  consulta anterior del historial, sacá el COUNT(*) y poné en su lugar las
  columnas que identifican a cada fila (serial_number, qr_code, model, brand,
  store_name). MANTENÉ EL WHERE EXACTO de la consulta anterior.
- En ese caso NO agregues condiciones: en particular NO inventes ids. Si la
  consulta anterior contaba los equipos en deposito, la de detalle trae los
  equipos en deposito y dice CUALES. Un "WHERE d.id = 1" inventado hace que
  la consulta no devuelva nada.
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
- NUNCA menciones un valor que no este en el resultado. Si el resultado tiene
  un solo dato, no hay ningun otro: escribir "id: 1" o un numero de serie
  que no vino en las filas es INVENTAR el detalle de un equipo, y el operador
  lo toma como dato real.

COMO DESCRIBIR UN DETALLE
-------------------------
Si la consulta trae filas de DETALLE (un equipo, un producto, un cliente),
la respuesta NO puede ser solo un numero: tiene que decir QUE es cada uno.
Por ejemplo, si la fila es el serial SN-001, la respuesta tiene que decir
"SN-001" y algo que lo identifique (donde esta, de que modelo es).

El operador ve la tabla abajo de tu respuesta, pero la tabla es el
RESPALDO: tu texto es lo que se lee primero. Un "Se encontro 1 equipo" con
la tabla abajo deja al operador adivinando cual es; un "Se encontro SN-001,
un Dell Latitude 5520 en el Deposito Central" no deja nada que adivinar.

Si la consulta no encontro filas, decilo y SUGERI una pregunta relacionada que
si se pueda responder ("no hay equipos en esa ubicacion; puedo buscar los que
estan en transito").
"""


class PreguntaNoRespondida(RuntimeError):
    """La IA no pudo responder. El router la traduce a un error de servidor."""


class _ErrorDeModelo(Exception):
    """El modelo devolvio algo que no se puede usar. No se lo muestra al usuario crudo."""


def _resumir_turno(turno: dict[str, Any]) -> str:
    """
    El resumen de un turno anterior, en una linea.

    Es lo que el modelo necesita para DESAMBIGUAR ("ese equipo", "esos"): el
    tema de la pregunta y el de la consulta. El detalle de las filas no
    viaja: llena el contexto sin aportar nada para resolver la referencia.
    """
    partes = []
    if turno.get("pregunta"):
        partes.append(f"Pregunta: {turno['pregunta']}")
    if turno.get("sql"):
        partes.append(f"Consulta: {turno['sql']}")
    if turno.get("resumen"):
        partes.append(f"Resultado: {turno['resumen']}")
    return " | ".join(partes)


def _limpiar_respuesta(texto: str, es_agregado: bool) -> str:
    """
    Limpia la respuesta del modelo antes de mostrarla.

    QUE SACA
    ---------
    1. Las tablas Markdown: la pantalla YA muestra los datos en una tabla, y
       el modelo repeating la tabla en el texto deja la respuesta a medias
       entre la prosa y los datos.
    2. En un resultado agregado, todo lo que viene despues de la PRIMERA
       FRASE. El modelo escribia "Se encontro 1 equipo en el Deposito." y
       seguia con una lista inventada ("id: 1, serial: SN-001...") con datos
       que no estaban en el resultado. La primera frase es la que responde;
       lo demas es relleno y, en este caso, mentira.
    """
    if not texto:
        return texto

    lineas = []
    for linea in texto.split("\n"):
        # Una fila de tabla Markdown: empieza o termina con "|".
        if linea.strip().startswith("|") or linea.strip().endswith("|"):
            continue
        # La linea de separacion de una tabla ("---" o "|---|---|").
        if set(linea.strip()) <= set("|-: "):
            continue
        lineas.append(linea)

    limpio = "\n".join(lineas).strip()

    if es_agregado:
        # La primera FRASE. El corte es en cualquier puntuacion seguida de
        # espacio (o de salto de linea): el modelo separa las frases con un
        # "\n" a veces, y con un corte solo de ". " se colaba la segunda.
        import re as _re

        partes = _re.split(r"(?<=[.!?])\s+", limpio)
        primera = partes[0].strip()
        if not primera.endswith("."):
            primera += "."
        # Si la "primera frase" es larguisima, es que no habia punto: se deja
        # el texto entero (recortado) en vez de cortar a la mitad de una idea.
        if len(primera) > 300:
            return limpio
        return primera

    return limpio


def resumen_de_resultado(
    columnas: list[str],
    filas: list[dict[str, Any]],
    cantidad_filas: int,
) -> str:
    """
    Un resumen de UNA linea del resultado, para el historial.

    Es lo que viaja al siguiente turno: alcanza con el TEMA y los valores
    identificatorios (el serial, el SKU). El detalle completo de las filas no
    se manda: llena el contexto y no aporta nada para resolver un "ese
    equipo".
    """
    if not filas:
        return "sin filas"

    # Un agregado se describe como lo que es: "un unico dato: count = 1".
    # Si se describiera como "1 fila | count: 1", el modelo tomaba el "1" por
    # un id y lo metia como filtro en la consulta siguiente (la del detalle),
    # que devolvia cero filas y contradecia a la respuesta anterior.
    if len(columnas) == 1:
        # NO se escribe el valor: "count = 1" lo leia el modelo como un id y
        # lo metia de filtro en la consulta siguiente ("WHERE d.id = 1"), que
        # no devolvia filas y contradecia a la respuesta anterior. El numero
        # que hace falta para responder esta pregunta ya esta en la
        # CONSULTA anterior del historial.
        return "un total (la consulta cuenta, no trae filas de detalle)"

    partes = [f"{cantidad_filas} filas"]

    primera = filas[0]
    for columna in columnas[:4]:
        valor = primera.get(columna)
        if valor is None:
            continue
        texto = str(valor)
        if len(texto) > 40:
            texto = texto[:37] + "..."
        partes.append(f"{columna}: {texto}")

    return " | ".join(partes)


def responder_pregunta(
    pregunta: str,
    llm: LLMProvider | None = None,
    historial: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Responde una pregunta sobre el inventario.

    Devuelve el SQL generado, las filas y la explicacion en prosa. Las tres
    cosas se devuelven siempre: el SQL es lo que permite al operador verificar
    de un vistazo que el modelo no invento la fuente, que es la diferencia entre
    una herramienta confiable y una caja negra.

    EL HISTORIAL
    ------------
    Los turnos anteriores se mandan al modelo para que las preguntas de
    seguimiento ("cual es ese equipo que se encontro?") entiendan de que se
    trata. Sin esto, el modelo responde con lo que le parece y el operador
    recibe una respuesta que contradice a la anterior.
    """
    get_settings()
    llm = llm or get_llm()

    if not pregunta or not pregunta.strip():
        raise ValueError("La pregunta no puede ir vacia.")

    # -- Paso 0: la pregunta pide ESCRIBIR? ----------------------------
    #
    # Si pide escribir, no se genera SQL: se responde que el asistente es de
    # consultas y a donde ir. La alternativa (dejar que el modelo lo intente)
    # produce una consulta inventada que no contesta lo pedido: "Agrega un
    # producto" terminaba en un SELECT de productos de la categoria
    # "desarrollo" y en un "no hay datos".
    if intencion.detectar(pregunta).es_escritura:
        guia = intencion.respuesta_de_lectura(pregunta)
        return {
            "pregunta": pregunta,
            "sql": None,
            "columnas": [],
            "filas": [],
            "cantidad_filas": 0,
            "truncado": False,
            "respuesta": str(guia["respuesta"]),
            "sugerencia": guia.get("sugerencia"),
            "resumen": None,
            "modelo": None,
            "proveedor": llm.name,
            "usou_fallback": False,
            "error": None,
        }

    # -- Paso 1: el modelo escribe el SQL -------------------------------
    contexto_historial = ""
    if historial:
        bloques = []
        for i, turno in enumerate(historial[-6:], 1):
            if not isinstance(turno, dict):
                continue
            bloque = [f"{i}. Pregunta: {turno.get('pregunta', '')}"]
            if turno.get("sql"):
                bloque.append(f"   Consulta anterior: {turno['sql']}")
            if turno.get("resumen"):
                bloque.append(f"   Resultado: {turno['resumen']}")
            bloques.append("\n".join(bloque))

        if bloques:
            contexto_historial = (
                "HISTORIAL DE LA CONVERSACION (usalo para resolver referencias "
                "como 'ese', 'esos', 'el anterior'):\n"
                + "\n\n".join(bloques)
                + "\n\n"
            )

    prompt_sql = (
        f"{descripcion_esquema()}\n\n"
        f"{contexto_historial}"
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
    #
    # EL CASO AGREGADO
    # ----------------
    # Cuando la consulta devuelve UN SOLO DATO (un COUNT, un SUM, un AVG),
    # el contexto se dice de otra manera: "unico dato: count = 1". Con el
    # formato general (una lista de "- count: 1"), el modelo lo leia como el
    # principio de una fila y completaba el resto con lo que se imaginaba:
    # Contestaba "1 equipo: id 1, serial SN-001, Dell Latitude 5520" sin
    # tener NINGUNO de esos datos. La respuesta daba el numero bien y el
    # detalle inventado, que es peor: el operador creia que ese era el
    # equipo.
    es_agregado = len(resultado["columnas"]) == 1

    if es_agregado:
        valor = filas_para_explicar[0].get(resultado["columnas"][0]) if filas_para_explicar else None
        contexto = (
            f"Consulta ejecutada: {resultado.get('sql', sql)}\n"
            f"Total de filas: {resultado['cantidad_filas']}\n"
            f"Unico dato disponible: {resultado['columnas'][0]} = {valor}\n\n"
            "Este resultado tiene UN SOLO DATO. No hay ningun otro: no hay ids, "
            "no hay seriales, no hay nombres. Tu respuesta es una frase corta "
            "con ese dato."
        )
    else:
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
        respuesta_final = _limpiar_respuesta(explicacion.text, es_agregado)
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
        "sugerencia": None,
        "resumen": resumen_de_resultado(
            resultado["columnas"], resultado["filas"], resultado["cantidad_filas"]
        ),
        "modelo": modelo_usado,
        "proveedor": llm.name,
        "usou_fallback": uso_fallback,
        "error": None,
    }
