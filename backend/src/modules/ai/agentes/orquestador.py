"""
El orquestrador: elige el agente, le da sus herramientas y responde.

COMO TRABAJA
------------
1. La intencion detecta si la pregunta pide ESCRIBIR. Si pide escribir, no
   hay agente: se responde con la guia de a donde ir (lo que ya hace el
   `sql_agent`).

2. Se elige el agente: por las palabras de la pregunta y, a falta de
   palabras, por el modulo en el que esta trabajando el operador.

3. Se eligen las HERRAMIENTAS del agente que aplican a esa pregunta (no
   todas: correrlas todas es caro y llena el contexto de datos que no
   Asked), y se corre. Sus resultados van en el prompt como "ESTOS SON LOS
   DATOS". El modelo no los inventa: estan ahi.

4. El modelo responde con esos datos. Si la pregunta necesita algo que las
   herramientas no traen, escribe su SQL y lo pasa por el guard, como
   siempre.

LO QUE NO HACE
--------------
- No ejecuta SQL libre fuera del guard.
- No escribe en la base: todo es lectura.
- No elige herramientas que el agente no declara.

POR QUE ES UN ORQUESTADOR Y NO UN "AGENTE DE AGENTES"
-------------------------------------------------------
Porque el reparto no lo hace el modelo: lo hace el codigo. Un modelo que
decide a que agente llamar es un modelo que puede decidir llamar al
agente equivocado, y el resultado es una respuesta sobre otra cosa. Con el
reparto en codigo, el agente es siempre el que corresponde, y se puede
auditar cual fue.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.llm.base import LLMProvider
from src.core.llm.factory import get_llm
from src.modules.ai import intencion
from src.modules.ai.agentes import agentes
from src.modules.ai.agentes import herramientas
from src.modules.ai.sql_agent import PreguntaNoRespondida, responder_pregunta


# El limite de filas que se mandan al modelo en el contexto de una
# herramienta. Con mas, la respuesta se entera de la mitad de la base y
# la pregunta pierde el foco.
MAX_FILAS_POR_HERRAMIENTA = 25

# Que herramienta se consulta para que palabras. La primera es la que se
# usa cuando ninguna coincide (la principal del agente).
PALABRAS_POR_HERRAMIENTA: dict[str, tuple[str, ...]] = {
    "stock_bajo": ("bajo", "minimo", "faltante", "reponer", "reposicion", "faltan"),
    "por_reponer": ("reponer", "reponer", "comprar", "pedido", "faltante"),
    "resumen_de_stock": ("resumen", "stock", "cuanto hay", "existencia", "existencias"),
    "reparaciones_abiertas": ("reparacion", "reparaciones", "taller", "roto", "falla", "averia"),
    "ordenes_pendientes": ("orden", "ordenes", "compra", "compras", "pedido", "recibir"),
    "sugerir_reorden": ("sugerir", "sugerencia", "reorden", "comprar", "pedido"),
    "valor_de_la_flota": ("valor", "flota", "cuanto vale", "libro", "depreciacion", "amortiz"),
}


def _serializar(datos: Any) -> str:
    """Los datos de una herramienta, como texto para el prompt."""
    if isinstance(datos, list):
        filas = datos[:MAX_FILAS_POR_HERRAMIENTA]
        if not filas:
            return "(sin resultados)"
        cuerpo = json.dumps(filas, ensure_ascii=False, default=str, indent=1)
        extra = ""
        if len(datos) > MAX_FILAS_POR_HERRAMIENTA:
            extra = f"\n(se muestran {len(filas)} de {len(datos)} filas)"
        return cuerpo + extra
    return json.dumps(datos, ensure_ascii=False, default=str, indent=1)


def _herramientas_relevantes(agente: agentes.Agente, pregunta: str) -> list[str]:
    """
    Las herramientas del agente que aplican a esta pregunta.

    POR QUE NO CORRER LAS DEL AGENTE
    ---------------------------------
    Correr todas es caro (el valor de la flota es un JOIN sobre todos los
    equipos) y llena el contexto con datos que la pregunta no pidio: el
    modelo los ve, se distrae, y a veces responde con lo que le يبدو
    interesante en vez de lo que se pregunto.

    Se quedan dos como maximo: la que mas coincide con las palabras de la
    pregunta y, si no hay ninguna, la principal del agente.
    """
    from src.modules.ai.intencion import _normalizar

    normalizada = _normalizar(pregunta)

    puntuadas: list[tuple[int, str]] = []
    for nombre in agente.herramientas:
        palabras = PALABRAS_POR_HERRAMIENTA.get(nombre, ())
        puntos = sum(1 for palabra in palabras if _normalizar(palabra) in normalizada)
        if puntos:
            puntuadas.append((puntos, nombre))

    if not puntuadas:
        return list(agente.herramientas[:1])

    puntuadas.sort(key=lambda par: par[0], reverse=True)
    return [nombre for _, nombre in puntuadas[:2]]


def _contexto_de_herramientas(
    agente: agentes.Agente, pregunta: str
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """
    Corre las herramientas que aplican y arma el bloque de contexto.

    UNA HERRAMIENTA QUE FALLA NO ROMPE LA RESPUESTA
    -------------------------------------------------
    Si una herramienta falla (una tabla que todavia no existe, un dato con
    una forma distinta), la respuesta sigue: el modelo tiene el SQL y puede
    consultar lo que le falte. Un error en una herramienta no puede dejar al
    operador sin respuesta.

    EL NOMBRE DE LA HERRAMIENTA NO ES UNA TABLA
    -------------------------------------------
    El contexto dice "RESULTADO DE LA CONSULTA «stock_bajo»", y no
    "stock_bajo:". Con la otra forma, el modelo leia el nombre de la
    herramienta como el nombre de una tabla y escribia
    `SELECT * FROM stock_bajo`, que el guard rechazaba ("la tabla
    `stock_bajo` no se puede consultar"): el modelo estaba obligado a
    inventar una tabla porque el contexto se la habia presentedo como una.
    """
    nombres = _herramientas_relevantes(agente, pregunta)
    bloques = []
    filas: list[dict[str, Any]] = []
    for nombre in nombres:
        try:
            datos = herramientas.ejecutar(nombre)
        except Exception as exc:  # noqa: BLE001 - una herramienta no puede voltear la respuesta
            bloques.append(
                f'RESULTADO DE LA CONSULTA «{nombre}» (no se pudo consultar: {exc})'
            )
            continue
        bloques.append(
            f"RESULTADO DE LA CONSULTA «{nombre}»:\n{_serializar(datos)}"
        )
        if isinstance(datos, list) and datos:
            filas.extend(datos[:MAX_FILAS_POR_HERRAMIENTA])

    if not bloques:
        return "", nombres, []

    contexto = (
        "ESTOS SON LOS DATOS REALES DE LA CONSULTA (no los calcules ni los "
        "inventes; usalos tal cual):\n\n"
        + "\n\n".join(bloques)
        + "\n\n"
        "Si estos datos YA contestan la pregunta, usalos y no escribas SQL. "
        "Solo consulta la base para lo que NO esta aca. Y usas UNICAMENTE las "
        "columnas que aparecen en el esquema: una columna que no esta ahi no "
        "existe, y la consulta no arranca.\n\n"
    )
    return contexto, nombres, filas


def _redactar_con_datos(
    pregunta: str,
    agente: agentes.Agente,
    datos: list[dict[str, Any]],
    llm: LLMProvider,
) -> str:
    """
    La prosa, hecha SOLO con los datos de la herramienta.

    POR QUE NO SE LE PIDE SQL
    ------------------------
    Un modelo chico que ve el esquema escribe SQL aunque los datos que
    tiene ya contesten la pregunta. Y lo escribe mal: se inventa columnas
    que no existen (`repairs.deleted_at`, que no existe; todas las demas
    tablas lo tienen, asi que el modelo lo da por hecho) y la consulta no
    arranca.

    Aca el modelo no ve el esquema: solo ve la tabla de datos y la
    pregunta. No puede escribir SQL porque no se lo ofrecen, y no puede
    inventar columnas porque los nombres vienen de los datos.

    Y si la herramienta NO trae datos, el camino es el otro: el del
    `sql_agent`, con esquema y guard.
    """
    from src.modules.ai.sql_agent import SYSTEM_RESPUESTA, _limpiar_respuesta
    from src.modules.ai.table_format import formatear_tabla

    if not datos:
        return ""

    columnas = list(datos[0].keys())
    cuerpo = formatear_tabla(columnas, datos)

    prompt = (
        f"Pregunta: {pregunta}\n\n"
        f"Estos son los datos:\n\n{cuerpo}\n\n"
        "Explicame el resultado en una o dos frases. "
        "Los valores van en el orden de COLUMNAS: no los reasignes."
    )

    try:
        explicacion = llm.chat(prompt, system=SYSTEM_RESPUESTA)
        return _limpiar_respuesta(explicacion.text, len(columnas) == 1)
    except Exception:  # noqa: BLE001 - sin glosa, los datos son la respuesta
        return (
            f"La consulta del {agente.nombre} devvio {len(datos)} resultado(s). "
            "Los datos estan en la tabla de arriba."
        )


def consultar(
    pregunta: str,
    modulo: str | None = None,
    llm: LLMProvider | None = None,
    historial: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Responde una pregunta con el agente que corresponde.

    Devuelve lo mismo que `sql_agent.responder_pregunta`, con dos campos
    extra:

      - `agente`: la clave del agente que respondio.
      - `agente_nombre`: su nombre, para mostrarlo en la pantalla.

    Y con las herramientas ya aplicadas: el `contexto` que se le paso al
    modelo, para que se pueda ver de donde salio la respuesta.
    """
    # -- 1. ¿Pide escribir? ------------------------------------------------
    #
    # No hay agente para eso: el asistente es de consultas. Se responde con
    # la guia y se sale.
    if intencion.detectar(pregunta).es_escritura:
        guia = intencion.respuesta_de_lectura(pregunta)
        return {
            "pregunta": pregunta,
            "agente": None,
            "agente_nombre": None,
            "sql": None,
            "columnas": [],
            "filas": [],
            "cantidad_filas": 0,
            "truncado": False,
            "respuesta": str(guia["respuesta"]),
            "sugerencia": guia.get("sugerencia"),
            "resumen": None,
            "modelo": None,
            "contexto": None,
            "herramientas": [],
            "error": None,
        }

    # -- 2. Que agente responde ------------------------------------------
    agente = agentes.elegir(pregunta, modulo)

    # -- 3. Sus herramientas ----------------------------------------------
    contexto, usadas, datos_de_herramienta = _contexto_de_herramientas(agente, pregunta)

    # -- 4. Si la herramienta YA contesto, no se escribe SQL -------------
    #
    # Es el camino corto y el que mas se usa: la herramienta consulto los
    # datos y el modelo solo los redacta. Sin esquema delante, el modelo no
    # puede inventar una columna que no existe.
    #
    # Y si la herramienta NO trajo nada (no hay stock bajo, no hay
    # reparaciones abiertas), se sigue por el camino largo: el del
    # `sql_agent`, con su SQL y su guard.
    llm = llm or get_llm()
    if datos_de_herramienta:
        respuesta = _redactar_con_datos(pregunta, agente, datos_de_herramienta, llm)
        return {
            "pregunta": pregunta,
            "agente": agente.clave,
            "agente_nombre": agente.nombre,
            "sql": None,
            "columnas": list(datos_de_herramienta[0].keys()) if datos_de_herramienta else [],
            "filas": datos_de_herramienta,
            "cantidad_filas": len(datos_de_herramienta),
            "truncado": False,
            "respuesta": respuesta,
            "sugerencia": None,
            "resumen": None,
            "modelo": None,
            "contexto": contexto,
            "herramientas": usadas,
            "error": None,
        }

    # -- 5. La pregunta, con el foco del agente --------------------------
    #
    # La pregunta se le pasa al `sql_agent` con el foco del agente delante.
    # Es la misma pregunta, con el mismo motor y el mismo guard: lo que
    # cambia es el lente con el que se mira.
    pregunta_con_enfoque = (
        f"{pregunta}\n\n"
        f"CONTEXTO DE LA CONSULTA (el agente es {agente.nombre}):\n"
        f"{agente.contexto}\n\n"
        f"{contexto}"
    )

    try:
        respuesta = responder_pregunta(
            pregunta_con_enfoque,
            llm=llm,
            historial=historial,
        )
    except PreguntaNoRespondida as exc:
        return {
            "pregunta": pregunta,
            "agente": agente.clave,
            "agente_nombre": agente.nombre,
            "sql": None,
            "columnas": [],
            "filas": [],
            "cantidad_filas": 0,
            "truncado": False,
            "respuesta": "",
            "sugerencia": None,
            "resumen": None,
            "modelo": None,
            "contexto": contexto,
            "herramientas": usadas,
            "error": str(exc),
        }

    respuesta["agente"] = agente.clave
    respuesta["agente_nombre"] = agente.nombre
    respuesta["contexto"] = contexto
    respuesta["herramientas"] = usadas
    return respuesta
