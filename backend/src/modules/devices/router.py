"""
Router de dispositivos. Montado en /api/devices.

LA RUTA QUE MAS SE USA
----------------------
`GET /api/devices/resolve` es el endpoint que el lector de QR golpea en cada
escaneo, y esta disenado para el caso de uso real: una persona con un telefono en
una mano, en un deposito, sin conexion perfecta.

De ahi salen las tres decisiones que se ven en el codigo:

  - Nunca levanta 404 por un codigo que no existe. Devuelve 200 con
    `encontrado: false` y un `motivo` que dice QUE hacer. Un 404 en un escaner se
    ve como "la app se cayo"; un motivo se ve como "esa etiqueta no es de acá".

  - No pide permisos de escritura para LEER. Resolver un codigo es una
    operacion de lectura: la exige el permiso de ver inventario, que es el que
    tiene el operador. Pedir permiso de creacion para leer una etiqueta haria
    que un Tecnico, que tiene `devices:read` pero no `devices:create`, no
    pueda usar el escaner.

  - El codigo va en QUERY STRING y no en el path. Un codigo de barras puede
    traer `/`, `#` o `?` adentro, y puesto en el path rompe el ruteo de FastAPI
    antes de llegar al handler. Ademas un codigo en el path queda en los logs de
    acceso del proxy.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.devices import service
from src.modules.devices.schemas import (
    CrearDispositivoRequest,
    DispositivoDetalle,
    ListaDispositivosResponse,
    ResolverCodigoResponse,
)
from src.modules.users.models import UsuarioActual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["dispositivos"])

# Estados validos del inventario. Se repite aca, y no se importa de la base,
# porque es el conjunto que el ESQUEMA de la API promete: si el dia de manana se
# agrega un estado, el frontend tiene que enterarse por el tipo, no por un 400
# en produccion.
ESTADOS = (
    "IN_WAREHOUSE",
    "IN_TRANSIT",
    "INSTALLED",
    "IN_REPAIR",
    "PERMANENTLY_DISPOSED",
)


@router.get(
    "/resolve",
    response_model=ResolverCodigoResponse,
    summary="Resuelve un codigo QR o serial a un componente",
)
def resolver(
    codigo: str = Query(
        ...,
        min_length=1,
        max_length=128,
        description="Lo que devolvio el lector: QR o serial.",
    ),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> ResolverCodigoResponse:
    """
    El endpoint del escaner.

    NUNCA da 404. Un codigo que no existe, uno que esta dado de baja y uno que
    esta activo devuelven 200 con distinto `encontrado` y distinto `motivo`,
    porque para el operador son tres pasos distintos a seguir.
    """
    return service.resolver_codigo(codigo)


@router.post(
    "",
    response_model=DispositivoDetalle,
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta un componente",
)
def crear(
    pedido: CrearDispositivoRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> DispositivoDetalle:
    """
    Alta de componente.

    Si no viene `qr_code`, se genera uno propio con prefijo `INV-`. La razon esta
    en `service.generar_qr()`: el serial es del fabricante y hay proveedores que
    los reciclan.
    """
    try:
        return service.crear_dispositivo(pedido.model_dump())
    except service.CodigoDuplicado as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        # Referencia a un model_id o location_id que no existe. El mensaje dice
        # cual de los dos es, que es lo unico que necesita saber el operador para
        # corregirlo.
        campo = "model_id" if "model_id" in str(exc) else "location_id"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El {campo} no corresponde a ningun registro del catalogo.",
        ) from exc


@router.get(
    "",
    response_model=ListaDispositivosResponse,
    summary="Listar componentes",
)
def listar(
    estado: str | None = Query(None, description="Uno de: " + ", ".join(ESTADOS)),
    ubicacion_id: int | None = Query(None, gt=0),
    categoria: str | None = Query(None, max_length=100),
    busqueda: str | None = Query(None, max_length=100, description="Serial o QR."),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> ListaDispositivosResponse:
    """
    Listado paginado.

    `limite` con tope de 200: sin tope, un cliente puede pedir un solo POST con
    `limite=100000` y la consulta trae la tabla entera. El tope no es
    arbitrario, es el punto en el que la respuesta deja de caber comoda en un
    celular.
    """
    if estado and estado not in ESTADOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "mensaje": "El estado no es valido.",
                "validos": list(ESTADOS),
                "recibido": estado,
            },
        )

    return service.listar_dispositivos(
        estado=estado,
        ubicacion_id=ubicacion_id,
        categoria=categoria,
        busqueda=busqueda,
        limite=limite,
        offset=offset,
    )


@router.get(
    "/{device_id}",
    response_model=DispositivoDetalle,
    summary="Ficha de un componente",
)
def obtener(
    device_id: int,
    incluir_historial: bool = Query(
        False,
        description="Incluye los ultimos movimientos en `movimientos`.",
    ),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """
    Ficha con dias instalados y ultimo movimiento.

    El `device_id` va por parametro de path, no por query, para que no se pueda
    pasar un id en una URL distinta a la que dice el nombre.
    """
    try:
        ficha = service.obtener_dispositivo(device_id)
    except service.DispositivoNoEncontrado as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if incluir_historial:
        from src.modules.movements import service as movimientos

        ficha["movimientos"] = movimientos.historial_dispositivo(device_id, limite=25)

    return ficha
