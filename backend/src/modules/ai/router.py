"""
Router de la API de IA. Montado en /api/ai.

POR QUE TODOS LOS ENDPOINTS DEVUELVEN 200 CON UN CAMPO `error`
-------------------------------------------------------------
Un error del proveedor de IA no es un error del cliente que pregunto. Si
Ollama está caido y el endpoint devuelve 502, el frontend lo muestra como "algo
salió mal" y el usuario piensa que su pregunta estaba mal. Con 200 y `error`
puesto, el frontend puede decir "no pude consultar, reintenta" y el problema es
visible.

La Excepción son los 4xx de entrada inválida (pregunta vacía, id de
dispositivo que no existe, SQL rechazado): esos si son culpa de quien llama.

El SQL rechazado va en 400 y NO en 200 con error, aunque se pueda. Si el modelo
intento algo que no puede hacer, el operador tiene que enterarse: un 200 con el
detalle adentro lo esconde.
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from src.core.config import get_settings
from src.core.deps import exigir, get_current_user
from src.core.llm import LLMError, get_llm
from src.core.permissions import Permiso
from src.modules.ai import analyzer, report, sql_agent
from src.modules.ai.schemas import (
    AnalisisDispositivoResponse,
    EstadoIA,
    PreguntaRequest,
    ReporteRequest,
    ReporteResponse,
    RespuestaIA,
)
from src.modules.users.models import UsuarioActual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ia"])


@router.post(
    "/preguntar",
    response_model=RespuestaIA,
    summary="Pregunta en lenguaje natural sobre el inventario",
)
def preguntar(
    pedido: PreguntaRequest,
    _usuario: UsuarioActual = Depends(exigir(Permiso.IA_PREGUNTAR)),
) -> RespuestaIA:
    """
    El modelo escribe un SELECT, se válida, se ejecuta contra Postgres y se
    devuelve la filas con una explicacion.

    Lo que sale del servidor nunca sale de las columnas vetadas en `guard.py`
    (`password`, `old_value`, `new_value`).
    """
    llm = get_llm()

    try:
        resultado = sql_agent.responder_pregunta(
            pedido.pregunta,
            llm=llm,
            # El historial: sin el, las preguntas de seguimiento ("cual es ese
            # equipo que se encontro?") se responden con otra cosa.
            historial=[turno.model_dump() for turno in pedido.historial],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sql_agent.PreguntaNoRespondida as exc:
        # El modelo no pudo: se responde 200 con el error, no 502.
        return RespuestaIA(
            pregunta=pedido.pregunta,
            respuesta="",
            error=str(exc),
            proveedor=llm.name,
        )

    filas = resultado.get("filas", [])
    if len(filas) > pedido.max_filas:
        filas = filas[: pedido.max_filas]
        resultado = dict(resultado, truncado=True)

    return RespuestaIA(
        pregunta=resultado.get("pregunta"),
        respuesta=resultado.get("respuesta", ""),
        sql=resultado["sql"] if pedido.incluir_sql else None,
        datos=filas,
        columnas=resultado.get("columnas", []),
        cantidad_filas=resultado.get("cantidad_filas", 0),
        truncado=resultado.get("truncado", False),
        modelo=resultado.get("modelo"),
        proveedor=resultado.get("proveedor"),
        uso_fallback=resultado.get("usou_fallback", False),
        error=resultado.get("error"),
        sugerencia=resultado.get("sugerencia"),
        resumen=resultado.get("resumen"),
    )


@router.get(
    "/analisis/dispositivo/{device_id}",
    response_model=AnalisisDispositivoResponse,
    summary="Clasificacion y puntaje de riesgo de un equipo",
)
def analizar(
    device_id: int,
    incluir_diagnosticos: bool = True,
    _usuario: UsuarioActual = Depends(exigir(Permiso.IA_ANALIZAR)),
) -> AnalisisDispositivoResponse:
    """
    Evalua UN equipo sobre métricas agregadas de su historial.

    No predice fallas futuras: no hay datos historicos de fallas resueltas con
    los que aprender. Es un juicio experto sobre indicadores, que es lo que el
    modelo puede sostener.
    """
    try:
        return analyzer.analizar_dispositivo(
            device_id,
            incluir_diagnosticos=incluir_diagnosticos,
            llm=get_llm(),
        )
    except analyzer.DispositivoNoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        # El proveedor cayo y no habia fallback. Si es un error de
        # infraestructura, se dice con un 503 explicito: reintentar tiene
        # sentido y el cliente debe saberlo.
        logger.warning("analisis/dispositivo sin proveedor: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/reporte",
    response_model=ReporteResponse,
    summary="Reporte operativo en lenguaje natural",
)
def generar_reporte(
    pedido: ReporteRequest,
    _usuario: UsuarioActual = Depends(exigir(Permiso.IA_REPORTE)),
) -> ReporteResponse:
    """
    Reporte con los Números de una consulta fija y la glosa del modelo.

    El SQL no lo escribe el modelo: eso es lo que hace que el mismo reporte dé
    el mismo número todos los días.
    """
    return report.generar_reporte(
        alcance=pedido.alcance,
        periodo=pedido.periodo,
        tipo=pedido.tipo,
        llm=get_llm(),
    )


@router.get(
    "/alcances",
    summary="Recortes de reporte disponibles",
)
def alcances(_: UsuarioActual = Depends(get_current_user)) -> dict:
    """Para que el frontend no tenga que adivinar que valores acepta `alcance`."""
    return {
        "periodos": report.PERIODOS,
        "alcances": report.listar_alcances(),
    }


@router.get(
    "/health",
    response_model=EstadoIA,
    summary="Estado del proveedor de IA",
)
def health_ia(_: UsuarioActual = Depends(get_current_user)) -> EstadoIA:
    """
    Estado del LLM. NUNCA lanza, ni siquiera si el proveedor está caido: su
    trabajo es justamente ese.

    Este endpoint NO va en el `/health` general de la API a propósito. Si la IA
    no responde, el servicio de inventario tiene que seguir Considerado
    saludable: el healthcheck del contenedor no puede depender de que haya un
    modelo cargado en la máquina.
    """
    config = get_settings()

    # Construir el provider puede fallar ANTES de poder reportar: una config
    # inválida, una API key faltante, un import roto. Este endpoint existe
    # justamente para diagnosticar eso, así que si `get_llm()` lanza, tiene que
    # devolver el motivo, no un 500.
    #
    # Se probó: con un typo en el nombre de una variable de Settings,
    # `/api/ai/health` devolvio 500Internal Server Error y no dijo nada. El
    # único sintoma era una traza en los logs del contenedor.
    try:
        llm = get_llm()
    except Exception as exc:  # noqa: BLE001 - este endpoint no propaga NUNCA
        logger.error("No se pudo construir el proveedor de IA: %s", exc)
        return EstadoIA(
            disponible=False,
            respondio=False,
            proveedor=config.llm_provider,
            modelo_configurado=(
                config.ollama_model if config.llm_is_local else config.openai_model
            ),
            modelo_fallback=config.ollama_fallback_model
            if config.llm_is_local
            else None,
            detalle=f"La configuracion del proveedor es invalida: {exc}",
            host_remoto=config.ollama_base_url
            if config.llm_is_local
            else config.openai_base_url,
        )

    modelos_disponibles: list[str] = []
    try:
        modelos_disponibles = llm.list_models()
    except Exception:  # noqa: BLE001 - reportar, no propagar
        pass

    inicio = time.monotonic()
    try:
        # "ping" con el schema mínimo: probar el modelo entero con una pregunta
        # real tardaria segundos y este endpoint se consulta seguido.
        llm.chat(
            "Responde exactamente: OK",
            system="Respondes con una sola palabra.",
            temperature=0.0,
        )
        respondio, detalle = True, None
    except LLMError as exc:
        respondio, detalle = False, str(exc)

    latencia_ms = int((time.monotonic() - inicio) * 1000)

    return EstadoIA(
        # "disponible" es configuración (hay proveedor configurado);
        # "respondió" es realidad (el modelo contesto). Son dos cosas y
        # confundirlas hace que un healthcheck verde sea mentira.
        disponible=True,
        respondio=respondio,
        proveedor=config.llm_provider,
        modelo_configurado=(
            config.ollama_model if config.llm_is_local else config.openai_model
        ),
        modelo_fallback=config.ollama_fallback_model if config.llm_is_local else None,
        detalle=detalle,
        latencia_ms=latencia_ms,
        host_remoto=config.ollama_base_url
        if config.llm_is_local
        else config.openai_base_url,
        modelos_disponibles=modelos_disponibles[:20],
    )
