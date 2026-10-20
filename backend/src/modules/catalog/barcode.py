"""
Codigo de barras (Code 128) de la etiqueta de un producto, en SVG.

POR QUE CODE 128 Y NO EAN-13
----------------------------
El codigo de barras de un producto NO es el de una góndola de supermercado: no
hay un organismo que asigne el numero ni un codigo de barras que "pague" en la
caja. El codigo de barras de un producto es un IDENTIFICADOR de ese producto
dentro de este inventario: el `barcode` que se imprime en la etiqueta y que se
escanea con la app para llegar al producto.

El Code 128 es el que se usa para eso: codifica cualquier texto ASCII (letras
y digitos), es un estandar de la industria, y lo leen TODOS los lectores
lineales, desde los scanners de mano hasta el lector del celular.

POR QUE ESTA ESCRITO ACA Y NO CON UNA LIBRERIA
-----------------------------------------------
El proyecto no quiere dependencias que compilen ni que agregan binarios al
contenedor (ver el docstring de `src/modules/devices/qr.py`): la imagen tiene
que ser texto. El algoritmo del Code 128 son cien lineas y una tabla de
amplitudes: es exactamente el tipo de cosa que conviene tener a la vista y no
dentro de un `.whl` de 300 KB con Pillow adentro.

POR QUE SVG
----------
Es un dibujo de barras y espacios: un vector de unos pocos KB, que se ve
nitido a cualquier tamaño y que se imprime sin perdida. Un PNG de 600x200
sale de 2 a 5 KB en base64, pero se ve borroso al imprimir en una etiqueta
grande.

LA TABLA
--------
Un simbolo del Code 128 son tres barras y tres espacios, con anchos de 1 a 4
modulos, y el conjunto de los seis anchos suma siempre 11. La tabla de
abajo es la del estandar (ISO/IEC 15417): un valor por simbolo, con sus seis
digitos de ancho en el orden barra, espacio, barra, espacio, barra, espacio.

Los valores 0..102 son datos, 103/104/105 son los arranques A/B/C y 106 es el
fin. Se usa solo el set B (arranque 104): codifica de forma directa los
caracteres imprimibles del ASCII, que es todo lo que un SKU o un nombre de
producto puede llegar a ser.
"""

from __future__ import annotations

# Ancho de un modulo, en px. 2 px por modulo es lo que usan las impresoras de
# etiquetas comunes: mas chico no se lee con un scanner de mano.
ANCHO_MODULO = 2

# Alto de la barra, en px. Un codigo de barras de menos de 30 px de alto es
# facil de leer mal con un lector barato.
ALTO_BARRA = 60

# Zona de silencio a la izquierda y a la derecha, en modulos. El estandar
# pide 10: sin ella, el lector no arranca y el codigo "no existe".
ZONA_SILENCIO = 10

# Inicio del set B. Los caracteres imprimibles del ASCII van del 32 (espacio)
# al 126 (~), y en el set B el valor del simbolo es el ASCII menos 32.
INICIO_B = 104
FIN = 106

# La tabla de amplitudes del estandar. Un string de seis digitos por simbolo.
_AMPLITUDES = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213",
    "122312", "132212", "221213", "221312", "231212", "112232", "122132",
    "122231", "113222", "123122", "123221", "223211", "221132", "221231",
    "213212", "223112", "312131", "311222", "321122", "321221", "312212",
    "322112", "322211", "212123", "212321", "232121", "111323", "131123",
    "131321", "112313", "132113", "132311", "211313", "231113", "231311",
    "112133", "112331", "132131", "113123", "113321", "133121", "313121",
    "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114",
    "413111", "241112", "134111", "111242", "121142", "121241", "114212",
    "124112", "124211", "411212", "421112", "421211", "212141", "214121",
    "412121", "111143", "111341", "131141", "114113", "114311", "411113",
    "411311", "113141", "114131", "311141", "411131", "211412", "211214",
    "211232", "233111",
)

# El patron de fin es el simbolo 106 (233111) mas una barra final de 2
# modulos: sin esa barra, el lector no sabe que termino el codigo.
_PATRON_FIN = "2331112"


def _simbolos(texto: str) -> list[int]:
    """
    Devuelve la secuencia de valores de simbolo del Code 128 (set B).

    Incluye el arranque, los datos y el simbolo de chequeo. Lanza
    `ValueError` si el texto tiene un caracter que el set B no codifica.
    """
    valores = [INICIO_B]

    for caracter in texto:
        punto = ord(caracter)
        if not 32 <= punto <= 126:
            raise ValueError(
                f"El caracter {caracter!r} no se puede codificar en Code 128."
            )
        valores.append(punto - 32)

    # Chequeo: suma ponderada modulo 103. El arranque pesa 1 y el primer
    # caracter tambien: por eso el primer `i` es 1 y no 0.
    checksum = INICIO_B
    for posicion, valor in enumerate(valores[1:], start=1):
        checksum += posicion * valor
    valores.append(checksum % 103)

    return valores


def _barras(texto: str) -> tuple[list[tuple[int, int]], int]:
    """
    Devuelve la lista de barras del codigo y el ancho total, en px.

    Cada barra es una tupla `(x, ancho)`: donde arranca y cuanto mide.
    """
    elementos: list[tuple[int, int]] = []
    x = ZONA_SILENCIO * ANCHO_MODULO

    for valor in _simbolos(texto):
        for indice, ancho in enumerate(_AMPLITUDES[valor]):
            # Los elementos pares (0, 2, 4) son barras; los impares, espacios.
            if indice % 2 == 0:
                elementos.append((x, int(ancho) * ANCHO_MODULO))
            x += int(ancho) * ANCHO_MODULO

    for indice, ancho in enumerate(_PATRON_FIN):
        if indice % 2 == 0:
            elementos.append((x, int(ancho) * ANCHO_MODULO))
        x += int(ancho) * ANCHO_MODULO

    # Zona de silencio de la derecha.
    return elementos, x + ZONA_SILENCIO * ANCHO_MODULO


def codigo128_como_svg(texto: str, *, alto: int = ALTO_BARRA) -> bytes:
    """
    Devuelve el codigo de barras de `texto` como SVG.

    Nunca lanza: si el texto tiene un caracter que no se puede codificar,
    devuelve un SVG con un cartel. Un endpoint que rompe la etiqueta de un
    producto es peor que uno que muestra un mensaje.
    """
    try:
        barras, ancho_total = _barras(texto or "")
    except Exception:  # pragma: no cover - defensivo
        return (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="220" height="60">'
            b'<rect width="220" height="60" fill="#f5f5f5"/>'
            b'<text x="110" y="35" text-anchor="middle" font-size="12" '
            b'fill="#666">No se pudo generar el codigo de barras</text></svg>'
        )

    partes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{ancho_total}" '
        f'height="{alto}" viewBox="0 0 {ancho_total} {alto}" '
        f'shape-rendering="crispEdges" role="img">',
        # Fondo blanco explicito: el lector mide el CONTRASTE contra el fondo,
        # y en modo oscuro un fondo transparente puede quedar gris.
        f'<rect width="{ancho_total}" height="{alto}" fill="#ffffff"/>',
    ]

    for x, ancho in barras:
        partes.append(f'<rect x="{x}" y="0" width="{ancho}" height="{alto}" fill="#000000"/>')

    partes.append("</svg>")
    return "".join(partes).encode("utf-8")
