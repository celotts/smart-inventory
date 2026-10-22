"""
Los atributos POR EQUIPO.

QUE SON
-------
Los valores de los campos que define la categoria del producto, pero de
CADA equipo y no del modelo.

POR QUE HACE FALTA
------------------
`models.specifications` guarda el valor por MODELO: dos notebooks del mismo
modelo comparten la especificacion. Pero "la notebook de la recepcion tiene
16 GB de RAM" y "la del laboratorio tiene 8 GB" son el mismo modelo con
valores distintos, y con el valor solo por modelo la segunda no se puede
guardar: o se miente en la ficha, o se pierde el dato.

Es tambien lo que hace falta para los equipos que no son de escritorio: una
camara IP con su direccion de gestion, un NVR con su cantidad de canales, un
UPS con su autonomia. Todos comparten modelo y ninguno comparte valores.

EL TIPO LO MANDA LA DEFINICION
------------------------------
El tipo de cada atributo (`category_attributes.data_type`) es el que dice
como se valida el valor: un numero, un texto, un booleano, una fecha o uno
de las opciones. El valor se guarda en JSONB, en la forma que corresponde a
ese tipo.

Y si el atributo TIENE OPCIONES, el valor tiene que ser una de ellas: un
"estado" con un valor inventado rompe cualquier consulta por estado.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import get_settings


class AtributoInvalido(ValueError):
    """Un valor que no cumple la definicion del atributo."""


class EquipoNoEncontrado(LookupError):
    """El equipo no existe."""


# Los tipos que acepta `category_attributes.data_type` (el CHECK de la base
# no deja mas), y como se valida el valor de cada uno.
#
# `select` no esta en la lista porque sus valores posibles estan en
# `attribute_values`, y se validan aparte.
TIPOS = {
    "text": str,
    "number": (int, float),
    "boolean": bool,
    "date": date,
}


def _motor():
    from src.db.session import get_engine

    return get_engine()


def _definiciones_de(categoria_id: int) -> dict[str, dict[str, Any]]:
    """
    Los atributos definidos para una categoria, por clave.

    Con sus opciones (si las tiene) y su tipo.
    """
    motor = _motor()
    with motor.connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT ca.key,
                       ca.label,
                       ca.data_type,
                       ca.options,
                       ca.unit,
                       ca.is_required,
                       ca.applies_to
                FROM category_attributes ca
                WHERE ca.category_id = :categoria_id
                  AND ca.is_active = TRUE
                ORDER BY ca.sort_order, ca.id
                """
            ),
            {"categoria_id": categoria_id},
        ).mappings().all()

    definiciones: dict[str, dict[str, Any]] = {}
    for fila in filas:
        definiciones[fila["key"]] = {
            "key": fila["key"],
            "label": fila["label"],
            "data_type": (fila["data_type"] or "text").lower(),
            "options": fila["options"] or [],
            "unit": fila["unit"],
            "is_required": bool(fila["is_required"]),
            "applies_to": fila["applies_to"],
        }
    return definiciones


def _opciones_de(categoria_id: int) -> dict[str, list[dict[str, Any]]]:
    """Las opciones posibles de cada atributo con lista."""
    motor = _motor()
    with motor.connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT ca.key,
                       av.value,
                       av.code,
                       av.sort_order
                FROM attribute_values av
                JOIN category_attributes ca ON ca.id = av.category_attribute_id
                WHERE ca.category_id = :categoria_id
                  AND ca.is_active = TRUE
                  AND av.is_active = TRUE
                ORDER BY ca.key, av.sort_order, av.id
                """
            ),
            {"categoria_id": categoria_id},
        ).mappings().all()

    opciones: dict[str, list[dict[str, Any]]] = {}
    for fila in filas:
        opciones.setdefault(fila["key"], []).append(
            {"valor": fila["value"], "codigo": fila["code"]}
        )
    return opciones


def _validar(
    clave: str,
    valor: Any,
    definiciones: dict[str, dict[str, Any]],
    opciones: dict[str, list[dict[str, Any]]],
) -> Any:
    """
    Valida un valor contra la definicion de su atributo.

    Devuelve el valor en la forma que se guarda (el JSON ya convertido).
    """
    definicion = definiciones.get(clave)
    if definicion is None:
        raise AtributoInvalido(
            f"El atributo '{clave}' no está definido para esta categoría."
        )

    tipo = definicion["data_type"]

    # Los atributos con OPCIONES: el valor tiene que ser una de ellas.
    if clave in opciones:
        permitidos = [o["codigo"] for o in opciones[clave]]
        if valor not in permitidos:
            # Se acepta tambien el TEXTO de la opcion (el operador lo ve y
            # lo escribe), pero se guarda el codigo.
            por_texto = {o["valor"]: o["codigo"] for o in opciones[clave]}
            if valor in por_texto:
                return por_texto[valor]
            raise AtributoInvalido(
                f"'{valor}' no es un valor válido para '{clave}'. "
                f"Valores: {', '.join(o['valor'] for o in opciones[clave])}."
            )
        return valor

    if valor is None:
        return None

    if tipo == "boolean":
        if isinstance(valor, bool):
            return valor
        if str(valor).lower() in ("true", "1", "si", "sí", "yes"):
            return True
        if str(valor).lower() in ("false", "0", "no"):
            return False
        raise AtributoInvalido(f"'{clave}' tiene que ser verdadero o falso.")

    if tipo == "number":
        try:
            return float(valor)
        except (TypeError, ValueError) as exc:
            raise AtributoInvalido(f"'{clave}' tiene que ser un número.") from exc

    if tipo == "date":
        if isinstance(valor, (date, datetime)):
            return valor.isoformat()
        try:
            return date.fromisoformat(str(valor)).isoformat()
        except ValueError as exc:
            raise AtributoInvalido(f"'{clave}' tiene que ser una fecha (AAAA-MM-DD).") from exc

    return str(valor)


def atributos_de(device_id: int) -> list[dict[str, Any]]:
    """
    Los atributos de un equipo, con la definicion y el valor.

    Cada elemento trae:
      - `key`, `label`, `data_type`, `unit`: la definicion.
      - `valor`: el valor guardado (o `None` si no se cargo).
      - `opciones`: las opciones posibles, si las tiene.
      - `opciones_validas`: si el valor actual es una de ellas.
    """
    motor = _motor()
    with motor.connect() as conexion:
        equipo = conexion.execute(
            text(
                """
                SELECT d.id, d.model_id, m.category_id
                FROM devices d
                JOIN models m ON m.id = d.model_id
                WHERE d.id = :device_id AND d.deleted_at IS NULL
                """
            ),
            {"device_id": device_id},
        ).mappings().first()

        if equipo is None:
            raise EquipoNoEncontrado(f"El equipo {device_id} no existe.")

        definiciones = _definiciones_de(equipo["category_id"])
        opciones = _opciones_de(equipo["category_id"])

        filas = conexion.execute(
            text(
                """
                SELECT key, valor, medido_at
                FROM device_attributes
                WHERE device_id = :device_id
                """
            ),
            {"device_id": device_id},
        ).mappings().all()

    guardados = {f["key"]: f for f in filas}

    resultado = []
    for clave, definicion in definiciones.items():
        fila = guardados.get(clave)
        valor = fila["valor"] if fila else None
        # El JSONB vuelve como texto si el driver no lo deserializa.
        if isinstance(valor, str):
            try:
                valor = json.loads(valor)
            except (TypeError, ValueError):
                pass

        lista_opciones = opciones.get(clave, [])
        resultado.append({
            **definicion,
            "valor": valor,
            "medido_at": fila["medido_at"] if fila else None,
            "opciones": lista_opciones,
            "opciones_validas": (valor in [o["codigo"] for o in lista_opciones])
            if lista_opciones else None,
        })

    return resultado


def guardar(
    device_id: int,
    valores: dict[str, Any],
    medido_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Guarda los atributos de un equipo.

    Los que vienen en `valores` se guardan; los que no vienen, se quedan
    como estaban (guardar es parcial: el operador carga la RAM y no tiene
    que volver a cargar el procesador).

    Y un atributo con valor `None` se BORRA: es la forma de decir "este
    equipo no tiene ese dato" (el que se dio de baja, el que no aplica).
    """
    motor = _motor()
    with motor.connect() as conexion:
        equipo = conexion.execute(
            text(
                """
                SELECT d.id, m.category_id
                FROM devices d
                JOIN models m ON m.id = d.model_id
                WHERE d.id = :device_id AND d.deleted_at IS NULL
                """
            ),
            {"device_id": device_id},
        ).mappings().first()

        if equipo is None:
            raise EquipoNoEncontrado(f"El equipo {device_id} no existe.")

        definiciones = _definiciones_de(equipo["category_id"])
        opciones = _opciones_de(equipo["category_id"])

        # Se validan TODOS antes de escribir: si uno esta mal, no se guarda
        # ninguno (un guardado a medias deja la ficha incoherentente).
        validados: dict[str, Any] = {}
        for clave, valor in valores.items():
            validados[clave] = _validar(clave, valor, definiciones, opciones)

        for clave, valor in validados.items():
            if valor is None:
                conexion.execute(
                    text("DELETE FROM device_attributes WHERE device_id = :d AND key = :k"),
                    {"d": device_id, "k": clave},
                )
                continue

            conexion.execute(
                text(
                    """
                    INSERT INTO device_attributes (device_id, key, valor, medido_at, updated_at)
                    VALUES (:device_id, :clave, CAST(:valor AS JSONB), :medido_at, NOW())
                    ON CONFLICT (device_id, key) DO UPDATE
                    SET valor = EXCLUDED.valor,
                        medido_at = EXCLUDED.medido_at,
                        updated_at = NOW()
                    """
                ),
                {
                    "device_id": device_id,
                    "clave": clave,
                    "valor": json.dumps(valor, ensure_ascii=False, default=str),
                    "medido_at": medido_at or datetime.now(),
                },
            )

        conexion.commit()

    return atributos_de(device_id)


def buscar_por_atributo(clave: str, valor: Any) -> list[dict[str, Any]]:
    """
    Los equipos que tienen un atributo con ese valor.

    Es la pregunta que justifica la tabla: "todas las camaras con la IP
    10.0.0.5" o "los equipos con 8 GB de RAM".
    """
    motor = _motor()
    with motor.connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT d.id AS device_id,
                       d.serial_number,
                       d.qr_code,
                       m.brand,
                       m.model,
                       da.valor
                FROM device_attributes da
                JOIN devices d ON d.id = da.device_id
                JOIN models m ON m.id = d.model_id
                WHERE da.key = :clave
                  AND da.valor = CAST(:valor AS JSONB)
                  AND d.deleted_at IS NULL
                ORDER BY d.serial_number
                LIMIT 500
                """
            ),
            {"clave": clave, "valor": json.dumps(valor, ensure_ascii=False, default=str)},
        ).mappings().all()

    return [dict(f) for f in filas]
