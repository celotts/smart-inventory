"""
Modelos de request y response de la API de IA.

POR QUE ESTOS MODELOS NO EXPONEN EL SQL CRUDO AL CONSUMIDOR POR DEFECTO
-----------------------------------------------------------------------
Un endpoint que devuelve filas crudas obliga al frontend a saber de antemano
que forma tienen. Estos modelos separan las tres cosas que el cliente realmente
necesita:

  `respuesta`   texto para mostrar a la persona
  `sql`         la consulta, para que pueda verificarla
  `datos`       filas estructuradas, por si quiere graficar

Un cliente que no quiere el SQL simplemente no lo lee. Va incluido porque
ocultarlo es peor: si el modelo se equivoca, la unica forma de detectarlo es ver
que consulta produjo el numero.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TurnoConversacion(BaseModel):
    """
    Un turno anterior de la conversacion.

    SIN MEMORIA NO HAY PREGUNTAS DE SEGUIMIENTO
    --------------------------------------------
    "Cuantos equipos hay en deposito?" se responde sola. "Cual es ese equipo
    que se encontro?" NO: sin contexto, el modelo no tiene idea de a que
    equipo se refiere y escribe la consulta que le parece (una consulta por
    equipos INSTALADOS, que es otra cosa, y el operador recibe una respuesta
    que contradice a la anterior).

    Con el historial, el modelo ve que la pregunta anterior contaba los
    equipos en deposito, y escribe la consulta de DETALLE de ese mismo
    conjunto.

    Se mandan solo tres cosas de cada turno: la pregunta, la consulta que se
    ejecuto y un resumen corto del resultado. El detalle de las filas no
    viaja: el contexto se llena rapido y lo que hace falta para desambiguar
    es el TEMA, no cada dato.
    """

    model_config = ConfigDict(extra="forbid")

    pregunta: str = Field(..., max_length=500)
    sql: str | None = Field(None, max_length=4000)
    resumen: str | None = Field(None, max_length=2000)


class Sugerencia(BaseModel):
    """Una accion que el operador puede hacer desde la respuesta."""

    texto: str = Field(..., max_length=80)
    vista: str | None = Field(None, max_length=40)


class PreguntaRequest(BaseModel):
    pregunta: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Pregunta en lenguaje natural sobre el inventario.",
        examples=["¿Cuántos equipos hay en el almacén y cuántos en reparación?"],
    )
    # Cuantas filas volver. El default es generoso a proposito (son conteos y
    # distribuciones, no detalle), pero un cliente que quiere una tabla chica
    # puede pedir menos y no pagar el resto de la serializacion.
    max_filas: int = Field(default=100, ge=1, le=500)
    incluir_sql: bool = Field(
        default=True,
        description="Incluir la consulta generada, para poder verificarla.",
    )
    # Los turnos anteriores, para que las preguntas de seguimiento ("ese
    # equipo", "los de ayer") se entiendan.
    historial: list[TurnoConversacion] = Field(
        default_factory=list,
        max_length=10,
        description="Turnos anteriores de la conversacion.",
    )


class RespuestaIA(BaseModel):
    pregunta: str | None = None
    respuesta: str = Field(..., description="Explicación en lenguaje natural.")
    sql: str | None = Field(None, description="Consulta SQL que se ejecutó.")
    datos: list[dict[str, Any]] = Field(default_factory=list)
    columnas: list[str] = Field(default_factory=list)
    cantidad_filas: int = 0
    truncado: bool = False
    modelo: str | None = None
    proveedor: str | None = None
    uso_fallback: bool = False
    latencia_ms: int | None = None
    error: str | None = None
    # La accion sugerida, cuando la pregunta pedia escribir: el asistente es
    # de consultas y le dice al operador donde hacer lo que pidio.
    sugerencia: Sugerencia | None = None
    # El resumen del resultado, en una linea. El front lo guarda y lo manda
    # de vuelta en la pregunta siguiente: es lo que permite que "cual es ese
    # equipo que se encontro?" entienda de que equipo se trata.
    resumen: str | None = None


class AnalisisDispositivoRequest(BaseModel):
    device_id: int = Field(..., gt=0)
    # Si viene False, no se manda el historial de reparaciones al modelo: solo
    # las cifras agregadas. Es el modo por defecto porque el texto de los
    # diagnosticos es el unico dato de este endpoint que no es un numero.
    incluir_diagnosticos: bool = True


class AnalisisDispositivoResponse(BaseModel):
    device_id: int
    serial_number: str
    estado_actual: str
    # Lo que el modelo concluYO, en categorias cerradas.
    clasificacion: str = Field(
        ...,
        description="Una de:excelente, bueno,_regular,malo,urgente,critico.",
    )
    puntaje_riesgo: int = Field(
        ..., ge=0, le=100, description="0 = sin riesgo, 100 = crítico."
    )
    nivel_riesgo: str = Field(..., description="bajo, medio, alto, critico")
    confianza: str = Field(..., description="baja, media, alta")
    razonamiento: str = Field(..., description="Por qué, en dos o tres frases.")
    factores: list[str] = Field(
        default_factory=list, description="Señales concretas que motivaron el puntaje."
    )
    recomendaciones: list[str] = Field(default_factory=list)
    # Las cifras que se le dieron al modelo, para que se puedan auditar.
    metricas: dict[str, Any] = Field(default_factory=dict)
    modelo: str | None = None
    proveedor: str | None = None
    latencia_ms: int | None = None


class ReporteRequest(BaseModel):
    # Sin esto, el reporte es de "todo el inventario", que no significa nada.
    # Con esto, el prompt se arma sobre un recorte concreto y el modelo tiene
    # que responder sobre ese recorte.
    alcance: str | None = Field(
        default=None,
        max_length=300,
        description="Restricción natural: un cliente, un período, un estado.",
        examples=["Solo la sede de Córdoba", "Equipos con más de 2 años instalados"],
    )
    periodo: str = Field(
        default="mes_actual",
        description="mes_actual | ultimo_trimestre | anio_actual | todo",
    )
    tipo: str = Field(default="resumen", description="resumen | operativo | financiero")
    incluir_sql: bool = True


class ReporteResponse(BaseModel):
    tipo: str
    alcance: str | None
    periodo: str
    reporte: str = Field(..., description="El reporte en texto, listo para mostrar.")
    sql: str | None = None
    datos: list[dict[str, Any]] = Field(default_factory=list)
    columnas: list[str] = Field(default_factory=list)
    modelo: str | None = None
    proveedor: str | None = None
    latencia_ms: int | None = None
    error: str | None = None


class EstadoIA(BaseModel):
    """Estado del proveedor. NUNCA lanza: reportar es la respuesta."""

    disponible: bool
    proveedor: str
    modelo_configurado: str
    modelo_fallback: str | None = None
    # False si se cayo y no habia con que reintentar. Un reporte de "no puedo
    # consultar" es informacion; un 500 no lo es.
    respondio: bool
    detalle: str | None = None
    latencia_ms: int | None = None
    # Si el host es local o remoto. No es un detalle: es lo que hay que mirar
    # primero cuando la IA funciona en la laptop y no en produccion.
    host_remoto: str | None = None
    modelos_disponibles: list[str] = Field(default_factory=list)
