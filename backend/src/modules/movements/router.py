"""
Router de movimientos. Montado en /api/movements.

LA REGLA QUE GOBIERNA ESTE ARCHIVO
----------------------------------
Un escaneo cambia el ESTADO de un componente y escribe su HISTORIAL. Las dos
cosas o pasan juntas o no pasan.

No es una preferencia de diseño: es la diferencia entre un inventario del que se
puede decir "el equipo 47 está instalado en Acme desde marzo" y uno donde el
historial dice una cosa, la pantalla dice otra y ninguno de los dos sabe cual
miente. Toda la lógica de transición está en `service.escanear()`, y el
`IntegrityError` de la base es la última línea de defensa.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.core.deps import exigir
from src.core.geo import CoordenadaInvalida
from src.core.permissions import Permiso
from src.modules.movements import service
from src.modules.movements.schemas import (
    EscanearLoteRequest,
    EscanearRequest,
    EscanearResponse,
    HistorialMovimiento,
    ListaMovimientosResponse,
    MovimientoPorProductoRequest,
    MovimientoPorProductoResponse,
    MovimientoTipoResponse,
)
from src.modules.users.models import UsuarioActual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/movements", tags=["movimientos"])


# Metadatos de tipos de movimiento para UI (descripciones, íconos, etc.)
METADATOS_MOVIMIENTO = {
    "WAREHOUSE_ENTRY": {
        "texto": "Entrada a deposito",
        "descripcion": "Nuevo equipo ingresa al deposito",
        "icono": "buscar",
        "estado_destino": "IN_WAREHOUSE",
        "requiere_reemplazo": False,
    },
    "WAREHOUSE_REENTRY": {
        "texto": "Devolucion a deposito",
        "descripcion": "El equipo vuelve al deposito desde el cliente",
        "icono": "refrescar",
        "estado_destino": "IN_WAREHOUSE",
        "requiere_reemplazo": False,
    },
    "OUT_TO_CLIENT": {
        "texto": "Salida a cliente",
        "descripcion": "El equipo sale del deposito hacia el cliente",
        "icono": "buscar",
        "estado_destino": "IN_TRANSIT",
        "requiere_reemplazo": False,
    },
    "INSTALLATION": {
        "texto": "Instalacion",
        "descripcion": "El equipo se instala en sitio del cliente",
        "icono": "ubicacion",
        "estado_destino": "INSTALLED",
        "requiere_reemplazo": False,
    },
    "REPLACEMENT": {
        "texto": "Reemplazo",
        "descripcion": "Se reemplaza un equipo por otro",
        "icono": "refrescar",
        "estado_destino": "IN_TRANSIT",
        "requiere_reemplazo": True,
    },
    "WITHDRAWAL_TO_WORKSHOP": {
        "texto": "Retiro a taller",
        "descripcion": "El equipo va a reparacion",
        "icono": "relojito",
        "estado_destino": "IN_REPAIR",
        "requiere_reemplazo": False,
    },
    "DISPOSAL": {
        "texto": "Baja definitiva",
        "descripcion": "El equipo se da de baja permanentemente",
        "icono": "error",
        "estado_destino": "PERMANENTLY_DISPOSED",
        "requiere_reemplazo": False,
    },
}


@router.get(
    "/movement-types",
    response_model=list[MovimientoTipoResponse],
    summary="Listar tipos de movimiento disponibles",
)
def listar_tipos_movimiento(
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> list[MovimientoTipoResponse]:
    """
    Devuelve los tipos de movimiento con metadatos para UI.
    
    El frontend usa esto para construir los chips de selección en la vista
    de escaneo. Requiere permiso de crear movimientos (Operador o superior).
    """
    from src.modules.movements.service import ESTADO_POR_MOVIMIENTO, REQUIERE_REEMPLAZO
    
    tipos = []
    for valor, estado_destino in ESTADO_POR_MOVIMIENTO.items():
        meta = METADATOS_MOVIMIENTO.get(valor, {})
        tipos.append(
            MovimientoTipoResponse(
                valor=valor,
                texto=meta.get("texto", valor),
                descripcion=meta.get("descripcion", ""),
                icono=meta.get("icono", "buscar"),
                estado_destino=estado_destino,
                requiere_reemplazo=valor in REQUIERE_REEMPLAZO,
            )
        )
    
    # Ordenar por texto para UI consistente
    tipos.sort(key=lambda t: t.texto)
    return tipos


def _errores_de_escaneo(exc: Exception) -> HTTPException:
    """
    Traduce las excepciones del service a HTTP.

    Que sea una función y no un bloque `try/except` repetido en cada endpoint no
    es estilo: es que el mapeo de "que tipo de HTTP" tiene que ser el mismo en
    todos. Un endpoint que responde 500 donde otro responde 400 hace que el
    frontend tenga que manejar dos caminos para el mismo error.
    """
    if isinstance(exc, service.DispositivoNoEncontrado):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, service.CodigoDeProducto):
        # 409 con el producto y sus unidades: el frontend los muestra para que
        # el operador elija la unidad. El `detalle` es un objeto y no un
        # string, y es lo único en este router que devuelve algo así —por eso
        # el frontend lo busca por `detalle.producto` y no por el texto.
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"mensaje": str(exc), "producto": exc.producto},
        )
    if isinstance(exc, service.TransicionInvalida):
        # 409, no 400 ni 500: el pedido está BIEN formado, el conflicto es con
        # el estado actual del componente. 409 es exactamente eso, y lo que
        # recibe el operador es el motivo del trigger, no un "algo salió mal".
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (service.ErrorDeEscaneo, CoordenadaInvalida)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="No se pudo registrar el escaneo.",
    )


@router.post(
    "/escanear",
    response_model=EscanearResponse,
    summary="Registrar un escaneo (QR o serial)",
)
def escanear(
    pedido: EscanearRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> EscanearResponse:
    """
    El endpoint del lector de códigos.

    IDEMPOTENTE: si se repite el `client_scan_id`, no inserta nada y devuelve el
    movimiento original con `replay: true`. El cliente genera ese UUID antes de
    enviar y lo reutiliza en los reintentos.

    QUE PERMISO PIDE Y POR QUE NO ES `IA_*`
    --------------------------------------
    `MOVIMIENTOS_CREAR`, que es el del Operador. Registrar un escaneo es la
    operación principal de un operador en un depósito; si pidiera un permiso
    distinto, la app del celular no serviria para el rol que más la usa.
    """
    try:
        pedido.check_reemplazo()
        return service.escanear(
            codigo=pedido.codigo,
            tipo_movimiento=pedido.tipo_movimiento,
            client_scan_id=str(pedido.client_scan_id),
            user_id=str(usuario.id),
            ubicacion_id=pedido.ubicacion_id,
            latitude=pedido.latitude,
            longitude=pedido.longitude,
            replaced_device_id=pedido.replaced_device_id,
            replacement_reason=pedido.replacement_reason,
            observations=pedido.observations,
        )
    except (
        service.ErrorDeEscaneo,
        service.DispositivoNoEncontrado,
        service.CodigoDeProducto,
        service.TransicionInvalida,
        CoordenadaInvalida,
    ) as exc:
        raise _errores_de_escaneo(exc) from exc


@router.post(
    "/escanear-lote",
    response_model=dict,
    summary="Registrar varios escaneos en una request (conteo ciclico)",
)
def escanear_lote(
    pedido: EscanearLoteRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> dict:
    """
    Conteo de depósito: N códigos, una request.

    POR QUE UN `client_scan_id` POR Código Y NO UNO PARA EL LOTE
    -----------------------------------------------------------
    Porque si el lote se manda dos veces, la idempotencia tiene que funcionar
    igual. Con un único id para todo el lote, el segundo envió se descarta
    completo y los códigos queTodavia no estaban no se registran: el operador
    pierde un conteo sin enterarse.

    El `client_scan_id` de cada elemento se deriva del del lote con el índice:
    `uuid5(lote_id, str(índice))`. Es deterministico, así que el mismo lote con
    el mismo id genera los mismos ids y el reintento es un replay real.

    Un elemento que falla NO frena el lote: un conteo de 200 equipos en el que
    uno tiene la etiqueta sucia tiene que registrar los otros 199. Por eso cada
    resultado lleva su propio `error`.
    """
    lote_id = uuid.uuid4()
    resultados: list[dict] = []
    registrados = 0

    for indice, codigo in enumerate(pedido.codigos):
        # `uuid5` con el namespace del lote: deterministico, y distinto para
        # cada índice. Es la clave con la que un reintento se reconoce a si
        # mismo.
        client_scan_id = str(uuid.uuid5(lote_id, str(indice)))

        try:
            resultado = service.escanear(
                codigo=codigo,
                tipo_movimiento=pedido.tipo_movimiento,
                client_scan_id=client_scan_id,
                user_id=str(usuario.id),
                ubicacion_id=pedido.ubicacion_id,
                latitude=pedido.latitude,
                longitude=pedido.longitude,
            )
            resultados.append({"codigo": codigo, **resultado})
            if not resultado.get("replay"):
                registrados += 1
        except (
            service.ErrorDeEscaneo,
            service.DispositivoNoEncontrado,
            service.CodigoDeProducto,
            service.TransicionInvalida,
            CoordenadaInvalida,
        ) as exc:
            resultados.append({"codigo": codigo, "error": str(exc), "registrado": False})

    fallidos = sum(1 for r in resultados if r.get("error") or not r.get("movement_id"))

    return {
        "total": len(pedido.codigos),
        "registrados": registrados,
        "fallidos": fallidos,
        # El `lote_id` se devuelve para que el cliente pueda reenviar EL MISMO
        # lote y obtener el replay de cada elemento en vez de duplicarlos.
        "lote_id": str(lote_id),
        "resultados": resultados,
    }


@router.post(
    "/por-producto",
    response_model=MovimientoPorProductoResponse,
    summary="Mover N unidades de un producto (entrada, salida, transferencia, baja…)",
)
def mover_por_producto(
    pedido: MovimientoPorProductoRequest,
    usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_CREAR)),
) -> MovimientoPorProductoResponse:
    """
    Mueve mercancia por producto y cantidad, sin escanear serial por serial.

    Es el endpoint que usa la vista "Movimientos": el operador elige un producto
    del maestro, una cantidad y la operación (entrada, salida a cliente,
    instalacion, transferencia entre sedes, retiro a taller, devolucion o baja),
    y el backend elige los equipos y los mueve.

    Un equipo que no se pueda mover NO frena el lote: se registra su error y los
    demas siguen. Un conteo de 20 en el que uno está en taller tiene que mover
    los otros 19.
    """
    try:
        return service.mover_por_producto(
            model_id=pedido.model_id,
            cantidad=pedido.cantidad,
            tipo_movimiento=pedido.tipo_movimiento,
            user_id=str(usuario.id),
            ubicacion_id=pedido.ubicacion_id,
            ubicacion_origen_id=pedido.ubicacion_origen_id,
            observations=pedido.observations,
        )
    except service.ErrorDeEscaneo as exc:
        # No hay stock en el estado de origen, o el movimiento no aplica por
        # producto. Es una regla de negocio: 409 con el motivo.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "",
    response_model=ListaMovimientosResponse,
    summary="Listar movimientos",
)
def listar(
    device_id: int | None = Query(None, gt=0),
    desde: str | None = Query(
        None,
        description="Fecha en ISO. Ej: 2026-01-01T00:00:00Z",
        max_length=40,
    ),
    hasta: str | None = Query(
        None,
        description="Fecha final en ISO. Ej: 2026-12-31T23:59:59Z",
        max_length=40,
    ),
    busqueda: str | None = Query(
        None,
        max_length=100,
        description="Texto libre: serial, QR, SKU, marca, modelo o ubicacion.",
    ),
    tipo_movimiento: str | None = Query(
        None,
        max_length=40,
        description="Codigo del tipo de movimiento (por ejemplo: ENTRADA).",
    ),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> ListaMovimientosResponse:
    """
    La actividad, paginada y con buscador.

    Sin buscador, con 5000 movimientos la pantalla muestra los últimos 50 y
    no hay forma de llegar a los de ayer: el historial deja de ser un
    historial y pasa a ser las últimas noticias.
    """
    fecha_desde = None
    if desde:
        from datetime import datetime

        try:
            fecha_desde = datetime.fromisoformat(desde.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La fecha no es válido. Usa ISO 8601: 2026-01-01T00:00:00Z",
            ) from exc

    fecha_hasta = None
    if hasta:
        from datetime import datetime

        try:
            fecha_hasta = datetime.fromisoformat(hasta.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La fecha no es válido. Usa ISO 8601: 2026-12-31T23:59:59Z",
            ) from exc

    return service.listar_movimientos(
        device_id=device_id,
        desde=fecha_desde,
        hasta=fecha_hasta,
        busqueda=busqueda,
        tipo_movimiento=tipo_movimiento,
        limite=limite,
        offset=offset,
    )


@router.get(
    "/dispositivo/{device_id}",
    response_model=list[HistorialMovimiento],
    summary="Donde estuvo un componente",
)
def historial(
    device_id: int,
    limite: int = Query(50, ge=1, le=200),
    _usuario: UsuarioActual = Depends(exigir(Permiso.MOVIMIENTOS_VER)),
) -> list[HistorialMovimiento]:
    """
    El recorrido de un componente, del más reciente al más antiguo.

    Es el endpoint que contesta "donde estuvo este equipo", que es la pregunta
    que se hace cuando algo no aparece. Por eso trae el nombre de la persona
    que registro cada movimiento y las coordenadas del escaneo: no alcanza con
    saber que el equipo se movio, hay que saber quien lo movio y desde donde.
    """
    return service.historial_dispositivo(device_id, limite=limite)
