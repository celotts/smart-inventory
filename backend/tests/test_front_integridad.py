"""
Tests de integridad del front.

POR QUE UN TEST EN EL BACKEND PARA EL FRONT
--------------------------------------------
El front no tiene runner de tests (ni bundler, por decision del proyecto: los
modulos ES se cargan nativos). Pero hay una clase de bug DEL FRONT que es
silenciosa y cara:

    Un modulo usa un helper compartido sin importarlo.

`ReferenceError: errorDe is not defined` dentro de un manejador de click. El
modulo CARGA bien (el error es en tiempo de ejecucion), la app arranca, la
vista se ve... y el boton no hace nada. Sin errores en la consola de la
pagina que el operador tiene abierta, y sin un mensaje util: el unico rastro
esta en la consola del navegador, que el operador no abre.

COSTE REAL
----------
Ese bug se colo DOS VECES en un mismo refactor, y las dos veces el sintoma
fue "el boton no hace nada". Un test que verifique los imports lo detecta en un
segundo.

QUE CHEQUEA
------------
1. Que cada `import { x }` exista de verdad en el modulo destino (un export
   que no existe rompe el grafo de modulos ENTERO: la app no arranca).
2. Que los helpers COMPARTIDOS que el archivo usa esten importados o definidos
   en el.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Donde puede estar el front.
#
# En el HOST, el test esta en `backend/tests/`, asi que el front esta dos
# directorios mas arriba. En el CONTENEDOR, el codigo esta en `/app` y el front
# se monta en `/app/frontend-src` (ver `docker-compose.dev.yml`).
RUTAS_FRONT = (
    Path(__file__).resolve().parents[2] / "frontend" / "src",
    Path("/app/frontend-src"),
)


def _raiz_del_front() -> Path:
    for ruta in RUTAS_FRONT:
        if ruta.exists():
            return ruta
    return RUTAS_FRONT[0]

# Los helpers compartidos del front. Un nombre de esta lista que se use sin
# estar importado es un bug garantizado.
HELPERS_COMPARTIDOS = {
    # De `dom.js`
    "crear", "reemplazar", "icono", "etiquetaProducto",
    # De `avisos.js`
    "avisar", "marcarPendientes",
    # De `dialogo.js`
    "abrirDialogo", "confirmar",
    # De `formulario.js`
    "campoTexto", "campoSelect", "campoNumero", "errorDe", "resumenDeErrores",
    # De `tooltip.js`
    "conTooltip",
    # De `etiqueta.js`
    "imagenQrProducto", "imagenBarcodeProducto", "svgsDeEtiqueta",
    # De `qr.js`
    "imagenQr",
    # De `escaner.js`
    "LectorQR", "normalizarCodigo",
    # De `escaner-campo.js`
    "botonEscanear",
    # De `descargas.js`
    "descargarCsv",
    # De `api.js`
    "api", "configurar", "restaurarSesion", "haySesion", "consumirOperacionPendiente",
    # De `estado.js` y `navegacion.js`
    "estado", "setUsuario", "permisosDe", "mostrarVista", "VISTAS",
    # De `cabecera.js`
    "actualizarCabeceraUsuario", "actualizarEstadoIA", "inicializarFooter", "iniciarModoExterior",
    # De `campana.js`
    "iniciarCampana",
    # De `build.js`
    "obtenerSelloDelServidor", "verificarVersionDelServidor",
    # De las vistas de producto
    "abrirAlta", "abrirEdicion", "abrirCategorias", "abrirDetalleDe",
    "etiquetaConDescripcion", "pintarAtributos", "leerAtributos",
}


def _archivos_js() -> list[Path]:
    raiz = _raiz_del_front()
    if not raiz.exists():  # pragma: no cover
        pytest.skip(
            "No se encuentra el front. Se prueba desde el host, o con el "
            "front montado en /app/frontend-src (docker-compose.dev.yml)."
        )
    return sorted(raiz.rglob("*.js"))


def _sin_comentarios(texto: str) -> str:
    """
    El texto sin los comentarios.

    Un ejemplo de uso en un docstring (`import { x } from './y'`) NO es un
    import: sin esto, el chequeo reporta como faltante algo que el modulo si
    exporta.
    """
    texto = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
    return re.sub(r"//.*", "", texto)


def _exportados(ruta: Path) -> set[str]:
    """Los nombres que el modulo exporta."""
    texto = ruta.read_text(encoding="utf-8")
    nombres: set[str] = set()

    nombres |= set(re.findall(r"export\s+(?:async\s+)?function\s+(\w+)", texto))
    nombres |= set(re.findall(r"export\s+(?:const|let|var|class)\s+(\w+)", texto))
    for bloque in re.findall(r"export\s*\{([^}]*)\}", texto):
        for parte in bloque.split(","):
            nombre = parte.strip().split(" as ")[0].strip()
            if nombre:
                nombres.add(nombre)
    return nombres


def _locales(texto: str) -> set[str]:
    """Los nombres definidos O importados en el archivo."""
    nombres: set[str] = set()

    for bloque in re.findall(r"import\s*\{([^}]*)\}", texto):
        for parte in bloque.split(","):
            nombre = parte.strip().split(" as ")[0].strip()
            if nombre:
                nombres.add(nombre)
    nombres |= set(re.findall(r"(?:async\s+)?function\s+(\w+)", texto))
    nombres |= set(re.findall(r"(?:const|let|var)\s+(\w+)", texto))
    nombres |= set(re.findall(r"class\s+(\w+)", texto))
    # Los imports dinamicos definen los nombres en su ambito:
    #   import('./x.js').then(({ a, b }) => ...)
    #   const { c, d } = await import('./y.js');
    for patron in (
        r"\.then\(\(\{([^}]*)\}\)",
        r"const\s*\{([^}]*)\}\s*=\s*await\s+import\(",
    ):
        for bloque in re.findall(patron, texto):
            for parte in bloque.split(","):
                nombre = parte.strip().split(":")[0].strip()
                if nombre:
                    nombres.add(nombre)
    return nombres


class TestGrafoDeModulos:
    def test_todo_import_existe(self):
        """
        Un import de un nombre que el modulo destino no exporta ROMPE el grafo
        de modulos entero: la app no arranca, y el error dice "Failed to load
        module script" sin decir cual.
        """
        problemas = []

        for ruta in _archivos_js():
            texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
            for nombres, modulo in re.findall(
                r"import\s*\{([^}]*)\}\s*from\s*'([^']+)'", texto
            ):
                if not modulo.startswith("."):
                    continue

                destino = (ruta.parent / modulo).resolve()
                if destino.is_dir():
                    destino = destino / "index.js"
                elif not destino.exists() and destino.with_suffix(".js").exists():
                    destino = destino.with_suffix(".js")

                if not destino.exists():
                    problemas.append(f"{ruta.name}: el modulo '{modulo}' no existe")
                    continue

                exportados = _exportados(destino)
                for parte in nombres.split(","):
                    nombre = parte.strip().split(" as ")[0].strip()
                    if nombre and nombre not in exportados:
                        problemas.append(
                            f"{ruta.name}: importa '{nombre}' de '{modulo}', que no lo exporta"
                        )

        assert not problemas, "\n".join(problemas)

    def test_los_helpers_compartidos_estan_importados(self):
        """
        Un helper compartido usado sin importar es un `ReferenceError` en
        tiempo de ejecucion: el boton no hace nada y no hay error en la
        consola de la pagina.

        El chequeo mira los nombres usados COMO LLAMADA (`nombre(`), asi que no
        marca los que aparecen en un comentario o en un texto.
        """
        problemas = []

        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")

            sin_comentarios = _sin_comentarios(texto)

            locales = _locales(sin_comentarios)
            usados = set(re.findall(r"(?<![\w.$])([a-zA-Z_$][\w$]*)\s*\(", sin_comentarios))

            for nombre in usados & HELPERS_COMPARTIDOS:
                if nombre not in locales:
                    problemas.append(f"{ruta.name}: usa '{nombre}' y no lo importa")

        assert not problemas, "\n".join(problemas)

    def test_no_hay_imports_de_ruta_absoluta(self):
        """
        Un import con ruta absoluta (`/src/...`) no lo resuelve el navegador:
        el modulo no se carga y el error dice "Failed to resolve module specifier".
        """
        problemas = []

        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")
            for modulo in re.findall(r"from\s+'([^']+)'", texto):
                if modulo.startswith("/") and not modulo.startswith("//"):
                    problemas.append(f"{ruta.name}: import con ruta absoluta '{modulo}'")

        assert not problemas, "\n".join(problemas)
