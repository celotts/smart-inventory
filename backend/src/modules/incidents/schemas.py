"""
Schemas del módulo de incidencias.

POR QUE VALIDAR CON PYDANTIC Y NO EN EL SERVICE
------------------------------------------------
El `title` con dos letras, una severidad que no existe o un `client_ref` de
cuatro kb tienen que rechazarse ANTES de tocar la base: si no, el error sale
como un 500 del trigger, que no le dice al operador que paso.

Los CHECK de la base siguen siendo la última línea de defensa (el que escribe
con `psql` no pasa por Pydantic), pero la API falla con un 422 que explica el
motivo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Las severidades, en el orden de la tabla de políticas de SLA.
SEVERIDADES = ("S1", "S2", "S3", "S4")

# Los estados del ciclo de vida. Las Transiciones válidas las controla el
# trigger de la base (`incident_transición_válida`): acá solo se válida que el
# estado exista, porque una transición válida depende del estado ACTUAL, que
# el cliente no manda.
ESTADOS = ("OPEN", "IN_PROGRESS", "WAITING", "RESOLVED", "CLOSED", "CANCELLED")

CATEGORIAS = ("HARDWARE", "SOFTWARE", "NETWORK", "PERIPHERAL", "PHYSICAL", "ACCESS", "OTHER")


class CrearIncidenciaRequest(BaseModel):
    """
    Alta de una incidencia.

    `client_ref` es la idempotencia: la app es una PWA con cobertura mala, y
    el mismo alta puede llegar dos veces. Con el mismo `client_ref`, el
    backend devuelve LA MISMA incidencia en vez de crear dos.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(..., min_length=5, max_length=200)
    description: str | None = Field(None, max_length=4000)
    severity: str = Field("S3")
    category: str = Field("OTHER")
    device_id: int | None = Field(None, gt=0)
    model_id: int | None = Field(None, gt=0)
    location_id: int | None = Field(None, gt=0)
    assigned_to: str | None = Field(None, max_length=64)
    client_ref: str | None = Field(None, max_length=64)
    # Un costo estimado, en la moneda del sistema. El costo real se carga al
    # cerrar.
    cost: float | None = Field(None, ge=0)
    downtime_minutes: int | None = Field(None, ge=0)

    @field_validator("severity")
    @classmethod
    def _severidad_valida(cls, valor: str) -> str:
        if valor not in SEVERIDADES:
            raise ValueError(f"Severidad invalida. Validas: {', '.join(SEVERIDADES)}.")
        return valor

    @field_validator("category")
    @classmethod
    def _categoria_valida(cls, valor: str) -> str:
        if valor not in CATEGORIAS:
            raise ValueError(f"Categoria invalida. Validas: {', '.join(CATEGORIAS)}.")
        return valor


class ActualizarIncidenciaRequest(BaseModel):
    """
    Edicion parcial de una incidencia.

    `versión` es el BLOQUEO OPTIMISTA: es la versión que el cliente leyo. Si
    otro operador guardo antes, la versión cambió y el backend responde 409
    con un conflicto explicito, en vez de pisar el trabajo del otro.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    version: int = Field(..., gt=0)
    title: str | None = Field(None, min_length=5, max_length=200)
    description: str | None = Field(None, max_length=4000)
    severity: str | None = None
    category: str | None = None
    assigned_to: str | None = Field(None, max_length=64)
    location_id: int | None = Field(None, gt=0)
    cost: float | None = Field(None, ge=0)
    downtime_minutes: int | None = Field(None, ge=0)
    root_cause: str | None = Field(None, max_length=4000)
    resolution: str | None = Field(None, max_length=4000)

    @field_validator("severity")
    @classmethod
    def _severidad_valida(cls, valor: str | None) -> str | None:
        if valor is not None and valor not in SEVERIDADES:
            raise ValueError(f"Severidad invalida. Validas: {', '.join(SEVERIDADES)}.")
        return valor

    @field_validator("category")
    @classmethod
    def _categoria_valida(cls, valor: str | None) -> str | None:
        if valor is not None and valor not in CATEGORIAS:
            raise ValueError(f"Categoria invalida. Validas: {', '.join(CATEGORIAS)}.")
        return valor


class CambiarEstadoRequest(BaseModel):
    """Una transición de estado. Las válidas las exige la base."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    estado: str
    nota: str | None = Field(None, max_length=2000)

    @field_validator("estado")
    @classmethod
    def _estado_valido(cls, valor: str) -> str:
        if valor not in ESTADOS:
            raise ValueError(f"Estado invalido. Validos: {', '.join(ESTADOS)}.")
        return valor


class ComentarRequest(BaseModel):
    """Una nota en la línea de tiempo."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    nota: str = Field(..., min_length=1, max_length=2000)


class CerrarIncidenciaRequest(BaseModel):
    """
    El cierre de un caso: que se hizo y que paso con el EQUIPO.

    `destino_equipo` es lo que hace que este Service Desk sea el control del
    stock danado y no un ticket de software:

    - TALLER: el equipo se rompio y va al taller (`IN_REPAIR`).
    - REPARADO: se arreglo y vuelve al depósito (`IN_WAREHOUSE`).
    - Daño_TOTAL: no se arregla; el equipo se desincorpora del stock.

    Sin destino, el caso se cierra y el inventario no se entera: el equipo
    sigue contando como disponible cuando en realidad está roto.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resolution: str = Field(..., min_length=3, max_length=4000)
    estado: str = Field("RESOLVED")
    destino_equipo: str = Field("NINGUNO")

    @field_validator("estado")
    @classmethod
    def _estado_valido(cls, valor: str) -> str:
        if valor not in ESTADOS:
            raise ValueError(f"Estado invalido. Validos: {', '.join(ESTADOS)}.")
        return valor

    @field_validator("destino_equipo")
    @classmethod
    def _destino_valido(cls, valor: str) -> str:
        validos = ("NINGUNO", "TALLER", "REPARADO", "DANO_TOTAL")
        if valor not in validos:
            raise ValueError(f"Destino invalido. Validos: {', '.join(validos)}.")
        return valor


class IncidenciaResumen(BaseModel):
    """Una incidencia en la lista: lo mínimo para identificarla."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    title: str
    severity: str
    status: str
    category: str
    device_id: int | None = None
    model_id: int | None = None
    location_id: int | None = None
    reported_by: UUID
    assigned_to: UUID | None = None
    created_at: datetime
    updated_at: datetime
    response_due_at: datetime
    resolution_due_at: datetime
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    version: int
    # Del join con la vista de SLA.
    minutos_para_resolver: float | None = None
    vencido: bool = False
    fuera_de_sla: bool = False


class EventoIncidencia(BaseModel):
    """Una línea de la línea de tiempo."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    actor_id: UUID | None = None
    note: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class IncidenciaDetalle(IncidenciaResumen):
    """La incidencia completa, con su línea de tiempo."""

    description: str | None = None
    resolution: str | None = None
    root_cause: str | None = None
    cost: float | None = None
    downtime_minutes: int | None = None
    eventos: list[EventoIncidencia] = []

    # Lo que paso con el EQUIPO al cerrar el caso ("quedó en el taller con la
    # orden #12"). La pantalla lo muestra: el técnico necesita ver que el
    # equipo se movio de verdad, no solo que el caso se cerró.
    mensaje_equipo: str | None = None


class ListaIncidenciasResponse(BaseModel):
    items: list[IncidenciaResumen]
    total: int
    limite: int
    offset: int
