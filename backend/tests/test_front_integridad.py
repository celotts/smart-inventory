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


def _claves_de_crear(texto: str) -> set[str]:
    """
    Las claves de PRIMER NIVEL de cada llamada a `crear(...)`.

    Se parsea el objeto de opciones contando llaves, corchetes y parentesis:
    sin los tres, una llamada anidada dentro de `hijos: [...]` (por ejemplo
    `icono(ICONOS.camara, { tamano: 20 })`) se cuenta como clave de la
    llamada de `crear`, y el test reporta un problema que no existe.
    """
    claves: set[str] = set()
    posicion = 0

    while True:
        inicio = texto.find("crear(", posicion)
        if inicio == -1:
            break
        posicion = inicio + 6

        llave = texto.find("{", inicio)
        if llave == -1:
            continue

        # Se busca el cierre del objeto contando los tres tipos de apertura.
        profundidad = 0
        fin = -1
        for i in range(llave, len(texto)):
            caracter = texto[i]
            if caracter in "{[(":
                profundidad += 1
            elif caracter in "}])":
                profundidad -= 1
                if profundidad == 0:
                    fin = i
                    break
        if fin == -1:
            continue

        cuerpo = texto[llave + 1 : fin]

        # Se recorre el cuerpo contando la profundidad de cada tipo.
        nivel_llaves = 0
        nivel_corchetes = 0
        nivel_parentesis = 0
        clave_actual = None
        i = 0
        while i < len(cuerpo):
            caracter = cuerpo[i]
            if caracter == "{":
                nivel_llaves += 1
            elif caracter == "}":
                nivel_llaves -= 1
            elif caracter == "[":
                nivel_corchetes += 1
            elif caracter == "]":
                nivel_corchetes -= 1
            elif caracter == "(":
                nivel_parentesis += 1
            elif caracter == ")":
                nivel_parentesis -= 1
            elif caracter in "'\"`":
                # Los literales se saltan enteros: un `estilo: 'margin-top:0'`
                # tiene dos puntos adentro y no es una clave.
                cierre = cuerpo.find(caracter, i + 1)
                if cierre == -1:
                    break
                i = cierre
            elif (
                clave_actual is None
                and nivel_llaves == 0
                and nivel_corchetes == 0
                and nivel_parentesis == 0
            ):
                m = re.match(r"\s*([a-zA-Z_$][\w$]*)\s*:", cuerpo[i:])
                if m:
                    clave_actual = m.group(1)
                    i += m.end() - 1
            elif clave_actual is not None and ":" in cuerpo[i : i + 2]:
                claves.add(clave_actual)
                clave_actual = None
            i += 1

    return claves


class TestCabecera:
    """
    La cabecera no se puede mover.

    QUE PASÓ
    ---------
    La cabecera era un `flex` con `margin-inline: auto` en el menu: el menu se
    centraba repartiendo el espacio sobrante. Al cambiar de modulo cambian
    las pestanas, cambia el ancho del menu, y con el se movia TODO lo que
    estaba alrededor: el selector de modulo (que es la unica cosa que
    cambia de verdad) salia de un lado y volvia al otro.

    El operador veia el menu y las pestanas cambiar, y el selector de modulo
    moverse. Se lee como "la app se rompió".

    QUE CHEQUEA
    -----------
    1. Que la cabecera sea una GRID con areas: es lo que fija las columnas.
    2. Que el menu NO se centre con margenes automaticos: eso es lo que
       hace que todo se mueva cuando el menu cambia de ancho.
    3. Que el selector de modulo tenga area propia: asi puede bajar a su
       propia fila en el celular sin depender de donde este en el HTML.
    """

    def _css(self) -> str:
        raiz = _raiz_del_front()
        for ruta in (raiz / "styles.css", raiz.parent / "styles.css"):
            if ruta.exists():
                return ruta.read_text(encoding="utf-8")
        pytest.skip("No se encuentra styles.css.")

    def test_la_cabecera_es_una_grid_con_areas(self):
        css = self._css()

        bloque = re.search(r"\.cabecera\s*\{[^}]*\}", css, re.S)
        assert bloque, "No se encuentra la regla de la cabecera."

        regla = bloque.group(0)
        assert "display: grid" in regla, "La cabecera tiene que ser una grid."
        assert "grid-template-areas" in regla, (
            "La cabecera tiene que tener areas: sin ellas, el contenido se "
            "acomoda solo y se mueve cuando cambia el ancho del menu."
        )

    def test_el_menu_no_se_centra_con_margenes_automaticos(self):
        css = self._css()

        bloque = re.search(r"\.cabecera__nav\s*\{[^}]*\}", css, re.S)
        assert bloque, "No se encuentra la regla del menu."

        regla = bloque.group(0)
        assert "margin-inline: auto" not in regla, (
            "El menu no se puede centrar con margenes automaticos: al cambiar "
            "de modulo cambia su ancho, y con el se mueve toda la cabecera."
        )
        assert "grid-area: nav" in regla, "El menu tiene que tener su area."

    def test_el_selector_de_modulo_tiene_area_propia(self):
        css = self._css()

        bloque = re.search(r"\.cabecera__modulos\s*\{[^}]*\}", css, re.S)
        assert bloque, "No se encuentra la regla del selector de modulo."
        assert "grid-area: modulos" in bloque.group(0), (
            "El selector de modulo tiene que tener area propia: es lo que "
            "permite que baje a su propia fila en el celular."
        )


class TestModales:
    """
    Un modal sin boton de cerrar es un modal TRABADO.

    QUE PASÓ
    ---------
    Cinco formularios (reportar incidencia, nuevo usuario, contrasena, mi
    cuenta, nueva categoria) se abrian con `acciones: []`: sin ningun boton
    visible. La unica salida era la tecla Escape, y en un CELULAR no hay
    tecla Escape: el operador abria el formulario y no podia volver.

    No habia ningun error, ninguna peticion fallida, nada en la consola. Solo
    un boton que no estaba.

    QUE CHEQUEA
    -----------
    1. Que ninguna llamada a `abrirDialogo` se quede sin acciones.
    2. Que todo modal con un formulario tenga, ademas, la opcion de cerrar
       tocando el fondo (eso lo garantiza `dialogo.js`).
    """

    def _archivos_con_dialogo(self) -> list[Path]:
        archivos = []
        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")
            if "abrirDialogo" in texto:
                archivos.append(ruta)
        return archivos

    def test_ningun_modal_se_abre_sin_botones(self):
        problemas = []

        for ruta in self._archivos_con_dialogo():
            texto = ruta.read_text(encoding="utf-8")
            for m in re.finditer(r"abrirDialogo\(\{", texto):
                # Se toma el bloque de opciones: hasta el cierre de la llave
                # del objeto de opciones.
                i = m.end() - 1
                profundidad = 0
                fin = len(texto)
                for j in range(i, len(texto)):
                    if texto[j] == "{":
                        profundidad += 1
                    elif texto[j] == "}":
                        profundidad -= 1
                        if profundidad == 0:
                            fin = j
                            break
                bloque = texto[i:fin]
                if re.search(r"acciones:\s*\[\s*\]", bloque):
                    linea = texto[: m.start()].count("\n") + 1
                    problemas.append(
                        f"{ruta.name}:{linea}: el modal se abre sin ningun boton "
                        "(acciones vacias): en un celular no hay Escape para salir."
                    )

        assert not problemas, "\n".join(problemas)


class TestCamposDeFormulario:
    """
    Los helpers de formulario aceptan dos formas de llamada.

    `campoTexto` se llama de las dos formas en el codigo: con un objeto
    (`campoTexto({ etiqueta: 'Marca' })`) y posicional
    (`campoTexto('Ej: Dell', 'Marca')`).

    Cuando la firma era solo la de objeto, las llamadas posicionales
    pasaban un TEXTO donde se esperaba un objeto: la destructuracion daba
    `undefined` para todo, y el campo salia con la etiqueta "undefined".
    El formulario se veia roto sin decir por que.
    """

    def test_las_llamadas_a_campo_texto_pasan_lo_correcto(self):
        problemas = []

        for ruta in _archivos_js():
            # Sin comentarios: un ejemplo en un docstring no es una llamada.
            texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
            for m in re.finditer(r"(?<!function )campoTexto\(", texto):
                # El primer argumento: tiene que ser un objeto o un texto.
                i = m.end()
                while i < len(texto) and texto[i] in " \n":
                    i += 1
                if i >= len(texto):
                    continue
                primero = texto[i]
                if primero not in "{'" and primero != "`":
                    linea = texto[: m.start()].count("\n") + 1
                    problemas.append(
                        f"{ruta.name}:{linea}: `campoTexto()` con un primer "
                        f"argumento que no es ni objeto ni texto: {primero!r}"
                    )

        assert not problemas, "\n".join(problemas)

    def test_las_etiquetas_de_los_campos_no_son_undefined(self):
        """
        Una etiqueta "undefined" es la firma de una llamada con la forma
        equivocada: el helper recibio un string donde esperaba un objeto.
        """
        problemas = []

        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")
            # Busqueda literal: si el codigo tiene "undefined" pegado a una
            # etiqueta de campo, esta mal.
            for m in re.finditer(r"etiqueta:\s*['\"]undefined['\"]", texto):
                linea = texto[: m.start()].count("\n") + 1
                problemas.append(f"{ruta.name}:{linea}: una etiqueta de campo es 'undefined'")

        assert not problemas, "\n".join(problemas)


class TestCss:
    """
    El CSS es codigo, y tiene una clase de bug que no da error: una regla
    MUERTA.

    QUE PASO
    ---------
    Un comentario de cabecera quedo sin cerrar (al insertar un bloque antes
    del comentario, se llevo su linea de cierre). Todo lo que venia
    despues quedo DENTRO del comentario: la regla `.kpi` dejo de existir.

    El sintoma no fue un error: fue un diseno roto. Los cuadros de resumen
    del tablero dejaron de tener fondo y borde, y el navegador los pinto con
    su aspecto NATIVO de boton (gris, con borde de 3D). La app no daba
    ningun error, la consola no decia nada, y el unico sintoma era que la
    pantalla se veia fea.

    POR QUE UN TEST Y NO UN LINTER
    -------------------------------
    Porque el linter (si lo hubiera) avisaria del comentario sin cerrar, que
    es el error de ESCRITURA; el test de abajo detecta el efecto: reglas
    muertas y contenedores sin cerrar. Lo primero se evita escribiendo bien;
    lo segundo se detecta solo.
    """

    def _css(self) -> str:
        # El CSS puede estar en la raiz del front o dentro de `src`: se
        # buscan los dos y no se assume ninguna de las dos.
        raiz = _raiz_del_front()
        for ruta in (raiz / "styles.css", raiz.parent / "styles.css"):
            if ruta.exists():
                return ruta.read_text(encoding="utf-8")
        pytest.skip("No se encuentra styles.css.")

    def test_los_comentarios_estan_cerrados(self):
        """
        Un `/*` sin su `*/` se traga todo lo que viene despues: las reglas
        que quedan dentro del comentario no existen, y el navegador no dice
        nada.
        """
        texto = self._css()

        abiertos = texto.count("/*")
        cerrados = texto.count("*/")

        assert abiertos == cerrados, (
            f"styles.css tiene {abiertos} comentarios abiertos y {cerrados} cerrados: "
            "las reglas entre un '/*' sin cerrar y el siguiente '*/' estan MUERTAS."
        )

    def test_las_llaves_estan_balanceadas(self):
        """
        Una llave de mas deja todo lo que sigue dentro de la regla anterior;
        una de menos rompe el archivo entero.
        """
        texto = self._css()
        # Los comentarios se borran primero: un `{` dentro de un comentario no
        # cuenta (y un `/*` puede tener un `*/` adentro de un ejemplo).
        sin_comentarios = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)

        profundidad = 0
        for i, linea in enumerate(sin_comentarios.split("\n"), 1):
            profundidad += linea.count("{") - linea.count("}")
            assert profundidad >= 0, f"styles.css: sobra un '}}' en la linea {i}"

        assert profundidad == 0, (
            f"styles.css: faltan {profundidad} llaves de cierre: las reglas "
            "del final del archivo estan dentro de otra regla."
        )

    def test_las_reglas_de_los_kpis_existen(self):
        """
        Las reglas que dan el diseno de los cuadros de resumen tienen que
        estar en el archivo. Si una se traga un comentario, el tablero se
        ve con el estilo NATIVO del boton: gris, con borde de 3D, y nadie
        sabe por que.
        """
        texto = self._css()

        for regla in (
            ".kpi {",
            ".kpi--destacado {",
            ".kpi__valor {",
            ".kpi--accion {",
        ):
            assert regla in texto, f"styles.css: falta la regla '{regla}'"


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

    def test_las_claves_de_crear_estan_permitidas(self):
        """
        `crear()` descarta en SILENCIO las claves que no conoce.

        El helper acepta `clase`, `id`, `texto`, `atributos`, `hijos` y
        `estilo`, y cualquier otra cosa se pierde sin error ni aviso. Un `role: 'tab'`
        puesto al lado de `atributos` desaparece, y el boton queda sin
        anunciar: el lector de pantalla lo lee como un boton cualquiera.

        Pasa tres veces en este repo (un `onclick` que no se escuchaba, un
        `errorDe` sin importar y un `role` perdido). Cada vez el sintoma fue
        "el boton no hace nada".
        """
        PERMITIDAS = {"clase", "id", "texto", "atributos", "hijos", "estilo"}
        problemas = []

        for ruta in _archivos_js():
            texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
            for clave in _claves_de_crear(texto):
                if clave not in PERMITIDAS:
                    problemas.append(
                        f"{ruta.name}: `crear()` con la clave '{clave}', que se descarta en silencio"
                    )

        unicos = sorted(set(problemas))
        assert not unicos, "\n".join(unicos)

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
