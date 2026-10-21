"""
Generación del QR de la etiqueta de un equipo.

QUE ES Y POR QUE Está EN EL BACKEND
------------------------------------
El QR es una imagen, y la app no tiene ni una dependencia de frontend (el
`index.html` no carga un solo CDN: cero cadena de suministro que auditar). Meter
un generador de QR en el JS seria agregar hundreds de líneas de un algoritmo
que hay que mantener, o un CDN, que es justo lo que la app no hace.

En el backend la dependencia es `qrcode`, que es Python puro: no compila
nada, no agrega un binario al contenedor y pesa 46 KB.

POR QUE SVG Y NO PNG
--------------------
El SVG son unos pocos KB de texto y se ve nitido en cualquier pantalla, sin
pixeles. Un PNG de 300x300 sale de 2 a 5 KB en base64, y para escalarlo hay que
subir la resolucion. Para una etiqueta que se mira de cerca o se imprime, el
SVG gana.

Tamaño Y CONTRASTE
------------------
El `box_size` de 8 y el borde de 2 dan un QR de 33 módulos (versión 3) que se
lee bien a pantalla. El negro es negro puro: los lectores de códigos de barras
miden el CONTRASTE, y un gris oscuro sobre blanco baja el margen de error.
"""

from __future__ import annotations

import io

# Box size y borde: con un texto corto (el código de la etiqueta) alcanza la
# versión 2 o 3 del QR. El `border` de 2 es la zona de silencio que el
# estándar exige: sin ella, el lector no arranca.
BOX_SIZE = 8
BORDER = 2


def qr_como_svg(texto: str) -> bytes:
    """
    Devuelve el QR de `texto` como SVG.

    Nunca lanza: si algo falla, devuelve un SVG con un mensaje. Un endpoint
    que rompe la ficha del equipo es peor que uno que muestra un cartel.
    """
    try:
        # El import va ADENTRO a propósito: `qrcode` es una dependencia Más de
        # este módulo, y si falta —un entorno de desarrollo sin instalarla— lo
        # que tiene que fallar es el QR, no el import de toda la API. Con el
        # import arriba del archivo, CUALQUIER endpoint devuelve 500 si la
        # libreria no está.
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(
            texto or "",
            image_factory=qrcode.image.svg.SvgPathImage,
            box_size=BOX_SIZE,
            border=BORDER,
        )
        buffer = io.BytesIO()
        img.save(buffer)
        return buffer.getvalue()
    except Exception:  # pragma: no cover - defensivo
        return (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60">'
            b'<rect width="200" height="60" fill="#f5f5f5"/>'
            b'<text x="100" y="35" text-anchor="middle" font-size="12" '
            b'fill="#666">No se pudo generar el QR</text></svg>'
        )
