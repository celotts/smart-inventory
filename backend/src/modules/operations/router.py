"""
Router de operaciones: transferencias, mantenimiento y alertas de stock.

POR QUE ESTE ARCHIVO SE REESCRIBIO ENTERO
-----------------------------------------
La version anterior aceptaba `payload: dict` sin validar nada, y hacia tres
cosas que no pueden quedar asi:

  1. `POST /api/dispositivos` tomaba el modelo y la ubicacion del PRIMER
     registro (`SELECT id FROM models LIMIT 1`). Todos los equipos dados de alta
     tenian el mismo modelo y la misma ubicacion. Eso no es "datos faltantes":
     son datos FALSOS, y un inventario con datos falsos es peor que uno sin
     datos, porque responde con seguridad. Ahora el alta exige `model_id` y va
     en `POST /api/devices`, con validacion.

  2. Ningun endpoint tenia control de permisos. `GET /api/ops/alertas` era
     publico para cualquiera con un token, incluido un Técnico que por diseno no
     ve datos de la operacion.

  3. Ninguno comprobaba que el `device_id` existiera. Mandar uno inexistente
     daba un 500 de Postgres ("violates foreign key constraint") en vez de un
     404 que dice cual fue el error.

COMPATIBILIDAD
--------------
`POST /api/ops/dispositivos` se conserva como alias de `POST /api/devices` para
no romper el frontend que ya lo llama, pero devuelve un 410 con la ruta nueva:
un endpoint que funciona a medias y ademas esta duplicado es peor que uno que
falla diciendo a donde ir.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.ai.executor import get_ai_engine
from src.modules.users.models import UsuarioActual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["operaciones"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class TransferenciaRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    device_id: int = Field(..., gt=0)
    from_location: str = Field(..., min_length=1, max_length=128)
    to_location: str = Field(..., min_length=1, max_length=128)
    motivo: str | None = Field(None, max_length=500)

    @field_validator("to_location")
    @classmethod
    def _distintas(cls, valor: str, info) -> str:
        """
        Una traslado a la misma sede se rechaza en Python, no en la base.

        El CHECK `ck_transfers_distintas` también existe, y es el que manda: acá
        el mensaje es útil ("origen y destino son la misma sede") y el de
        Postgres es un `violates check constraint`. El trigger de la base sigue
        siendo la garantía para los caminos que no pasan por acá.
        """
        datos = info.data
        if "from_location" in datos and valor == datos["from_location"]:
            raise ValueError("El origen y el destino son la misma sede.")
        return valor


class MantenimientoRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    device_id: int = Field(..., gt=0)
    tipo: str = Field(..., min_length=1, max_length=64)
    descripcion: str | None = Field(None, max_length=2000)
    scheduled_for: date
    tecnico_id: str | None = Field(None, max_length=64)

    @field_validator("tipo")
    @classmethod
    def _sin_control_chars(cls, valor: str) -> str:
        """
        Los caracteres de control no van a un campo de texto libre.

        Sin este filtro, un `tipo` con `\n` o un caracter de control Unicode
        rompe el `text/plain` de la consola de admin y puede inyectar líneas en
        un log que se lee como una columna. Es una defensa de bajo costo contra
        una Outputs rarita de los logs.
        """
        limpio = "".join(c for c in valor if c.isprintable()).strip()
        if not limpio:
            raise ValueError("El tipo de mantenimiento no puede quedar vacio.")
        return limpio


# ---------------------------------------------------------------------------
# Utilidad de existencia
# ---------------------------------------------------------------------------
def _existe(tabla: str, columna: str, valor: Any, etiqueta: str) -> None:
    """
    Verifica que la FK exista y responde 404 si no.

    `tabla` y `columna` son literales del código, nunca vienen del request: por
    eso concatenarlos es seguro. Si VINIERAN del cliente, esto seria una
    inyeccion de SQL.
    """
    with get_ai_engine().connect() as conexion:
        existe = conexion.execute(
            text(f"SELECT 1 FROM {tabla} WHERE {columna} = :v LIMIT 1"),
            {"v": valor},
        ).first()

    if not existe:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El {etiqueta} {valor} no existe.",
        )


# ---------------------------------------------------------------------------
# Transferencias
# ---------------------------------------------------------------------------
@router.post(
    "/transferencias",
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar transferencia",
)
def solicitar_transferencia(
    pedido: TransferenciaRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """Pide mover un componente de una sede a otra. Queda `pending` hasta su aprobacion."""
    _existe("devices", "id", pedido.device_id, "componente")

    try:
        with get_ai_engine().begin() as conexion:
            fila = conexion.execute(
                text(
                    """
                    INSERT INTO transfers (
                        device_id, from_location, to_location, requested_by, status
                    )
                    VALUES (:device_id, :origen, :destino, :usuario, 'pending')
                    RETURNING id, status, created_at
                    """
                ),
                {
                    "device_id": pedido.device_id,
                    "origen": pedido.from_location,
                    "destino": pedido.to_location,
                    "usuario": str(usuario.id),
                },
            ).mappings().first()
    except IntegrityError as exc:
        detalle = str(getattr(exc, "orig", exc))
        if "ck_transfers_distintas" in detalle:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El origen y el destino son la misma sede.",
            ) from exc
        if "ck_transfers_status" in detalle:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El estado de la transferencia no es válido.",
            ) from exc
        raise

    return {
        "id": str(fila["id"]),
        "estado": fila["status"],
        "mensaje": "Transferencia solicitada.",
        "solicitada_por": usuario.full_name,
    }


@router.get("/transferencias", summary="Listar transferencias")
def listar_transferencias(
    estado: str | None = Query(
        None, description="pending, approved, rejected o completed"
    ),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """Transferencias, de la más reciente a la más antigua."""
    where = "WHERE (:estado IS NULL OR t.status = :estado)"
    parametros: dict[str, Any] = {"estado": estado, "limite": limite, "offset": offset}

    with get_ai_engine().connect() as conexion:
        filas = conexion.execute(
            text(
                f"""
                SELECT t.id, t.status, t.created_at, t.from_location, t.to_location,
                       d.serial_number,
                       u.full_name AS solicitada_por,
                       ua.full_name AS aprobada_por
                FROM transfers t
                LEFT JOIN devices d  ON d.id = t.device_id
                LEFT JOIN users u    ON u.id = t.requested_by
                LEFT JOIN users ua   ON ua.id = t.approved_by
                {where}
                ORDER BY t.created_at DESC
                LIMIT :limite OFFSET :offset
                """
            ),
            parametros,
        ).mappings().all()

        total = conexion.execute(
            text(f"SELECT COUNT(*) AS n FROM transfers t {where}"),
            parametros,
        ).scalar_one()

    items = [
        {**dict(f), "id": str(f["id"])} for f in filas
    ]
    return {"items": items, "total": int(total or 0), "limit": limite, "offset": offset}


# ---------------------------------------------------------------------------
# Mantenimiento
# ---------------------------------------------------------------------------
@router.post(
    "/mantenimiento",
    status_code=status.HTTP_201_CREATED,
    summary="Programar mantenimiento",
)
def programar_mantenimiento(
    pedido: MantenimientoRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.REPARACIONES_EDITAR)),
) -> dict:
    """
    Programa un mantenimiento preventivo.

    Requiere `repairs:edit` y no `devices:edit`: programar una revisión es una
    tarea de taller, y un Operador —que si puede mover equipos— no tiene por que
    poder agendar mantenimientos.
    """
    _existe("devices", "id", pedido.device_id, "componente")

    if pedido.tecnico_id:
        _existe("users", "id", pedido.tecnico_id, "tecnico")

    with get_ai_engine().begin() as conexion:
        fila = conexion.execute(
            text(
                """
                INSERT INTO maintenance (
                    device_id, tipo, descripcion, scheduled_for, técnico_id
                )
                VALUES (:device_id, :tipo, :descripcion, :fecha, :tecnico)
                RETURNING id, scheduled_for
                """
            ),
            {
                "device_id": pedido.device_id,
                "tipo": pedido.tipo,
                "descripcion": pedido.descripcion,
                "fecha": pedido.scheduled_for,
                "tecnico": pedido.tecnico_id or str(usuario.id),
            },
        ).mappings().first()

    return {
        "id": str(fila["id"]),
        "scheduled_for": fila["scheduled_for"],
        "mensaje": "Mantenimiento programado.",
    }


@router.get("/mantenimiento", summary="Listar mantenimientos")
def listar_mantenimiento(
    pendientes: bool = Query(True, description="Solo los que no se completaron."),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _usuario: UsuarioActual = Depends(exigir(Permiso.REPARACIONES_VER)),
) -> dict:
    """Mantenimientos programados, del más próximo al más lejano."""
    where = "WHERE (:pendientes = FALSE OR m.completed_at IS NULL)"
    parametros: dict[str, Any] = {"pendientes": pendientes, "limite": limite, "offset": offset}

    with get_ai_engine().connect() as conexion:
        filas = conexion.execute(
            text(
                f"""
                SELECT m.id, m.tipo, m.descripcion, m.scheduled_for, m.completed_at,
                       d.serial_number, u.full_name AS técnico
                FROM maintenance m
                LEFT JOIN devices d ON d.id = m.device_id
                LEFT JOIN users u   ON u.id = m.tecnico_id
                {where}
                ORDER BY m.scheduled_for ASC
                LIMIT :limite OFFSET :offset
                """
            ),
            parametros,
        ).mappings().all()

        total = conexion.execute(
            text(f"SELECT COUNT(*) AS n FROM maintenance m {where}"),
            parametros,
        ).scalar_one()

    items = [{**dict(f), "id": str(f["id"])} for f in filas]
    return {"items": items, "total": int(total or 0), "limit": limite, "offset": offset}


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
@router.get("/alertas", summary="Alertas de stock sin resolver")
def listar_alertas(
    incluir_resueltas: bool = Query(False),
    limite: int = Query(100, ge=1, le=500),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """
    Alertas de stock.

    Requiere `devices:read`. No es un dato sensible (son pisos de stock por
    sede), pero antes era publico para cualquiera con token, y ahora al menos
    depende de un permiso que existe para esto.
    """
    with get_ai_engine().connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT id, ubicacion, mínimo, actual, resuelto, created_at
                FROM stock_alerts
                WHERE (:incluir = TRUE OR resuelto = FALSE)
                ORDER BY (mínimo - actual) DESC, created_at DESC
                LIMIT :limite
                """
            ),
            {"incluir": incluir_resueltas, "limite": limite},
        ).mappings().all()

    return {"items": [{**dict(f), "id": str(f["id"])} for f in filas]}


# ---------------------------------------------------------------------------
# Compatibilidad
# ---------------------------------------------------------------------------
@router.post(
    "/dispositivos",
    status_code=status.HTTP_410_GONE,
    summary="Obsoleto: usar POST /api/devices",
)
def crear_dispositivo_obsoleto() -> None:
    """
    410 Gone, no 404.

    404 dice "esto no existe"; 410 dice "esto existia y fue movido", que es
    exactamente lo que el cliente necesita para reescribir la llamada sin
    adivinar.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Este endpoint fue movido a POST /api/devices. Ademas ahora exige "
            "model_id: antes tomaba el modelo y la ubicacion del primer registro "
            "de la base, y por eso todos los equipos se creaban con el mismo "
            "modelo."
        ),
    )
