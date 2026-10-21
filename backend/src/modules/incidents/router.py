"""
Router de incidencias. Montado en /api/incidents.

QUE PERMITE HACER
-----------------
- Reportar una incidencia (cualquiera del equipo).
- Ver las abiertas, filtradas por severidad, responsable, equipo o vencidas.
- Cambiar el estado, asignar, comentar: todo queda en la línea de tiempo.
- Ver el resumen del tablero: cuantas abiertas, cuantas vencidas, cuanto tarda
  el taller.

LOS Permisos
------------
- `incidents:read`: ver. Lo tiene el operador, el técnico y el admin.
- `incidents:create`: reportar. Lo tiene el operador (el que esta en el
  deposito y ve el equipo roto) y el técnico.
- `incidents:manage`: cambiar el estado, asignar y cerrar. Es del técnico y
  del admin: reportar un problema es de cualquiera, resolverlo es del taller.

REPARAR Y REABRIR
-----------------
Reabrir una incidencia resuelta es una transicion Válida (el problema
volvio), y el trigger de la base lo permite. Un caso cerrado o cancelado no
se reabre: para eso se abre uno nuevo. La razon esta en el trigger
`incident_transicion_válida`.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.incidents import service
from src.modules.incidents.schemas import (
    ActualizarIncidenciaRequest,
    CambiarEstadoRequest,
    CerrarIncidenciaRequest,
    ComentarRequest,
    CrearIncidenciaRequest,
    IncidenciaDetalle,
    IncidenciaResumen,
    ListaIncidenciasResponse,
)
from src.modules.users.models import UsuarioActual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/incidents", tags=["incidencias"])


def _errores(exc: Exception) -> HTTPException:
    """Traduce las excepciones del service a HTTP."""
    if isinstance(exc, service.IncidenciaNoEncontrada):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, service.ConflictoDeVersion):
        # 409: el conflicto es con el ESTADO ACTUAL de la incidencia, no con
        # la forma del pedido.
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, service.IncidenciaInvalida):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    # Un 500 NUNCA es silencioso: sin este log, un bug queda sin rastro y lo
    # único que se ve es "Error interno." en la pantalla del operador.
    logger.exception("Error inesperado en incidencias: %s", exc)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno.")


@router.get(
    "",
    response_model=ListaIncidenciasResponse,
    summary="Listar incidencias",
)
def listar(
    estado: str | None = Query(None, description="Uno de: OPEN, IN_PROGRESS, WAITING, RESOLVED, CLOSED, CANCELLED."),
    severidad: str | None = Query(None, description="Uno de: S1, S2, S3, S4."),
    categoria: str | None = Query(None),
    asignado_a: str | None = Query(None, description="UUID del responsable."),
    device_id: int | None = Query(None, gt=0),
    model_id: int | None = Query(None, gt=0),
    location_id: int | None = Query(None, gt=0),
    solo_vencidas: bool = Query(False, description="Solo las que ya pasaron su plazo de resolucion."),
    solo_abiertas: bool = Query(False, description="Solo las que siguen abiertas (OPEN, IN_PROGRESS, WAITING)."),
    solo_sin_asignar: bool = Query(False, description="Solo las abiertas y sin responsable asignado."),
    sin_atender: bool = Query(False, description="Solo las abiertas que nadie respondio todavia (el backlog)."),
    solo_mias: bool = Query(False, description="Solo las asignadas al usuario que pregunta."),
    fuera_de_sla: bool = Query(False, description="Solo las que se resolvieron despues del plazo."),
    busqueda: str | None = Query(None, max_length=100, description="Texto libre: codigo, titulo o descripcion."),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> ListaIncidenciasResponse:
    """
    Listado paginado, con filtros combinables.

    El orden es el que un operador necesita: las VENCIDAS primero, y dentro de
    cada grupo, las más graves y las más antiguas.
    """
    return service.listar_incidencias(
        estado=estado,
        severidad=severidad,
        categoria=categoria,
        asignado_a=asignado_a,
        device_id=device_id,
        model_id=model_id,
        location_id=location_id,
        solo_vencidas=solo_vencidas,
        solo_abiertas=solo_abiertas,
        solo_sin_asignar=solo_sin_asignar,
        sin_atender=sin_atender,
        solo_mias=solo_mias,
        mi_usuario=str(usuario.id) if solo_mias else None,
        fuera_de_sla=fuera_de_sla,
        busqueda=busqueda,
        limite=limite,
        offset=offset,
    )


@router.get(
    "/metrics",
    summary="Resumen de incidencias para el tablero",
)
def metricas(
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> dict:
    """
    Cuantas abiertas, cuantas vencidas, y cuanto se demora el taller (MTTR).

    El MTTR es el número que le importa al responsable: no como hay many
    tickets, sino cuanto tarda el taller en resolverlos.
    """
    return service.metricas(user_id=usuario.id)




# ===========================================================================
# AVISOS DE SLA
# ===========================================================================


@router.get(
    "/notifications",
    summary="Avisos de SLA del usuario",
)
def listar_notificaciones(
    solo_no_leidas: bool = Query(False),
    limite: int = Query(30, ge=1, le=100),
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> list[dict]:
    """
    Los avisos: los propios y los generales.

    Un aviso general (sin destinatario) le llega a todos los que pueden ver
    el caso: "el depósito se quedó sin conexión" le interesa a más de uno.
    """
    return service.listar_notificaciones(
        user_id=usuario.id,
        solo_no_leidas=solo_no_leidas,
        limite=limite,
    )


@router.get(
    "/notifications/count",
    summary="Cuantos avisos sin leer tiene el usuario",
)
def contar_notificaciones(
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> dict:
    return {"no_leidos": service.contar_notificaciones(user_id=usuario.id)}


@router.post(
    "/notifications/{notificacion_id}/leer",
    summary="Marcar un aviso como leido",
)
def marcar_leida(
    notificacion_id: int,
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> dict:
    try:
        return service.marcar_notificacion_leida(notificacion_id, user_id=usuario.id)
    except Exception as exc:
        raise _errores(exc) from exc


@router.post(
    "/sweep",
    summary="Correr el barrido de SLA a mano (admin)",
)
def barrer(
    minutos_aviso: int = Query(service.MINUTOS_AVISO_POR_DEFECTO, ge=1, le=1440),
    _usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> dict:
    """
    El barrido: avisa de los casos que vencen y escala los que se pasaron.

    Lo corre el backend en segundo plano cada minuto. Este endpoint existe
    para probarlo y para que un operador pueda forzar el barrido sin esperar.
    """
    return service.barrer_sla(minutos_aviso=minutos_aviso)


# ===========================================================================
# CALENDARIO LABORAL
# ===========================================================================


@router.get(
    "/calendar",
    summary="Configuracion del calendario laboral del SLA",
)
def ver_calendario(
    _usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> dict:
    """
    La ventana en la que corren los plazos del SLA.

    Es lo que define si un SLA de 4 horas son 4 horas de reloj o 4 horas de
    trabajo. Y los días que el sistema NO conoce (feriados) son una limitacion
    declarada: la lista se carga a mano.
    """
    try:
        return service.obtener_calendario()
    except Exception as exc:
        raise _errores(exc) from exc


@router.put(
    "/calendar",
    summary="Cambiar la configuracion del calendario laboral (admin)",
)
def cambiar_calendario(
    body: dict = Body(...),
    _usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> dict:
    """
    Body:
    {
      "timezone": "America/Argentina/Buenos_Aires",
      "day_start": "09:00",
      "day_end": "18:00",
      "workdays": [1, 2, 3, 4, 5],
      "holidays": ["2026-12-25"]
    }

    La zona horaria se válida contra las que el propio Postgres tiene: una
    zona inválida hace fallar el calculo del SLA en el ALTA de una incidencia,
    que es el peor momento para descubrirlo.
    """
    try:
        return service.actualizar_calendario(body)
    except Exception as exc:
        raise _errores(exc) from exc


@router.get(
    "/{incidencia_id}",
    response_model=IncidenciaDetalle,
    summary="Ver una incidencia con su linea de tiempo",
)
def obtener(
    incidencia_id: UUID,
    _usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_VER)),
) -> IncidenciaDetalle:
    try:
        return service.obtener_incidencia(incidencia_id)
    except Exception as exc:
        raise _errores(exc) from exc


@router.post(
    "",
    response_model=IncidenciaDetalle,
    status_code=status.HTTP_201_CREATED,
    summary="Reportar una incidencia",
)
def crear(
    pedido: CrearIncidenciaRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_CREAR)),
) -> IncidenciaDetalle:
    """
    Alta de una incidencia.

    IDEMPOTENTE: si viene `client_ref` y ya existe una incidencia con ese
    ref, se devuelve LA MISMA. La app es una PWA con cobertura mala y el
    mismo alta puede llegar dos veces; sin esto, un corte de conexión a mitad
    del envió deja dos casos iguales.
    """
    try:
        return service.crear_incidencia(
            pedido.model_dump(),
            user_id=usuario.id,
            client_ref=pedido.client_ref,
        )
    except Exception as exc:
        raise _errores(exc) from exc


@router.patch(
    "/{incidencia_id}",
    response_model=IncidenciaDetalle,
    summary="Editar una incidencia",
)
def actualizar(
    incidencia_id: UUID,
    pedido: ActualizarIncidenciaRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_GESTIONAR)),
) -> IncidenciaDetalle:
    """
    Edicion parcial, con BLOQUEO OPTIMISTA.

    El `versión` que manda el cliente es la que leyo. Si otro operador guardo
    antes, la base tiene otra versión y el backend responde 409 con un
    conflicto explicito, en vez de pisar su trabajo.
    """
    try:
        return service.actualizar_incidencia(
            incidencia_id,
            pedido.model_dump(exclude_none=True),
            user_id=usuario.id,
        )
    except Exception as exc:
        raise _errores(exc) from exc


@router.post(
    "/{incidencia_id}/transitions",
    response_model=IncidenciaDetalle,
    summary="Cambiar el estado de una incidencia",
)
def cambiar_estado(
    incidencia_id: UUID,
    pedido: CambiarEstadoRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_GESTIONAR)),
) -> IncidenciaDetalle:
    """
    Una transicion de estado.

    Las transiciones válidas las exige la BASE (trigger): si el par
    actual -> nuevo no esta permitido, el UPDATE falla y se traduce a un 409
    con el motivo. La API no reimplementa la maquina de estados: dos
    implementaciones se desincronizan, y la que queda sin actualizar es la que
    deja pasar lo imposible.
    """
    try:
        return service.cambiar_estado(
            incidencia_id,
            pedido.estado,
            user_id=usuario.id,
            nota=pedido.nota,
        )
    except Exception as exc:
        raise _errores(exc) from exc


@router.post(
    "/{incidencia_id}/claim",
    response_model=IncidenciaDetalle,
    summary="Tomar un caso de la bandeja",
)
def tomar_caso(
    incidencia_id: UUID,
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_GESTIONAR)),
) -> IncidenciaDetalle:
    """
    Toma un caso de la bandeja: queda asignado a quien lo toma.

    Es la accion del técnico al empezar el turno: un clic y el caso es suyo,
    con su plazo de respuesta ya arrancando y el que lo reporto avisado.
    """
    try:
        return service.tomar_caso(incidencia_id, user_id=usuario.id)
    except Exception as exc:
        raise _errores(exc) from exc


@router.post(
    "/{incidencia_id}/cierre",
    response_model=IncidenciaDetalle,
    summary="Resolver un caso y definir el destino del equipo",
)
def cerrar_caso(
    incidencia_id: UUID,
    pedido: CerrarIncidenciaRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_GESTIONAR)),
) -> IncidenciaDetalle:
    """
    Cierra el caso y dice que paso con el EQUIPO.

    - TALLER: el equipo quedó roto, en el taller (`IN_REPAIR`).
    - REPARADO: se reparo y volvio al depósito (`IN_WAREHOUSE`).
    - Daño_TOTAL: no se arregla; sale del stock con su certificado de baja.

    El que reporto el caso recibe el aviso del cierre.
    """
    try:
        return service.cerrar_caso(
            incidencia_id,
            pedido.model_dump(),
            user_id=usuario.id,
        )
    except Exception as exc:
        raise _errores(exc) from exc


@router.post(
    "/{incidencia_id}/comments",
    response_model=IncidenciaDetalle,
    summary="Agregar una nota a la linea de tiempo",
)
def comentar(
    incidencia_id: UUID,
    pedido: ComentarRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.INCIDENCIAS_GESTIONAR)),
) -> IncidenciaDetalle:
    try:
        return service.comentar(incidencia_id, pedido.nota, user_id=usuario.id)
    except Exception as exc:
        raise _errores(exc) from exc
