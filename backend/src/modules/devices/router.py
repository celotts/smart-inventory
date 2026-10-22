"""
Router de dispositivos. Montado en /api/devices.

LA RUTA QUE Más SE USA
----------------------
`GET /api/devices/resolve` es el endpoint que el lector de QR golpea en cada
escaneo, y esta disenado para el caso de uso real: una persona con un telefono en
una mano, en un deposito, sin conexion perfecta.

De ahi salen las tres decisiones que se ven en el código:

  - Nunca levanta 404 por un código que no existe. Devuelve 200 con
    `encontrado: false` y un `motivo` que dice QUE hacer. Un 404 en un escaner se
    ve como "la app se cayo"; un motivo se ve como "esa etiqueta no es de aca".

  - No pide permisos de escritura para LEER. Resolver un código es una
    operacion de lectura: la exige el permiso de ver inventario, que es el que
    tiene el operador. Pedir permiso de creacion para leer una etiqueta haria
    que un Técnico, que tiene `devices:read` pero no `devices:create`, no
    pueda usar el escaner.

  - El código va en QUERY STRING y no en el path. Un código de barras puede
    traer `/`, `#` o `?` adentro, y puesto en el path rompe el ruteo de FastAPI
    antes de llegar al handler. Ademas un código en el path queda en los logs de
    acceso del proxy.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.core.deps import exigir
from src.core.permissions import Permiso
from src.modules.devices import qr, service
from src.modules.devices.schemas import (    GuardarAtributosRequest,

    ActualizarDispositivoRequest,
    BajaDispositivoRequest,
    CrearDispositivoRequest,
    DispositivoDetalle,
    ListaDispositivosResponse,
    ResolverCodigoResponse,
)
from src.modules.users.models import UsuarioActual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["dispositivos"])

# Estados válidos del inventario. Se repite acá, y no se importa de la base,
# porque es el conjunto que el ESQUEMA de la API promete: si el día de mañana se
# agrega un estado, el frontend tiene que enterarse por el tipo, no por un 400
# en producción.
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

    NUNCA da 404. Un código que no existe, uno que está dado de baja y uno que
    está activo devuelven 200 con distinto `encontrado` y distinto `motivo`,
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

    Si no viene `qr_code`, se genera uno propio con prefijo `INV-`. La razón está
    en `service.generar_qr()`: el serial es del fabricante y hay proveedores que
    los reciclan.
    """
    try:
        return service.crear_dispositivo(pedido.model_dump())
    except service.CodigoDuplicado as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        # Referencia a un model_id o location_id que no existe. El mensaje dice
        # cual de los dos es, que es lo único que necesita saber el operador para
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
    ubicacion_id: int | None = Query(
        None,
        gt=0,
        description="Ubicacion. Incluye sus pasillos, estantes y bins.",
    ),
    categoria: str | None = Query(None, max_length=100, description="Nombre exacto de la categoria."),
    categoria_id: int | None = Query(
        None,
        gt=0,
        description="Categoria. Incluye sus subcategorias.",
    ),
    busqueda: str | None = Query(
        None,
        max_length=100,
        description="Texto libre: serial, QR, SKU, marca o modelo. Es de texto completo.",
    ),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> ListaDispositivosResponse:
    """
    Listado paginado, con filtros combinables.

    `límite` con tope de 200: sin tope, un cliente puede pedir un solo POST con
    `límite=100000` y la consulta trae la tabla entera. El tope no es
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
        categoria_id=categoria_id,
        busqueda=busqueda,
        limite=limite,
        offset=offset,
    )


@router.get(
    "/{device_id}/qr",
    summary="QR de la etiqueta del equipo (SVG)",
    response_class=Response,
    responses={
        200: {"content": {"image/svg+xml": {}}, "description": "QR en SVG."},
    },
)
def qr_de_etiqueta(
    device_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> Response:
    """
    El QR de la etiqueta del equipo, listo para escanear o imprimir.

    QUE CODIFICA
    ------------
    El `qr_code`, que es el código de la ETIQUETA fisica. El escaner resuelve
    las dos cosas (`qr_code` y `serial_number`), así que cualquiera de las dos
    serviria, pero la etiqueta es lo que está pegado al equipo: lo que el
    operador tiene en la mano es eso.

    POR QUE ES UN SVG
    ----------------
    Son unos pocos KB de texto y se ve nitido a cualquier tamaño, sin pixeles.
    Para una etiqueta que se mira de cerca en un depósito, o que se imprime, el
    vector gana al raster.

    El permiso es el de VER inventario, no el de editar: mostrar el QR de un
    equipo es una lectura, como el resto de la ficha.
    """
    try:
        etiqueta = service.obtener_qr_equipo(device_id)
    except service.DispositivoNoEncontrado as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return Response(
        content=qr.qr_como_svg(etiqueta),
        media_type="image/svg+xml",
        headers={
            # La etiqueta de un equipo no cambia: el QR se puede cachear.
            # Un equipo dado de baja conserva su etiqueta histórica.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get(
    "/locations",
    summary="Listar ubicaciones (para selects en alta de dispositivo)",
)
def listar_ubicaciones(
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """Ubicaciones activas para selects. Sin paginación: son pocas."""
    from sqlalchemy import text
    from src.db.session import get_engine

    with get_engine().connect() as conexion:
        filas = conexion.execute(
            text("SELECT id, store_name AS nombre FROM locations WHERE is_active = TRUE ORDER BY store_name")
        ).mappings().all()

    return {"items": [{**dict(f), "id": f["id"]} for f in filas]}


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
    Ficha con días instalados y último movimiento.

    El `device_id` va por parámetro de path, no por query, para que no se pueda
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


@router.patch(
    "/{device_id}",
    response_model=DispositivoDetalle,
    summary="Editar un componente",
)
def actualizar(
    device_id: int,
    pedido: ActualizarDispositivoRequest,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> DispositivoDetalle:
    """
    Edicion parcial. Solo se toca lo que viene en el cuerpo.

    `exclude_unset` es la clave: un campo que el cliente no mando no viaja al
    service y por lo tanto no se escribe. Sin esto, cada `PATCH` de un campo
    pisaria los demas con `None`.
    """
    try:
        return service.actualizar_dispositivo(device_id, pedido.model_dump(exclude_unset=True))
    except service.DispositivoNoEncontrado as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except service.CodigoDuplicado as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except service.TransicionInvalida as exc:
        # 409 y no 422: el cuerpo de la petición es sintacticamente correcto, el
        # problema es que la operación choca con el estado actual del recurso.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        campo = "model_id" if "model_id" in str(exc) else "location_id"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El {campo} no corresponde a ningun registro del catalogo.",
        ) from exc


@router.get(
    "/{device_id}/atributos",
    summary="Los atributos de un equipo",
)
def listar_atributos(
    device_id: int,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """
    Los atributos del equipo, con su definicion y su valor.

    Se ve lo que esta DEFINIDO para la categoria (con su tipo y sus
    opciones) y lo que esta CARGADO: un atributo sin valor sale con
    `valor: null`, para que el operador sepa que falta cargarlo y no que
    no existe.
    """
    from src.modules.devices import atributos as atributos_service

    try:
        return {"device_id": device_id, "atributos": atributos_service.atributos_de(device_id)}
    except atributos_service.EquipoNoEncontrado as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put(
    "/{device_id}/atributos",
    summary="Cargar los atributos de un equipo",
)
def guardar_atributos(
    device_id: int,
    pedido: GuardarAtributosRequest,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Carga los atributos del equipo.

    Es PARCIAL: los atributos que vienen se guardan, los que no vienen se
    quedan como estaban. Y un atributo con valor `null` se borra.

    Se valida cada valor contra la definicion de la categoria: un numero
    donde va un numero, y un valor de la lista cuando el atributo tiene
    opciones. Si uno esta mal, NO se guarda ninguno: una ficha a medias es
    peor que una ficha sin tocar.
    """
    from src.modules.devices import atributos as atributos_service

    try:
        atributos_service.guardar(device_id, pedido.valores, medido_at=pedido.medido_at)
    except atributos_service.EquipoNoEncontrado as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except atributos_service.AtributoInvalido as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return {"device_id": device_id, "atributos": atributos_service.atributos_de(device_id)}


@router.get(
    "/atributos/{clave}/{valor}",
    summary="Los equipos que tienen un atributo con ese valor",
)
def buscar_por_atributo(
    clave: str,
    valor: str,
    _usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_VER)),
) -> dict:
    """
    "Todas las camaras con la IP 10.0.0.5", "los equipos con 8 GB de RAM".

    Es la pregunta que justifica que los valores esten indexados.
    """
    from src.modules.devices import atributos as atributos_service

    try:
       Devices = atributos_service.buscar_por_atributo(clave, valor)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"clave": clave, "valor": valor, "items": Devices}


@router.delete(
    "/{device_id}",
    summary="Dar de baja un componente (borrado logico)",
)
def eliminar(
    device_id: int,
    pedido: BajaDispositivoRequest | None = None,
    usuario: UsuarioActual = Depends(exigir(Permiso.DISPOSITIVOS_EDITAR)),
) -> dict:
    """
    Baja lógica: el equipo desaparece del inventario activo y queda auditado.

    NO se borra la fila. Un `DELETE` físico se lleva puesto el historial de
    movimientos y deja el inventario sin trazabilidad. `deleted_at` lo saca de
    las consultas y el `disposal_certificates` guarda el motivo y el responsable.
    """
    try:
        return service.dar_de_baja(
            device_id,
            usuario_id=usuario.id,
            motivo=(pedido.motivo if pedido else None),
        )
    except service.DispositivoNoEncontrado as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
