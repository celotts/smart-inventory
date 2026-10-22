"""
Router de Reportes - Reportes ejecutivos, KPIs, tendencias.
Montado en /api/reports.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.reporting import service, valor_libro
from src.modules.users.models import UsuarioActual

router = APIRouter(prefix="/api/reports", tags=["reporting"])


@router.get(
    "/operational",
    summary="Reporte operativo completo (redactado por LLM)",
)
def reporte_operativo(
    _usuario: UsuarioActual = Depends(exigir(Permiso.IA_PREGUNTAR)),
) -> dict:
    """Genera reporte en prosa: resumen, alertas, KPIs, detalle, recomendaciones."""
    return service.reporte_operativo()


@router.get(
    "/dashboard",
    summary="Dashboard: KPIs, alertas, ultimos movimientos y productos criticos",
)
def dashboard(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """
    Todo lo de la pantalla de inicio en UNA llamada.

    La pantalla hace varias consultas: si cada una fuera un endpoint distinto,
    abrirla haria seis pedidos, y en un celular con 4G eso son seis esperas.
    """
    return service.dashboard()


@router.get(
    "/inventory-stats",
    summary="KPIs de inventario: total, por estado, por ubicacion, valor, rotacion",
)
def estadisticas_inventario(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    return service.estadisticas_inventario()


@router.get(
    "/rotation",
    summary="Rotacion de modelos: fast/slow movers en N días",
)
def rotacion_modelos(
    dias: int = Query(30, ge=1, le=365),
    top: int = Query(10, ge=1, le=50),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> list[dict]:
    return service.rotacion_modelos(dias=dias, top=top)


@router.get(
    "/install-time",
    summary="Tiempo promedio OUT_TO_CLIENT → INSTALLATION por modelo/cliente",
)
def tiempo_instalacion(
    model_id: int | None = Query(None, gt=0),
    cliente_id: int | None = Query(None, gt=0),
    dias: int = Query(90, ge=1, le=365),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[dict]:
    return service.tiempo_instalacion(model_id=model_id, cliente_id=cliente_id, dias=dias)


@router.get(
    "/valor-libro",
    summary="Valor libro de los activos (depreciacion lineal)",
)
def ver_valor_libro(
    client_id: int | None = Query(None, gt=0, description="Solo los equipos del cliente."),
    estado: str | None = Query(None, description="Filtra por estado del equipo."),
    limite: int = Query(200, ge=1, le=1000),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """
    Lo que el equipo vale HOY, no lo que costo.

    Es el reporte que pide el cliente y la contabilidad: el valor de la flota
    instalada, con la depreciacion acumulada y el valor libro por equipo.

    Sin `purchase_date` o sin `purchase_value` el equipo no entra en el
    cálculo (y el resumen dice cuantos son): un valor libro estimado con una
    fecha inventada es una mentira con formato de número.
    """
    return valor_libro.valor_libro(client_id=client_id, estado=estado, limite=limite)
