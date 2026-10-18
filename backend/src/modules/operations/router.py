"""Router de operaciones: transferencias, mantenimiento, alertas de stock."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.core.deps import get_current_user
from src.core.repository import BaseRepository

router = APIRouter(prefix="/api/ops", tags=["operaciones"])
repo = BaseRepository()


@router.post("/transferencias", summary="Solicitar transferencia")
def solicitar_transferencia(payload: dict, usuario=Depends(get_current_user)):
    from uuid import uuid4
    repo.execute(
        """
        INSERT INTO transfers (id, device_id, from_location, to_location, requested_by)
        VALUES (:id, :device_id, :from_loc, :to_loc, :req)
        """,
        {
            "id": str(uuid4()),
            "device_id": payload.get("device_id"),
            "from_loc": payload.get("from_location"),
            "to_loc": payload.get("to_location"),
            "req": str(usuario.id),
        },
    )
    return {"mensaje": "Transferencia solicitada."}


@router.get("/transferencias", summary="Listar transferencias")
def listar_transferencias():
    return {"items": repo.fetch_all("SELECT * FROM transfers ORDER BY created_at DESC LIMIT 100")}


@router.post("/mantenimiento", summary="Programar mantenimiento")
def programar_mantenimiento(payload: dict, usuario=Depends(get_current_user)):
    from uuid import uuid4
    repo.execute(
        """
        INSERT INTO maintenance (id, device_id, tipo, descripcion, scheduled_for, tecnico_id)
        VALUES (:id, :device_id, :tipo, :desc, :fecha, :tec)
        """,
        {
            "id": str(uuid4()),
            "device_id": payload.get("device_id"),
            "tipo": payload.get("tipo"),
            "desc": payload.get("descripcion"),
            "fecha": payload.get("scheduled_for"),
            "tec": str(usuario.id),
        },
    )
    return {"mensaje": "Mantenimiento programado."}


@router.get("/mantenimiento", summary="Listar mantenimientos")
def listar_mantenimiento():
    return {"items": repo.fetch_all("SELECT * FROM maintenance ORDER BY scheduled_for ASC LIMIT 100")}


@router.get("/alertas", summary="Alertas de stock")
def listar_alertas():
    return {"items": repo.fetch_all("SELECT * FROM stock_alerts WHERE resuelto = FALSE ORDER BY created_at DESC")}