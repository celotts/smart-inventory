"""
Los agentes del sistema.

QUE ES UN AGENTE
----------------
Un agente es un ENFOQUE, no un modelo: el mismo modelo, con otro prompt y
con otras herramientas a disposicion.

    El de inventario responde "donde esta cada equipo" con el foco en el
    stock y los movimientos.
    El de mantenimiento responde "que se esta rompiendo" con el foco en las
    reparaciones abiertas.
    El de compras responde "que hay que comprar" con el foco en las ordenes y
    el minimo de stock.

Todos comparten el motor (`sql_agent`), el guard de SQL y la auditoria. Lo
que cambia es la lente.

POR QUE NO UN MODELO POR AGENTE
-------------------------------
Porque no hace falta y sale caro: un modelo mas chico por agente es un
modelo mas chico que descargar, que servir y que mantener, y para cambiar
de agente bastan unas lineas de prompt.

El multi-agente de verdad (varios modelos, coordinacion entre ellos, memoria
compartida) tiene sentido cuando las tareas son de VERDAD distintas: un
modelo que razona sobre números y otro que redacta. Aca no: la tarea es
siempre la misma ("contame esto del inventario"), lo que cambia es de qué
parte del inventario mira.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Agente:
    """
    Un agente: su nombre, su foco y sus herramientas.

    `contexto` es el texto que se le suma al prompt del sistema: le dice al
    modelo QUE tiene que estar mirando y QUE tiene que responder. Sin esto,
    el agente es un alias sin diferencia.

    `palabras` son las palabras que lo seleccionan. El score es la cantidad
    de palabras que aparecen en la pregunta: gana el que mas tiene. El
    modulo en el que esta trabajando el operador pesa como un desempate: si
    pregunta desde el Service Desk, el agente de Service Desk gana.
    """

    clave: str
    nombre: str
    descripcion: str
    contexto: str
    herramientas: tuple[str, ...]
    palabras: tuple[str, ...]
    # El modulo del front al que pertenece. `None` es "de los dos".
    modulos: tuple[str, ...] = field(default=())

    def puntaje(self, pregunta: str, modulo: str | None = None) -> int:
        """
        Que tan pertinente es este agente para esta pregunta.

        Se cuenta cuantas palabras suyas aparecen. Y si el operador esta
        trabajando en un modulo, y este agente es de ese modulo, se le suma
        un punto: es la informacion de "donde esta mirando" el operador.
        """
        from src.modules.ai.intencion import _normalizar

        normalizada = _normalizar(pregunta)
        puntos = sum(1 for palabra in self.palabras if palabra in normalizada)
        if modulo and modulo in self.modulos:
            puntos += 1
        return puntos


# ---------------------------------------------------------------------------
# Los agentes
# ---------------------------------------------------------------------------

INVENTARIO = Agente(
    clave="inventario",
    nombre="Agente de Inventario",
    descripcion="Equipos, stock y movimientos",
    contexto=(
        "FOCUS: DONDE ESTA CADA EQUIPO Y CUANTO VALE.\n\n"
        "Las preguntas tipicas son: cuanto equipo hay, donde esta, cuanto vale "
        "la flota, que hay que reponer, que se movio hoy.\n\n"
        "Herramientas: stock_bajo, por_reponer, resumen_de_stock.\n"
        "Para el VALOR de la flota usa valor_de_la_flota."
    ),
    herramientas=("stock_bajo", "por_reponer", "resumen_de_stock", "valor_de_la_flota"),
    palabras=(
        "equipo", "equipos", "dispositivo", "dispositivos", "componente", "componentes",
        "inventario", "stock", "existencias", "existencia", "almacen", "deposito",
        "movimiento", "movimientos", "serial", "ubicacion", "ubicaciones", "flota",
        "valor", "libro", "reponer", "minimo",
    ),
    modulos=("inventario",),
)

MANTENIMIENTO = Agente(
    clave="mantenimiento",
    nombre="Agente de Mantenimiento",
    descripcion="Reparaciones, taller y riesgo",
    contexto=(
        "FOCUS: QUE SE ROMPE Y QUE ESTA EN EL TALLER.\n\n"
        "Las preguntas tipicas son: que reparaciones hay abiertas, cuanto tiempo "
        "lleva un equipo en el taller, cuanto costo, que se rompe mas.\n\n"
        "Herramientas: reparaciones_abiertas.\n"
        "Para el CONTEXTO de un equipo (garantia, ultimo servicio) consulta la "
        "tabla maintenance y el analisis de riesgo del equipo."
    ),
    herramientas=("reparaciones_abiertas",),
    palabras=(
        "reparacion", "reparaciones", "taller", "mantenimiento", "mantenimientos",
        "roto", "rota", "rompiendose", "falla", "fallando", "averia", "averias",
        "costo", "costos", "riesgo", "garantia", "plan", "preventivo",
    ),
    modulos=("helpdesk",),
)

COMPRAS = Agente(
    clave="compras",
    nombre="Agente de Compras",
    descripcion="Ordenes, proveedores y reposición",
    contexto=(
        "FOCUS: QUE HAY QUE COMPRAR Y CUANDO.\n\n"
        "Las preguntas tipicas son: que ordenes hay abiertas, que proveedor "
        "trae que, cuanto falta para recibir, que se esta por reponer.\n\n"
        "Herramientas: ordenes_pendientes, sugerir_reorden, stock_bajo.\n"
        "Una orden esta PENDIENTE si alguna de sus lineas tiene pendiente > 0."
    ),
    herramientas=("ordenes_pendientes", "sugerir_reorden", "stock_bajo"),
    palabras=(
        "compra", "compras", "orden", "ordenes", "proveedor", "proveedores",
        "recepcion", "recibir", "reabastecer", "stock", "pedido", "pedidos",
    ),
    modulos=("inventario",),
)

REPORTES = Agente(
    clave="reportes",
    nombre="Agente de Reportes",
    descripcion="Cifras, indicadores y SLA",
    contexto=(
        "FOCUS: LAS CIFRAS Y LOS INDICADORES.\n\n"
        "Las preguntas tipicas son: cuanto vale la flota, cual es el SLA de "
        "este caso, cuantos casos hay vencidos, cual es la rotacion, cual es "
        "la tasa de cierre.\n\n"
        "Para los datos del SLA consulta incident_sla_policies y los casos de "
        "incidents. Para la rotacion, la tabla movements."
    ),
    herramientas=("valor_de_la_flota", "reparaciones_abiertas"),
    palabras=(
        "reporte", "reportes", "informe", "informes", "cifra", "cifras", "total",
        "totales", "cuanto vale", "sla", "indicador", "indicadores", "rotacion",
        "estadistica", "estadisticas", "kpi", "cierre", "vencidos", "vencimiento",
    ),
    modulos=(),
)

SERVICE_DESK = Agente(
    clave="servicedesk",
    nombre="Agente de Service Desk",
    descripcion="Casos, SLA y resolución",
    contexto=(
        "FOCUS: LOS CASOS Y LOS PLAZOS.\n\n"
        "Las preguntas tipicas son: que casos tengo, cuales se vencen, quien "
        "tomo cada caso, cuanto tarda un caso en cerrarse, que prioridad "
        "tiene.\n\n"
        "El SLA de cada caso sale de incident_sla_policies SEGUN su severidad: "
        "no hay un SLA unico, hay uno por severidad y por horario laboral. Los "
        "casos vencidos son los que ya pasaron su plazo."
    ),
    herramientas=("reparaciones_abiertas",),
    palabras=(
        "incidencia", "incidencias", "caso", "casos", "ticket", "tickets",
        "sla", "vencido", "vencida", "vencidos", "severidad", "prioridad",
        "asignado", "asignada", "resolver", "resolucion", "ayuda", "cliente",
    ),
    modulos=("helpdesk",),
)

# Todos los agentes, en el orden en que se evaluan.
AGENTES: tuple[Agente, ...] = (
    INVENTARIO,
    MANTENIMIENTO,
    COMPRAS,
    SERVICE_DESK,
    REPORTES,
)

AGENTES_POR_CLAVE: dict[str, Agente] = {a.clave: a for a in AGENTES}


def por_clave(clave: str) -> Agente:
    """El agente de esa clave. Lanza `KeyError` si no existe."""
    return AGENTES_POR_CLAVE[clave]


def elegir(pregunta: str, modulo: str | None = None) -> Agente:
    """
    El agente que mejor corresponde a la pregunta.

    EL DESEMPATE
    ------------
    Si ningun agente tiene palabras de la pregunta, gana el del MODULO en el
    que el operador esta trabajando: si esta en el Service Desk preguntando
    "y el que se rompio?", el agente de Service Desk es el que tiene que
    responder, aunque no haya una sola palabra clave.

    Y si tampoco hay modulo (una consulta desde la API), gana el de
    inventario: es el nucleo del sistema.
    """
    puntuaciones = [(a.puntaje(pregunta, modulo), a) for a in AGENTES]
    puntuaciones.sort(key=lambda par: par[0], reverse=True)

    mejor_puntaje, mejor = puntuaciones[0]
    if mejor_puntaje > 0:
        return mejor

    # Sin palabras: el modulo.
    if modulo:
        for agente in AGENTES:
            if modulo in agente.modulos:
                return agente

    return INVENTARIO
