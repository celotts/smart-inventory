"""
Schemas de movimientos y escaneo.

EL CAMPO MAS IMPORTANTE: `client_scan_id`
------------------------------------------
Es lo que hace que escanear dos veces no duplique el movimiento. Lo genera el
DISPOSITIVO, no el servidor, porque es la unica forma de que sobreviva al viaje:
si lo generara el servidor, cada reintento tendria uno distinto y la
idempotencia no existiria.

El cliente lo crea con `crypto.randomUUID()` ANTES de hacer el `fetch`, y reutiliza
el mismo valor para todos los reintentos de ese envio.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.modules.movements.service import ESTADO_POR_MOVIMIENTO

TIPOS_MOVIMIENTO = sorted(ESTADO_POR_MOVIMIENTO)


class EscanearRequest(BaseModel):
    """
    Un escaneo.

    `extra="forbid"` es deliberado: si el cliente manda un campo que no existe,
    se rechaza. Sin esto, un typo como `ubicacion` en vez de `ubicacion_id`
    pasa el modelo con un valor `None` silencioso, el escaneo se registra sin
    destino y el componente queda donde estaba sin que nadie lo note. Es el
    peor tipo de bug: no falla, deja de hacer lo que dice.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    codigo: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="QR o serial leido por el lector.",
    )
    tipo_movimiento: str = Field(
        ...,
        description="Uno de: " + ", ".join(TIPOS_MOVIMIENTO),
    )
    client_scan_id: UUID = Field(
        ...,
        description=(
            "Idempotencia. Generar en el dispositivo antes de enviar y "
            "reutilizar el mismo valor en los reintentos de este envio."
        ),
    )
    ubicacion_id: int | None = Field(
        None, gt=0, description="Sede de destino del movimiento."
    )
    latitude: float | None = Field(
        None, ge=-90, le=90, description="GPS del momento del escaneo."
    )
    longitude: float | None = Field(
        None, ge=-180, le=180, description="GPS del momento del escaneo."
    )
    replaced_device_id: int | None = Field(
        None, gt=0, description="Solo para REPLACEMENT: el equipo que sale."
    )
    replacement_reason: str | None = Field(
        None, max_length=500, description="Solo para REPLACEMENT."
    )
    observations: str | None = Field(None, max_length=1000)

    @field_validator("tipo_movimiento")
    @classmethod
    def _tipo_valido(cls, valor: str) -> str:
        if valor not in ESTADO_POR_MOVIMIENTO:
            raise ValueError(
                f"Tipo de movimiento desconocido: {valor!r}. "
                f"Validos: {', '.join(TIPOS_MOVIMIENTO)}."
            )
        return valor

    def check_reemplazo(self) -> None:
        """
        Valida el par de reemplazo.

        Va como metodo y no como `model_validator` para que el error salga con
        el status que corresponde (400) y no como un 422 de validacion de
        esquema: la diferencia para el operador es "tu pedido esta mal" contra
        "el formulario esta roto", y este caso es el primero.
        """
        es_reemplazo = self.tipo_movimiento == "REPLACEMENT"
        if es_reemplazo and not (self.replaced_device_id and self.replacement_reason):
            raise ValueError(
                "Un movimiento de reemplazo tiene que decir QUE componente se "
                "reemplaza (replaced_device_id) y POR QUE (replacement_reason)."
            )
        if not es_reemplazo and (self.replaced_device_id or self.replacement_reason):
            raise ValueError(
                f"Un movimiento de tipo {self.tipo_movimiento} no lleva equipo "
                "reemplazado ni motivo: solo REPLACEMENT los lleva."
            )


class ResultadoGeo(BaseModel):
    """El veredicto de la geo-validacion, con lo que el operador necesita ver."""

    dentro_del_radio: bool | None = Field(
        None,
        description=(
            "None = no se pudo verificar (sin GPS, o sede sin coordenadas). "
            "False = se pudo verificar y estabas fuera."
        ),
    )
    distancia_metros: float | None = None
    radio_metros: int | None = None
    sede: str | None = None
    sede_sin_coordenadas: str | None = None


class EscanearResponse(BaseModel):
    """
    Resultado del escaneo.

    `replay` esta en el contrato porque el cliente lo necesita: si es `True` ya
    estaba registrado y no tiene que avisarle al operador que hay un problema.
    Sin ese campo, el frontend tiene que deducirlo comparando `movement_id` con
    lo que ya tenia, que es fragile.
    """

    replay: bool = Field(
        ..., description="True si este envio ya se habia procesado antes."
    )
    movement_id: int | None = None
    tipo_movimiento: str
    movimiento_date: datetime | None = None
    dispositivo: dict[str, Any]
    geo: ResultadoGeo
    mensaje: str


class HistorialMovimiento(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    movement_type: str
    movement_date: datetime
    scan_latitude: float | None = None
    scan_longitude: float | None = None
    is_valid_location: bool | None = None
    observations: str | None = None
    replacement_reason: str | None = None
    origen: str | None = None
    destino: str | None = None
    registrado_por: str | None = None
    equipo_reemplazado: str | None = None


class ListaMovimientosResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class EscanearLoteRequest(BaseModel):
    """
    Conteo ciclico: N codigos en una request.

    El maximo lo pone `scan_batch_max`. Sin tope, un cliente puede mandar 100.000
    codigos en un POST y la API los procesa todos en una transaccion: 100.000
    inserts y updates de estado, con el rate limit contando UNA request.
    """

    model_config = ConfigDict(extra="forbid")

    codigos: list[str] = Field(..., min_length=1, max_length=500)
    tipo_movimiento: str = Field(..., description="Uno de: " + ", ".join(TIPOS_MOVIMIENTO))
    ubicacion_id: int | None = Field(None, gt=0)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @field_validator("codigos")
    @classmethod
    def _codigos_no_vacios(cls, valores: list[str]) -> list[str]:
        """
        Cada codigo tiene que tener algo adentro.

        Sin esto, un `""` en medio de la lista llega al `resolver_codigo`, que
        devuelve "el codigo viene vacio", y el lote entero falla con un error
        para un elemento que el operador ni puede haber escaneado.
        """
        return [v.strip() for v in valores if v and v.strip()]


class ResultadoLote(BaseModel):
    """Resultado del conteo: uno por codigo, con el motivo si fallo."""

    total: int
    registrados: int
    fallidos: int
    resultados: list[EscanearResponse | dict[str, Any]]


class MovimientoTipoResponse(BaseModel):
    """Metadatos de un tipo de movimiento para UI."""
    valor: str
    texto: str
    descripcion: str
    icono: str
    estado_destino: str
    requiere_reemplazo: bool = False
