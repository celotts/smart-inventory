"""
Deteccion de INTENCION de la pregunta.

QUE HACE
--------
Antes de gastar una llamada al modelo, se mira si la pregunta pide ESCRIBIR
(agregar un producto, crear una incidencia, borrar un equipo) o solo
CONSULTAR.

POR QUE NO SE LO PREGUNTA AL MODELO
-----------------------------------
Porque la respuesta del modelo ante "Agrega un producto" fue escribir un
SELECT: el modelo no puede escribir (esta prohibido por el guard y por
Postgres), asi que se lo reinterpreto como "mostrame los productos de
desarrollo" y contesto que no habia ninguno. La pregunta del operador se
perdio en el camino: el "agrega" se leyo como un "agregados".

La deteccion es DETERMINISTICA (una lista de verbos), no una llamada al
modelo: es más rápida, más barata y no se equivoca con "agregar" vs
"agregados". Y el mensaje de respuesta es el mismo SIEMPRE, sin importar que
diga el modelo: "soy de consultas, para eso tenes la pantalla X".

QUE NO HACEMOS
--------------
No BLOQUEAMOS la pregunta: la pantalla de consultas sigue aceptandola. Lo
que cambia es la RESPUESTA: en vez de una consulta que no era lo que se
pregunto, se explica que el asistente es de solo lectura y a donde ir.

LA DIFERENCIA ENTRE "CONTAR" Y "CONTAR QUE HAY QUE"
--------------------------------------------------
"Cuantos equipos hay que reparar" es una CONSULTA: cuenta los que hay que
reparar. "Repara el equipo 3" es una ESCRITURA. Por eso los verbos se
buscan al PRINCIPIO de la frase (imperativo) o despues de un "podes"/
"necesito que": en el medio de la frase es casi siempre una pregunta.
"""

from __future__ import annotations

import re
import unicodedata

# Verbos de ESCRITURA, en imperativo y en las formas que se usan al hablar.
#
# Son verbos, no palabras: "agrega" escribe, "agregado" cuenta. Por eso
# los patrones terminan en el final de palabra y no en "agrega*".
VERBOS_ESCRITURA = (
    r"agrega|agrega|agregas|agregas|agregale|agregales|agreguen|agrega|agregar",
    r"crea|crea|creas|crea|creale|crear|creemos|creo que agregues",
    r"carga|carga|cargas|cargas|cargale|cargar",
    r"registra|registra|registras|registras|registrale|registrar",
    r"elimina|elimina|eliminas|eliminas|eliminale|eliminar",
    r"borra|borra|borras|borras|borrale|borrar",
    r"actualiza|actualiza|actualizas|actualizas|actualizar",
    r"modifica|modifica|modificas|modificas|modificar",
    r"cambia|cambia|cambias|cambias|cambiale|cambiar",
    r"asigna|asigna|asignas|asignas|asignale|asignar",
    r"transfieri|transferi|transferir|transferila",
    r"mueve|move|movi|mover|movilo",
    r"ingresa|ingresa|ingresar|registra el ingreso",
    r"marca|marca|marca como|marcar",
)

# Frases de ESCRITURA que no son un verbo solo.
FRASES_ESCRITURA = (
    r"da de baja|dar de baja|dame de baja|darlo de baja",
    r"pone|poner|pon|poner en|ponerlo",
    r"saca|sacar|sacalo|quitar|quitalo|eliminar del",
    r"pasa|pasar|pasarlo",
)

# Verbos de escritura Después de un pedido ("podés agregar X", "necesito que
# cargues X"). Solo se aceptan con está forma: en el medio de una frase, un
# verbo así casi siempre es una pregunta ("cuantos equipos hay que reparar").
PEDIDOS_ESCRITURA = (
    r"(?:podes|podes|podrias|podrias|queres|queres|necesito|necesitamos|"
    r"quisiera|quisiste|quisieron|intenta|intenta|ayudame|ayudame a|"
    r"quedaria bien|seria bueno|seria ideal|bueno seria|might as well)\s+"
    r"(?:a\s+|que\s+|a que\s+)?"
    # Las tres formas del verbo: infinitive ("agregar"), imperativo ("agrega")
    # y subjuntivo ("agregues", el de "necesito que cargues").
    r"(?:agregar|agrega|agregues|agregas|crear|crea|crees|creas|"
    r"cargar|carga|cargues|cargas|registrar|registra|registres|registras|"
    r"eliminar|elimina|elmines|eliminas|borrar|borra|borres|borras|"
    r"actualizar|actualiza|actualices|actualizas|modificar|modifica|"
    r"modifiques|modificas|cambiar|cambia|cambies|cambias|"
    r"asignar|asigna|asignes|asignas|transferir|transferi|transfieras|"
    r"mover|move|movas|movas|ingresar|ingresa|ingreses|ingresas|"
    r"dar de baja|darlo de baja|dar)"
)

# A donde mandan al operador, según de que se trata.
#
# La clave de la vista es la de `navegacion.js` (`VISTAS`): en minúscula y
# sin acentos. Si no coincide con una clave de ese registro, el botón de la
# sugerencia no lleva a ningún lado, y el operador hace clic y no pasa nada.
DESTINOS_POR_TEMA: tuple[tuple[str, str, str], ...] = (
    (
        r"producto|modelo|catalogo|catalogo|sku",
        "productos",
        "anda a Productos y usa el boton «Nuevo producto»",
    ),
    (
        r"incidencia|caso|ticket|reporte de falla",
        "incidencias",
        "anda a Incidencias y usa «Reportar incidencia»",
    ),
    (
        r"equipo|dispositivo|componente|serial",
        "inventario",
        "anda a Inventario y usa «Escanear» o la ficha del equipo",
    ),
    (
        r"usuario|empleado|tecnico|admin",
        "usuarios",
        "anda a Usuarios y usa «Nuevo usuario»",
    ),
    (
        r"compra|orden de compra|proveedor",
        "compras",
        "anda a Compras y usa «Nueva orden de compra»",
    ),
)


# El nombre que se muestra de cada vista. La clave es la de `navegacion.js`.
ETIQUETAS_DE_VISTA = {
    "productos": "Productos",
    "incidencias": "Incidencias",
    "inventario": "Inventario",
    "usuarios": "Usuarios",
    "compras": "Compras",
    "escanear": "Escanear",
    "nuevo": "Nuevo equipo",
}


def _normalizar(texto: str) -> str:
    """Minusculas y sin acentos: para comparar, no para mostrar."""
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


class Intencion:
    """
    Lo que se detecto de la pregunta.

    `es_escritura` True significa que hay que responder con la guia, no con
    una consulta. `destino` y `guia` son la pantalla y el texto concretos.
    """

    def __init__(self, es_escritura: bool, destino: str | None = None, guia: str | None = None):
        self.es_escritura = es_escritura
        self.destino = destino
        self.guia = guia

    def __bool__(self) -> bool:
        return self.es_escritura


def _destino_para(pregunta: str) -> tuple[str | None, str | None]:
    """La pantalla y la guia que corresponden al tema de la pregunta."""
    normalizada = _normalizar(pregunta)
    for patron, vista, guia in DESTINOS_POR_TEMA:
        if re.search(patron, normalizada):
            return vista, guia
    return None, None


def detectar(pregunta: str) -> Intencion:
    """
    Dice si la pregunta pide escribir algo.

    Returns:
        Una `Intencion`. `es_escritura` es False para la pregunta normal.
    """
    if not pregunta or not pregunta.strip():
        return Intencion(False)

    normalizada = _normalizar(pregunta.strip())

    # Se descartan Solo los saludos y las marcas de cortesia: "hola, podés
    # agregar un producto" tiene que detectarse igual que "agrega un
    # producto".
    #
    # "necesito", "podés" y "quisiera" NO se quitan: son la marca de la forma
    # "necesito que cargues", que se reconoce más abajo. Si se quitaran, la
    # pregunta se volveria "que cargues un producto" y no se detectaria.
    limpio = re.sub(
        r"^(hola|buenas|buen dia|buen dias|che|oye|por favor|qfis|disculpe|gracias)\b[ ,]*",
        "",
        normalizada,
    )

    patron_verbos = "|".join(VERBOS_ESCRITURA)
    patron_frases = "|".join(FRASES_ESCRITURA)

    # 1. EL VERBO AL PRINCIPIO: es un imperative ("Agrega un producto").
    if re.match(rf"^({patron_verbos}|{patron_frases})\b", limpio):
        destino, guia = _destino_para(pregunta)
        return Intencion(True, destino, guia)

    # 2. EL VERBO Después DE UN PEDIDO: ("Podés agregar un producto").
    if re.search(PEDIDOS_ESCRITURA, limpio):
        destino, guia = _destino_para(pregunta)
        return Intencion(True, destino, guia)

    return Intencion(False)


def respuesta_de_lectura(pregunta: str) -> dict[str, object]:
    """
    La respuesta cuando la pregunta pide escribir.

    Es una respuesta FIJA: no la redacta el modelo. Si la redactara el
    modelo, podria inventar que ya lo hizo ("Listo, agregue el producto"),
    que es la peor respuesta posible: el operador cree que el producto
    existe y no existe.
    """
    intencion = detectar(pregunta)

    partes = [
        "Soy un asistente de consultas: puedo contarte y mostrarte los datos "
        "reales del inventario, pero no puedo crear ni modificar nada."
    ]

    if intencion.guia:
        partes.append(f"Para eso, {intencion.guia}.")

    partes.append(
        "Si lo que queres es ver si ya existe, decime «busca X» y lo busco."
    )

    return {
        "respuesta": " ".join(partes),
        "sugerencia": (
            {
                "texto": "Ir a " + ETIQUETAS_DE_VISTA.get(intencion.destino, intencion.destino),
                "vista": intencion.destino,
            }
            if intencion.destino
            else None
        ),
    }
