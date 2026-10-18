"""
Distancia geodesica: "¿este escaneo ocurrio en la sede?"

POR QUE ESTO ESTA ACa Y NO EN EL ROUTER
---------------------------------------
Porque `is_valid_location` es una REGLA de negocio, no una validacion de
entrada. Si viviera en el router, el proximo endpoint que registre un
movimiento se olvida de llamarla, y el campo queda siempre en TRUE: que es
exactamente el valor que hace que la columna parezca funcionar.

QUE CALCULA Y POR QUE NO UNA DISTANCIA PLANA
--------------------------------------------
La distancia euclidiana sobre (latitud, longitud) NO es una distancia: un grado
de longitud son 111 km cerca del ecuador y 0 km en el polo. Laformula de haversine
si lo es.

Y la alternativa "plana" es peor de lo que parece: entre Buenos Aires y
Rosario, un error de un grado de latitud te deja un margen de error de 111 km
en el eje vertical. El radio de 50 m de la sede es de un orden de magnitud mas
chico que ese error. O sea, la version "rapida" decide al azar, y decidir al
azar es lo que hace que un campo de geolocalizacion no sirva para nada.

POR QUE EARTH_RADIUS Y NO 6371000
---------------------------------
Es el radio medio de la Tierra. El error entre el radio polar (6357 km) y el
ecuatorial (6378 km) es de 0.3%, o sea 15 metros en un radio de 5 km. Para un
radio de 50 m, el error es de 15 cm. Irrelevante para decidir si estabas en el
deposito o en la calle.

POR QUE SE COMPARA CONTRA EL RADIO DE LA SEDE Y NO CONTRA UN 50 FIJO
---------------------------------------------------------------------
`locations.tolerance_radius_meters` existe justamente para esto. Un galpon de
300 metros y una oficina de dos pisos no pueden compartir el mismo radio, y en
un caso el radio bien puesto es lo que hace util el dato: sin el, todo se
registra como "fuera de radio" y la columna no informa nada.
"""

from __future__ import annotations

import math

# Radio medio terrestre en metros.
TIERRA_RADIO_METROS = 6_371_000.0

class CoordenadaInvalida(ValueError):
    """La latitud o la longitud no existe como punto en el planeta."""


def normalizar_coordenada(latitude: float, longitude: float) -> tuple[float, float]:
    """
    Valida y normaliza un par de coordenadas.

    Devuelve un tuple en el ORDEN que espera la columna: (latitud, longitud).
    Se normaliza y no se pasa tal cual porque un cliente que mande `90.000001`
    tiene que recibir el mismo error que uno que mando `NaN`, y no uno que se
    guarda y aparece como un punto imposible en todos los reportes.

    Las latitudes van de -90 a 90 porque el ecuador es el centro del mundo y de
    polo a polo hay 180 grados. La longitud va de -180 a 180: se puede ir a los
    dos lados del meridiano de Greenwich, y 200 no es "el otro lado", es una
    coordenada rota.
    """
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError) as exc:
        raise CoordenadaInvalida(
            "Las coordenadas tienen que ser numeros."
        ) from exc

    # NaN e infinito. `math.isfinite` es lo que los detecta: comparar NaN con
    # cualquier cosa da False, asi que un `if lat < -90 or lat > 90` deja pasar
    # NaN sin quejarse, y un NaN en la columna se propaga a todos los calculos
    # de distancia como si fuera cero.
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise CoordenadaInvalida(
            "Las coordenadas tienen que ser numeros finitos."
        )

    if not -90.0 <= lat <= 90.0:
        raise CoordenadaInvalida(
            f"La latitud {lat} esta fuera de rango: va de -90 a 90."
        )

    if not -180.0 <= lon <= 180.0:
        raise CoordenadaInvalida(
            f"La longitud {lon} esta fuera de rango: va de -180 a 180."
        )

    return lat, lon


def distancia_metros(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """
    Distancia en metros entre dos puntos de la superficie terrestre.

    Haversine. La razon de que sea esta y no el teorema del coseno: el coseno
    pierde precision justamente en distancias cortas, que es el caso de uso
    aqui (un radio de 50 m). Haversine usa el seno, que en angulos chicos es
    preciso, y por eso sirve para medir un galpon y no solo para medir un pais.
    """
    lat1, lon1 = normalizar_coordenada(lat1, lon1)
    lat2, lon2 = normalizar_coordenada(lat2, lon2)

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    # `min(1.0, a)` por precision de punto flotante: `sin` puede devolver
    # 1.0000000000000002, y `sqrt(1.0000000000000002)` es NaN. A radios de
    # kilometers es irrelevante; a radios de decimetros no lo es, porque
    # aparece justo en el caso borde que uno esta testeando.
    c = 2 * math.atan2(math.sqrt(min(1.0, a)), math.sqrt(max(0.0, 1 - a)))

    return TIERRA_RADIO_METROS * c


def dentro_del_radio(
    lat_escaneo: float,
    lon_escaneo: float,
    lat_sede: float,
    lon_sede: float,
    radio_metros: float,
) -> tuple[bool, float]:
    """
    Devuelve (dentro, distancia_en_metros).

    Un tupla y no un bool porque el operador que escanea fuera de radio
    necesita saber CUANTO le falta, no solo que fallo: "a 340 m, probably otra
    sede" es accionable, "escaneo invalido" no.
    """
    metros = distancia_metros(lat_escaneo, lon_escaneo, lat_sede, lon_sede)
    # `radio_metros` puede venir NULL de una sede created antes de que la
    # columna existiera. NULL significaba "sin limite", y por eso se traduce a
    # infinito en vez de a 0: con 0, ninguna sede antiga aceptaria un escaneo.
    limite = float(radio_metros) if radio_metros is not None else math.inf
    return metros <= limite, round(metros, 1)
