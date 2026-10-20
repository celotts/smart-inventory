"""
Schemas del modulo de dispositivos.

POR QUE NO SE USA `dict` COMO TIPO DE ENTRADA
---------------------------------------------
El router de operaciones original aceptaba `payload: dict`, que es lo mismo que
no validar nada: un `serial_number` de tipo objeto, un `location_id` como
lista o un `model_id` de 10 megabytes llegan intactos hasta el `INSERT` y
reventan con un error de Postgres 500 cuyo mensaje no le dice nada al operador.

Con pydantic, el error sale antes de tocar la base, dice que campo esta mal, y
el frontend lo puede mostrar. La validacion de entrada no es burocracia: es lo
que evita que el error llegue al motor.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Un serial de fabricante: alfanumericos y un par de separadores. 128 es el
# mismo tope que usa el escaner.
_SERIAL_RE = r"^[A-Za-z0-9._\-/]{1,100}$"


class CrearDispositivoRequest(BaseModel):
    """
    Alta de un componente.

    `model_id` y `location_id` son OBLIGATORIOS a proposito. La version vieja
    los tomaba del primer registro de la tabla (`SELECT id FROM models LIMIT
    1`), lo que significaba que todos los equipos dados de alta tenian el mismo
    modelo y la misma ubicacion: datos falsos, no datos faltantes.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    serial_number: str = Field(
        ...,
        pattern=_SERIAL_RE,
        description="Serial del fabricante. Unico en toda la base.",
    )
    model_id: int = Field(..., gt=0, description="Modelo del catalogo.")
    location_id: int | None = Field(
        None,
        gt=0,
        description="Ubicacion inicial. Si no se manda, el componente queda sin asignar.",
    )
    qr_code: str | None = Field(
        None,
        max_length=128,
        description="Codigo QR. Si no se manda, se genera uno propio.",
        examples=["INV-4f2a1c8b"],
    )
    installation_date: date | None = None

    @field_validator("qr_code")
    @classmethod
    def _qr_sin_espacios(cls, valor: str | None) -> str | None:
        """
        El QR no puede llevar espacios en los bordes.

        Un lector con la camara mal enfocada devuelve " INV-123 " con espacios, y
        si eso se guarda tal cual, el mismo equipo tiene dos codigos distintos
        segun como se escanee. El `strip` de `model_config` ya lo cubre, pero
        se deja explicito porque es una regla del dominio y no un limpieza.
        """
        return valor.strip() if valor else valor


class ActualizarDispositivoRequest(BaseModel):
    """
    Edicion parcial de un componente.

    TODOS LOS CAMPOS SON OPCIONALES, Y ESO NO ES COMODIDAD
    -----------------------------------------------------
    Con `exclude_unset=True` en el service, un campo ausente NO se toca. Si
    `model_id` viniera con valor por defecto, cada vez que alguien corrige un
    serial el equipo cambiaria de modelo. Editar un campo no puede alterar los
    demas.

    `current_status` es opcional pero se valida contra la matriz de transiciones
    en el service: pasar de `IN_WAREHOUSE` a `INSTALLED` en un solo salto es un
    dato que nadie vivio, y el trigger de la base lo rechazaria igual.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    serial_number: str | None = Field(None, pattern=_SERIAL_RE)
    model_id: int | None = Field(None, gt=0)
    location_id: int | None = Field(None, gt=0)
    qr_code: str | None = Field(None, max_length=128)
    installation_date: date | None = None
    current_status: str | None = Field(
        None,
        description="Uno de: IN_WAREHOUSE, IN_TRANSIT, INSTALLED, IN_REPAIR, PERMANENTLY_DISPOSED.",
    )

    @field_validator("qr_code")
    @classmethod
    def _qr_sin_espacios(cls, valor: str | None) -> str | None:
        return valor.strip() if valor else valor


class BajaDispositivoRequest(BaseModel):
    """Baja logica de un componente. El motivo queda en el certificado."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    motivo: str | None = Field(
        None,
        max_length=500,
        description="Por que se da de baja. Queda en el certificado de baja.",
        examples=["Fin de contrato con el cliente."],
    )


class DispositivoResumen(BaseModel):
    """Lo que devuelve el escaner: lo minimo para confirmar en pantalla."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_number: str
    qr_code: str
    current_status: str
    marca: str | None = None
    modelo: str | None = None
    modelo_id: int | None = None
    categoria: str | None = None
    ubicacion_id: int | None = None
    ubicacion_nombre: str | None = None
    installation_date: datetime | None = None
    esta_en_taller: bool = False


class DispositivoDetalle(DispositivoResumen):
    """Mas datos, para la pantalla de ficha."""

    descripcion: str | None = None
    dias_instalado: int | None = Field(
        None,
        description="Dias que lleva instalado. Es lo que hace legible el desgaste.",
    )
    movimientos_totales: int = 0
    ultimo_movimiento: datetime | None = None
    ultimo_movimiento_tipo: str | None = None


class ResolverCodigoResponse(BaseModel):
    """
    Respuesta de la resolucion de un codigo.

    `encontrado` va explicito en lugar de devolver 404. Un escaner que no
    encuentra una etiqueta necesita distinguir tres cosas que para el operador
    son distintas:

      - no existe ese codigo  -> probablemente etiqueta de otro sistema
      - existe pero esta dado de baja -> hay que emitir un certificado nuevo
      - existe y esta activo -> se puede mover

    Con un 404 para todo, el operador ve el mismo error para las tres y no sabe
    que hacer. Con el campo, el frontend puede dar la instruccion correcta.
    """

    encontrado: bool
    codigo: str
    dispositivo: DispositivoResumen | None = None
    motivo: str | None = Field(
        None,
        description="Por que no se resolvio, si no se resolvio.",
        examples=["El componente esta dado de baja desde 2026-01-15."],
    )


class ListaDispositivosResponse(BaseModel):
    items: list[DispositivoResumen]
    total: int
    limit: int
    offset: int
