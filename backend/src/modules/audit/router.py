"""
Router de auditoría. Montado en /api/audit.

QUIEN PUEDE VER ESTO
--------------------
Solo quien tiene `audit:read`, que es ADMIN. La auditoría muestra quien hizo que,
y eso es información de control: un OPERATOR que pueda ver los movimientos de
todos sus companeros con su nombre y su IP tiene un dato que no necesita para
trabajar.

NO HAY ENDPOINTS DE ESCRITURA
----------------------------
La bitacora no se edita ni se borra desde la API. Un registro de auditoría que
se puede modificar no audita nada. Las filas las escriben los triggers de la
base y `core.audit.registrar()` en el camino de las operaciones.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.audit import service
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/audit", tags=["auditoria"])


@router.get(
    "/timeline",
    summary="Linea de tiempo de lo que paso en el sistema",
)
def timeline(
    limite: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    usuario_id: str | None = Query(
        None,
        description="Solo los eventos de este usuario (UUID).",
    ),
    categoria: str | None = Query(
        None,
        description="'inventario' para movimientos de equipos, 'sistema' para cuentas y logins.",
    ),
    desde: datetime | None = Query(None, description="Desde esta fecha (ISO 8601)."),
    hasta: datetime | None = Query(None, description="Hasta esta fecha (ISO 8601)."),
    busqueda: str | None = Query(
        None,
        max_length=100,
        description="Serial, nombre de usuario, sede o texto de la observacion.",
    ),
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> dict:
    """
    Los eventos del sistema, del más reciente al más antiguo.

    Une los movimientos de inventario con la bitacora de cuentas en una sola
    lista. El detalle de por que está union y no dos pantallas está en el
    docstring de `service.línea_de_tiempo`.
    """
    return service.linea_de_tiempo(
        limite=limite,
        offset=offset,
        usuario_id=usuario_id,
        categoria=categoria,
        desde=desde,
        hasta=hasta,
        busqueda=busqueda,
    )


@router.get(
    "/resumen",
    summary="Cifras de actividad para el encabezado de auditoria",
)
def resumen(
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> dict:
    """Movimientos de hoy, de la semana, y quienes más operaron."""
    return service.resumen()


@router.get(
    "/trazabilidad",
    summary="Trazabilidad completa por tabla/registro",
)
def trazabilidad_completa(
    table_name: str | None = Query(None, description="Nombre de la tabla"),
    record_id: str | None = Query(None, description="ID del registro (UUID)"),
    user_id: str | None = Query(None, description="ID del usuario"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _usuario: UsuarioActual = Depends(exigir(Permiso.AUDITORIA_VER)),
) -> dict:
    """
    Trazabilidad completa con old/new values para INSERT/UPDATE/DELETE.
    Permite ver exactamente que cambio, quien lo hizo y desde dónde.
    """
    from src.core.audit_enhanced import (
        contar_trazabilidad,
        obtener_trazabilidad_completa,
    )
    items = obtener_trazabilidad_completa(
        table_name=table_name,
        record_id=record_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    # El TOTAL es el de todos los registros que cumplen el filtro, no el
    # tamaño de la pagina: sin el, el front no puede saber cuantas paginas
    # hay, y el operador ve 50 de 4000 sin enterarse.
    total = contar_trazabilidad(
        table_name=table_name,
        record_id=record_id,
        user_id=user_id,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
