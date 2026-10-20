"""
Analisis y clasificacion de dispositivos.

QUE HACE
--------
Recibe un `device_id` y devuelve una clasificacion cerrada (excelente...critico),
un puntaje de riesgo de 0 a 100 y las razones. No inventa una escala nueva cada
vez: la escala es FIJA y esta en el prompt, y el modelo tiene que elegir de ahi.

POR QUE UN PUNTAJE Y NO SOLO UNA FRASE
-------------------------------------
Una frase como "este equipo deberia revisarse" no se puede ordenar ni comparar
entre 400 equipos. Un numero del 0 al 100 se puede: se ordena, se agrupa, se
muestra en un semaforo. El texto queda como explicacion DEL numero, no al
revés.

QUE DATOS SE LE MANDAN
---------------------
Solo agregados. Un SELECT con COUNT y AVG sobre el historial del equipo y de su
mismo modelo. NO se mandan las filas de movimientos, ni los nombres de los
clientes, ni los de los técnicos.

La excepción son los `initial_diagnosis` del taller, y estan detras de un
parametro (`incluir_diagnosticos`, True por defecto) porque son el unico dato
de este endpoint que no es una cifra, y sin ellos el modelo no puede decir por
que un equipo esta roto. Son textos redactados por tecnicos internos, no datos
de clientes.

LO QUE NO PUEDE HACER
---------------------
No puede predecir una falla futura. El modelo no tiene datos historicos de
fallas RESUELTAS con las que aprender: ve "este equipo lleva 3 visitas al taller",
no "a estos equipos les pasa X cada N meses". Lo que hace es un JUICIO
EXPERTO sobre indicadores, no una prediccion estadistica. El prompt lo dice, y
el nombre del campo es `clasificacion`, no `prediccion`.
"""

import json
import re
from typing import Any

from sqlalchemy import text

from src.core.config import get_settings
from src.core.llm import LLMError, LLMProvider, get_llm
from src.modules.ai.schemas import AnalisisDispositivoResponse

SYSTEM_ANALISIS = """Sos un tecnico senior de un taller de mantenimiento de dispositivos.

Recibes el estado de UN equipo y tenes que evaluarlo.

Respondé UNICAMENTE en JSON, con esta forma exacta:
{
  "clasificacion": "excelente" | "bueno" | "regular" | "malo" | "urgente" | "critico",
  "puntaje_riesgo": <entero 0-100>,
  "nivel_riesgo": "bajo" | "medio" | "alto" | "critico",
  "confianza": "baja" | "media" | "alta",
  "razonamiento": "dos o tres frases",
  "factores": ["señal concreta 1", "señal concreta 2"],
  "recomendaciones": ["accion concreta 1", "accion concreta 2"]
}

ESCALA DE RIESGO (0 = nada mal, 100 = sacar del servicio ya)
- 0-15   excelente: recien ingresado o sin actividad, sin fallas
- 16-30  bueno: uso normal, mantenimiento al dia
- 31-45  regular: hay que vigilarlo, alguna señal negativa
- 46-60  malo: multiple señal negativa o una grave
- 61-80  urgente: requiere intervencion pronta
- 81-100 critico: no debe volver a servicio sin reparacion

REGLAS
- El puntaje tiene que ser coherente con la clasificacion que elegiste.
- Los factores y las recomendaciones tienen que citar numbers que esten en las
  metricas que te pasaron. No inventes datos.
- Si las metricas son pocas o el equipo no tiene historial, la confianza es
  "baja" y lo decís en el razonamiento.
- NO estas prediciendo una falla futura: no tenés datos historicos de fallas
  resueltas. Estás evaluando el estado actual con criterio tecnico. No digas
  "va a fallar"; decí "requiere atencion" y por que.
- JSON puro. Sin texto antes ni despues.
"""


class DispositivoNoEncontrado(LookupError):
    pass


def analizar_dispositivo(
    device_id: int,
    incluir_diagnosticos: bool = True,
    llm: LLMProvider | None = None,
) -> AnalisisDispositivoResponse:
    get_settings()
    llm = llm or get_llm()

    metricas, encabezado = obtener_metricas(device_id, incluir_diagnosticos)

    prompt = (
        f"{json.dumps(encabezado, ensure_ascii=False, indent=2)}\n\n"
        f"{json.dumps(metricas, ensure_ascii=False, indent=2)}\n\n"
        "Evaluá este equipo y respondé en JSON."
    )

    try:
        respuesta = llm.chat(prompt, system=SYSTEM_ANALISIS, json_mode=True)
    except LLMError as exc:
        # Un modelo chico ignora json_mode con frecuencia. No es motivo para
        # devolver 502: se reintenta sin pedir formato.
        try:
            respuesta = llm.chat(prompt, system=SYSTEM_ANALISIS, json_mode=False)
        except LLMError:
            raise RuntimeError(f"El proveedor de IA no respondio: {exc}") from exc

    evaluacion = _parsear_json(respuesta.text)

    return AnalisisDispositivoResponse(
        device_id=device_id,
        serial_number=encabezado.get("serial_number", "?"),
        estado_actual=encabezado.get("current_status", "?"),
        clasificacion=evaluacion.get("clasificacion", "regular"),
        puntaje_riesgo=_acotar(evaluacion.get("puntaje_riesgo"), 0, 100),
        nivel_riesgo=evaluacion.get("nivel_riesgo", "medio"),
        confianza=evaluacion.get("confianza", "media"),
        razonamiento=evaluacion.get("razonamiento", ""),
        factores=_lista(evaluacion.get("factores")),
        recomendaciones=_lista(evaluacion.get("recomendaciones")),
        metricas=metricas,
        modelo=respuesta.model_used,
        proveedor=llm.name,
        latencia_ms=respuesta.latency_ms,
    )


def obtener_metricas(
    device_id: int, incluir_diagnosticos: bool
) -> "tuple[dict[str, Any], dict[str, Any]]":
    """
    Trae las cifras agregadas del equipo. Un solo SELECT.

    OJO con el FROM: el equipo se busca por `devices.id`, no por
    `serial_number`. Buscarlo por el numero de serie que manda el cliente
    significa que un `int` mal tipeado devuelve el analisis del equipo equivocado
    sin ningun error visible.
    """
    from src.modules.ai.executor import get_ai_engine

    diagnosticos = (
        "COALESCE((SELECT array_agg(initial_diagnosis) FROM repairs "
        " WHERE device_id = d.id AND initial_diagnosis IS NOT NULL), '{}') AS diagnosticos"
        if incluir_diagnosticos
        # Sin diagnosticos se manda un array VACIO, no la columna: es la unica
        # forma de que el modelo no la espere y no se queje de que falta.
        else "'{}'::text[] AS diagnosticos"
    )

    consulta = f"""
        SELECT
            d.serial_number,
            d.current_status,
            d.installation_date,
            d.warehouse_entry_date,
            d.created_at,
            EXTRACT(EPOCH FROM (NOW() - COALESCE(d.installation_date, d.warehouse_entry_date, d.created_at)))
                / 86400.0 AS dias_en_servicio,
            mo.brand,
            mo.model,
            mo.warranty_months,
            c.name AS categoria,
            COALESCE(l.store_name, 'Almacen central') AS ubicacion_actual,
            (SELECT count(*) FROM repairs r WHERE r.device_id = d.id) AS reparaciones_totales,
            (SELECT count(*) FROM repairs r WHERE r.device_id = d.id
                AND r.status NOT IN ('REPAIRED', 'UNREPAIRABLE')) AS reparaciones_abiertas,
            (SELECT count(*) FROM repairs r WHERE r.device_id = d.id
                AND r.status = 'WAITING_FOR_PARTS') AS esperando_repuestos,
            (SELECT count(*) FROM movement_history mh WHERE mh.device_id = d.id) AS movimientos_totales,
            (SELECT count(*) FROM movement_history mh
                JOIN movement_types mt ON mt.id = mh.movement_type_id
                WHERE mh.device_id = d.id
                AND mt.name = 'REPLACEMENT') AS reemplazos,
            (SELECT count(*) FROM movement_history mh WHERE mh.device_id = d.id
                AND mh.is_valid_location = FALSE) AS escaneos_fuera_de_radio,
            (SELECT count(*) FROM movement_history mh WHERE mh.device_id = d.id
                AND mh.replacement_reason IS NOT NULL) AS motivos_de_reemplazo,
            -- Tasa de falla del MISMO modelo en la flota: si el 40% de los
            -- Galaxy A10 del parque pasa por el taller, este equipo esta en un
            -- modelo problemático, y eso cambia el diagnostico por completo.
            (SELECT count(*) FROM repairs r2
                JOIN devices d2 ON d2.id = r2.device_id
              WHERE d2.model_id = d.model_id) AS reparaciones_del_mismo_modelo,
            (SELECT count(*) FROM devices d3
              WHERE d3.model_id = d.model_id AND d3.deleted_at IS NULL) AS flota_del_mismo_modelo,
            -- Dias desde la ultima visita al taller. Un equipo que no vuelve
            -- hace 2 años no esta "sano", esta "abandonado".
            --
            -- El / 86400.0 no es cosmetico: EXTRACT(EPOCH ...) devuelve
            -- SEGUNDOS. Sin la division, un equipo reparado hace 3 meses
            -- reportaba 1036849 dias, y el modelo leia eso como "esta en el
            -- taller desde antes de Cristo" — un numero tan absurdo que no
            -- sesa en ninguna conclusion, pero se cuela igual en el texto.
            COALESCE((SELECT EXTRACT(EPOCH FROM (NOW() - MAX(r3.entry_date))) / 86400.0
                FROM repairs r3 WHERE r3.device_id = d.id), NULL) AS dias_desde_ultima_reparacion,
            {diagnosticos}
        FROM devices d
        LEFT JOIN models mo     ON mo.id = d.model_id
        LEFT JOIN categories c ON c.id = mo.category_id
        LEFT JOIN locations l  ON l.id = d.current_location_id
        WHERE d.id = :device_id AND d.deleted_at IS NULL
    """

    with get_ai_engine().connect() as conexion:
        fila = (
            conexion.execute(text(consulta), {"device_id": device_id})
            .mappings()
            .first()
        )

    if fila is None:
        raise DispositivoNoEncontrado(
            f"No existe un dispositivo activo con id={device_id}."
        )

    # `encabezado` es lo que va al prompt. No incluye el cliente: un JOIN a
    # `clients` no esta porque el nombre comercial no aporta nada al diagnostico
    # tecnico y es dato del cliente. La ubicacion entra, que si es relevante.
    encabezado = {
        "serial_number": fila["serial_number"],
        "current_status": fila["current_status"],
        "marca": fila["brand"],
        "modelo": fila["model"],
        "categoria": fila["categoria"],
        "ubicacion": fila["ubicacion_actual"],
    }

    metricas: dict[str, Any] = {
        "dias_en_servicio": _entero(fila["dias_en_servicio"]),
        "meses_de_garantia": fila["warranty_months"],
        "fecha_instalacion": _texto(fila["installation_date"]),
        "reparaciones_totales": fila["reparaciones_totales"],
        "reparaciones_abiertas": fila["reparaciones_abiertas"],
        "esperando_repuestos": fila["esperando_repuestos"],
        "dias_desde_ultima_reparacion": _entero(fila["dias_desde_ultima_reparacion"]),
        "movimientos_totales": fila["movimientos_totales"],
        "reemplazos": fila["reemplazos"],
        "escaneos_fuera_de_radio": fila["escaneos_fuera_de_radio"],
        "reparaciones_del_mismo_modelo_en_la_flota": fila[
            "reparaciones_del_mismo_modelo"
        ],
        "flota_del_mismo_modelo": fila["flota_del_mismo_modelo"],
        "diagnosticos_del_taller": fila["diagnosticos"] or [],
    }

    # La tasa de falla de la flota se calcula ACA y no se le pide al modelo que
    # la divida. Un modelo chico divide mal, y un porcentaje mal calculado en la
    # conclusion tecnica es peor que no tenerlo.
    flota = fila["flota_del_mismo_modelo"] or 0
    reparadas = fila["reparaciones_del_mismo_modelo"] or 0
    metricas["tasa_reparacion_del_modelo"] = (
        round(reparadas / flota, 3) if flota else None
    )

    return metricas, encabezado


# ---------------------------------------------------------------------------
# Parseo defensivo de la respuesta
# ---------------------------------------------------------------------------
# Un modelo chico, cuando se le pide JSON, devuelve JSON con corchetes de mas,
# con un ```json alrededor, o mezclado con una frase. Todo eso se acepta; lo
# que no se acepta es reventar el endpoint por eso.


def _parsear_json(texto: str) -> dict[str, Any]:
    # 1. JSON puro, tal cual.
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    # 2. El objeto mas grande que haya dentro del texto. Un ```json``` de un
    #    modelo de 3b suele venir con una linea de prosa antes.
    bloque = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", texto, re.DOTALL)
    candidato = bloque.group(1) if bloque else None

    if candidato is None:
        primer = texto.find("{")
        ultimo = texto.rfind("}")
        if primer != -1 and ultimo > primer:
            candidato = texto[primer : ultimo + 1]

    if candidato:
        try:
            return json.loads(candidato)
        except json.JSONDecodeError:
            pass

    # 3. Ultimo recurso: el modelo no devolvio JSON. Se devuelve una evaluacion
    #    vacia con su texto crudo en el razonamiento. Perder la clasificacion
    #    automatica es molesto; perder el analisis entero es peor.
    return {
        "clasificacion": "regular",
        "puntaje_riesgo": 40,
        "nivel_riesgo": "medio",
        "confianza": "baja",
        "razonamiento": texto.strip()[:800] or "El modelo no devolvio un analisis.",
        "factores": [],
        "recomendaciones": [],
    }


def _acotar(valor: Any, minimo: int, maximo: int) -> int:
    """El modelo puede devolver 95.5, 'alto' o -3. Se lleva a un entero acotado."""
    try:
        numero = round(float(valor))
    except (TypeError, ValueError):
        return 40
    return max(minimo, min(maximo, numero))


def _lista(valor: Any) -> list[str]:
    """Acepta lista, string unico o None. Devuelve lista de strings."""
    if valor is None:
        return []
    if isinstance(valor, str):
        return [valor.strip()] if valor.strip() else []
    if isinstance(valor, list):
        return [str(v).strip() for v in valor if str(v).strip()]
    return [str(valor)]


def _entero(valor: Any) -> int | None:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def _texto(valor: Any) -> str | None:
    return valor.isoformat() if valor is not None else None
